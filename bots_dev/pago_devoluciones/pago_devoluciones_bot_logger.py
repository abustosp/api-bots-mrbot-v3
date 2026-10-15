import asyncio
import logging
import os
import re
import tempfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from playwright.async_api import (
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
BOT_NAME = "pago_devoluciones"
SERVICE_NAME = "PAGO DEVOLUCIONES"
INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")

LOGGER = logging.getLogger(f"{BOT_NAME}_logger")

# --- Debug Capture ---
def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.har_dir = self.run_dir / "har"
        self.descargas_dir = self.run_dir / "descargas"
        for path in (self.logs_dir, self.screenshots_dir, self.html_dir, self.har_dir, self.descargas_dir):
            path.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.logs_dir / f"{prefix}.log")
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            self.logger.propagate = False

        self.step = 0
        self.logger.info("run_dir=%s", self.run_dir)

    def log(self, message: str) -> None:
        self.logger.info("%s", message)

    async def capture(self, page, step: str) -> None:
        if page is None:
            self.logger.info("capture_skipped step=%s page=None", step)
            return

        self.step += 1
        safe_step = _safe_step_name(step)
        prefix = f"{self.step:03d}_{safe_step}"
        self.logger.info("step=%s name=%s url=%s", self.step, step, getattr(page, "url", ""))

        screenshot_path = self.screenshots_dir / f"{prefix}.png"
        html_path = self.html_dir / f"{prefix}.html"

        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
            self.logger.info("screenshot=%s", screenshot_path)
        except Exception:
            self.logger.exception("screenshot_failed step=%s", step)

        try:
            content = await page.content()
            html_path.write_text(content, encoding="utf-8")
            self.logger.info("html=%s", html_path)
        except Exception:
            self.logger.exception("html_failed step=%s", step)

# --- Helpers (Duplicated) ---

def _append_section_error(
    error_logs: List[str],
    section_errors: Dict[str, List[str]],
    section: str,
    message: str,
) -> None:
    clean_message = (message or "Error desconocido").strip()
    if not clean_message:
        clean_message = "Error desconocido"
    section_errors.setdefault(section, []).append(clean_message)
    error_logs.append(f"[{section}] {clean_message}")

def _has_critical_errors(section_errors: Dict[str, List[str]]) -> bool:
    critical_sections = {
        "validacion",
        "proxy",
        "login",
        "servicio",
        "representado",
        "consulta",
        "general",
    }
    return any(section_errors.get(section) for section in critical_sections)

def safe_filename(value: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value or "")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return cleaned or "archivo"

def only_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")

async def _wait_visible(locator: Locator, timeout_ms: int = 2_500) -> bool:
    try:
        await locator.first.wait_for(state="visible", timeout=timeout_ms)
        return True
    except Exception:
        return False

async def _wait_dom_settle(page: Page, timeout_ms: int = 12_000) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        pass
    await page.wait_for_timeout(500)

async def _service_page_ready(page: Page) -> bool:
    probes: Sequence[Locator] = (
        page.get_by_role("link", name=re.compile(r"Consultar", re.I)),
        page.locator("a:has-text('Consultar')"),
        page.locator("text=/Exportar/i"),
    )
    for probe in probes:
        if await _wait_visible(probe, timeout_ms=8_000):
            return True
    return False

async def _click_first_visible(candidates: Sequence[Locator], max_items: int = 8) -> bool:
    for candidate_group in candidates:
        count = await candidate_group.count()
        if count == 0:
            continue
        for idx in range(min(count, max_items)):
            candidate = candidate_group.nth(idx)
            try:
                if not await candidate.is_visible():
                    continue
                await candidate.click(timeout=3_000)
                return True
            except Exception:
                continue
    return False

async def ensure_login(page: Page, cuit_representante: str, clave: str) -> None:
    login_errors: List[str] = []
    login_result = await login_arca(
        page,
        cuit_representante,
        clave,
        error_logs=login_errors,
        login_url=LOGIN_URL,
        fail_on_captcha=True,
    )
    if not login_result.success:
        raise RuntimeError(login_errors[-1] if login_errors else "No se pudo completar el login en ARCA")

async def open_service(page: Page) -> Page:
    service_page = await open_arca_service(
        page,
        service_name=SERVICE_NAME,
        service_matchers=(
            SERVICE_NAME,
            re.compile(r"PAGO\s+DEVOLUCIONES", re.I),
        ),
        service_unavailable_message=f"No se encuentra habilitado el servicio de {SERVICE_NAME}",
    )
    if service_page is None:
        raise RuntimeError(f"No se encuentra habilitado el servicio de {SERVICE_NAME}")

    await _wait_dom_settle(service_page)
    if not await _service_page_ready(service_page):
        raise RuntimeError(f"No se encuentra habilitado el servicio de {SERVICE_NAME}")

    return service_page

async def select_representado(page: Page, cuit_representado: str, debug: DebugCapture = None) -> None:
    cuit_digits = only_digits(cuit_representado)
    if not cuit_digits:
        raise RuntimeError("CUIT_REPRESENTADO invalido")

    company_select = page.locator("#ctl00_ddlExtranetEmpresa, select[name='ctl00$ddlExtranetEmpresa']")
    if await company_select.count() > 0:
        if debug: debug.log("Found company select dropdown")
        select_box = company_select.first
        with suppress(PlaywrightTimeoutError):
            await select_box.wait_for(state="attached", timeout=10_000)

        option_values = await select_box.evaluate(
            """el => Array.from(el.options || []).map(opt => (opt.value || '').trim())"""
        )
        if debug: debug.log(f"Options available: {option_values}")
        
        if cuit_digits not in option_values:
            raise RuntimeError(f"No se pudo seleccionar el representado {cuit_digits}")

        current_value = only_digits(await select_box.input_value())
        if current_value != cuit_digits:
            await select_box.select_option(value=cuit_digits)
            await _wait_dom_settle(page)

        selected_value = only_digits(await select_box.input_value())
        if selected_value != cuit_digits:
            await page.evaluate(
                """({selector, value}) => {
                    const select = document.querySelector(selector);
                    if (!select) {
                        return false;
                    }
                    select.value = value;
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }""",
                {"selector": "#ctl00_ddlExtranetEmpresa", "value": cuit_digits},
            )
            await _wait_dom_settle(page)
            selected_value = only_digits(await select_box.input_value())

        if selected_value != cuit_digits:
            raise RuntimeError(f"No se pudo seleccionar el representado {cuit_digits}")
        return

    if debug: debug.log("Dropdown not found, trying click strategies")
    clicked = await _click_first_visible(
        (
            page.locator(f"[title*='{cuit_digits}']"),
            page.get_by_role("treeitem", name=re.compile(cuit_digits)),
            page.locator(f"li:has-text('{cuit_digits}')"),
            page.locator(f"a:has-text('{cuit_digits}')"),
            page.locator(f"text=/{cuit_digits}/"),
        )
    )
    if not clicked:
        raise RuntimeError(f"No se pudo seleccionar el representado {cuit_digits}")

    tree_exact = page.get_by_role("treeitem", name=re.compile(cuit_digits))
    if await tree_exact.count() > 0:
        await tree_exact.first.click(timeout=3_000)
    await _wait_dom_settle(page)

async def consultar_y_exportar(page: Page, cuit_representado: str, downloads_dir: Path) -> Path:
    await page.get_by_role("link", name=re.compile(r"Consultar", re.I)).first.click()
    with suppress(PlaywrightTimeoutError):
        await page.wait_for_load_state("networkidle", timeout=12_000)

    export_link = page.get_by_role("link", name=re.compile(r"Exportar", re.I))
    if await export_link.count() == 0:
        export_link = page.locator("a:has-text('Exportar')")
    if await export_link.count() == 0:
        raise RuntimeError("No se encontro el boton Exportar")

    async with page.expect_download(timeout=45_000) as download_info:
        await export_link.first.click()
        try:
            aceptar = page.get_by_role("button", name=re.compile(r"Aceptar", re.I))
            await aceptar.first.wait_for(state="visible", timeout=4_000)
            await aceptar.first.click()
        except PlaywrightTimeoutError:
            pass

    download = await download_info.value
    suggested_name = download.suggested_filename or "pago_devoluciones.xlsx"
    extension = Path(suggested_name).suffix or ".xlsx"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_name = safe_filename(
        f"pago_devoluciones_{only_digits(cuit_representado)}_{timestamp}{extension}"
    )
    downloads_dir.mkdir(parents=True, exist_ok=True)
    download_path = downloads_dir / output_name
    await download.save_as(str(download_path))
    return download_path

# --- Main Bot Logic ---

async def bot_pago_devoluciones_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: Optional[str] = None,
    proxy_request: bool = False,
    headless: bool = True,
) -> Tuple[Dict[str, Any], List[str], bool]:
    results: Dict[str, Any] = {
        "archivo": None,
        "archivo_path": None,
        "errores_por_seccion": {},
    }
    error_logs: List[str] = []
    section_errors: Dict[str, List[str]] = {}

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot Pago Devoluciones Logger")

    if not cuit_representante or not clave_representante:
        _append_section_error(
            error_logs,
            section_errors,
            "validacion",
            "Faltan parametros requeridos: cuit_representante y/o clave_representante",
        )
        results["errores_por_seccion"] = section_errors
        return results, error_logs, False

    cuit_representado_value = (cuit_representado or cuit_representante).strip()
    if not cuit_representado_value:
        _append_section_error(
            error_logs,
            section_errors,
            "validacion",
            "El cuit_representado es invalido",
        )
        results["errores_por_seccion"] = section_errors
        return results, error_logs, False

    proxy = None
    if proxy_request:
        try:
            proxy = get_proxy_config()
        except Exception as exc:
            _append_section_error(
                error_logs,
                section_errors,
                "proxy",
                f"No se pudo configurar proxy: {exc}",
            )
            results["errores_por_seccion"] = section_errors
            return results, error_logs, False

    download_path: Optional[Path] = None
    
    # Use debug directories
    download_dir = debug.descargas_dir
    har_path = debug.har_dir / f"{BOT_NAME}.har"

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=headless, proxy=proxy)
            context = await browser.new_context(
                accept_downloads=True,
                record_har_path=har_path,
                record_har_content="embed"
            )
            page = await context.new_page()
            
            await debug.capture(page, "inicio")

            try:
                await ensure_login(page, cuit_representante, clave_representante)
                await debug.capture(page, "post_login")
            except Exception as exc:
                _append_section_error(error_logs, section_errors, "login", str(exc))
                debug.log(f"Login error: {exc}")
                raise

            try:
                service_page = await open_service(page)
                await debug.capture(service_page, "servicio_abierto")
            except Exception as exc:
                _append_section_error(error_logs, section_errors, "servicio", str(exc))
                debug.log(f"Service error: {exc}")
                raise

            try:
                await select_representado(service_page, cuit_representado_value, debug=debug)
                await debug.capture(service_page, "representado_seleccionado")
            except Exception as exc:
                _append_section_error(error_logs, section_errors, "representado", str(exc))
                debug.log(f"Representado error: {exc}")
                raise

            try:
                download_path = await consultar_y_exportar(
                    service_page,
                    cuit_representado_value,
                    download_dir,
                )
                debug.log(f"Downloaded: {download_path}")
            except Exception as exc:
                _append_section_error(error_logs, section_errors, "consulta", str(exc))
                debug.log(f"Consulta error: {exc}")
                raise

            results["archivo"] = download_path.name
            results["archivo_path"] = str(download_path.resolve())
            
            await context.close()
            await browser.close()

    except Exception as exc:
        LOGGER.exception("bot_error_pago_devoluciones")
        if not error_logs:
            _append_section_error(
                error_logs,
                section_errors,
                "general",
                f"Error en bot Pago Devoluciones: {exc}",
            )
            debug.log(f"General error: {exc}")
    
    success = download_path is not None and not _has_critical_errors(section_errors)

    if not success and not error_logs:
        _append_section_error(
            error_logs,
            section_errors,
            "general",
            "No se pudo completar la consulta de Pago Devoluciones",
        )

    results["errores_por_seccion"] = section_errors
    return results, error_logs, success

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    cuit = os.getenv("PAGO_DEVOLUCIONES_CUIT_REPRESENTANTE", "")
    clave = os.getenv("PAGO_DEVOLUCIONES_CLAVE_REPRESENTANTE", "")
    cuit_rep = os.getenv("PAGO_DEVOLUCIONES_CUIT_REPRESENTADO", "")
    
    if cuit and clave:
        res = await bot_pago_devoluciones_logger(cuit, clave, cuit_rep)
        print(res)

if __name__ == "__main__":
    asyncio.run(main())
