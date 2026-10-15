import asyncio
import logging
import os
import re
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from dotenv import load_dotenv
from playwright.async_api import (
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

from app.utils.arca_login import login_arca, open_arca_service
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser

load_dotenv()

LOGIN_URL = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
BOT_NAME = "pago_devoluciones"
SERVICE_NAME = "PAGO DEVOLUCIONES"
INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")

LOGGER = logging.getLogger(BOT_NAME)


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


async def _is_captcha_visible(page: Page) -> bool:
    captcha_candidates: Sequence[Locator] = (
        page.get_by_role("img", name=re.compile(r"captcha", re.I)),
        page.locator("img[alt*='captcha' i], img[src*='captcha' i]"),
        page.locator("iframe[src*='captcha' i]"),
        page.locator("[id*='captcha' i], [class*='captcha' i]"),
    )
    for locator in captcha_candidates:
        if await _wait_visible(locator, timeout_ms=1_500):
            return True
    return False


async def _password_change_required(page: Page) -> bool:
    candidates: Sequence[Locator] = (
        page.get_by_role("heading", name=re.compile(r"Cambiar\s+Clave\s+Fiscal", re.I)),
        page.locator("text=/Cambiar\\s+Clave\\s+Fiscal/i"),
    )
    for locator in candidates:
        if await _wait_visible(locator, timeout_ms=1_500):
            return True
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


async def _wait_services_catalog_ready(page: Page, timeout_ms: int = 30_000) -> None:
    wait_until = datetime.now().timestamp() + (timeout_ms / 1000)
    while datetime.now().timestamp() < wait_until:
        service_probes: Sequence[Locator] = (
            page.get_by_role("button", name=SERVICE_NAME),
            page.get_by_role("button", name=re.compile(r"PAGO\s+DEVOLUCIONES", re.I)),
            page.locator("button:has-text('PAGO DEVOLUCIONES')"),
            page.locator("[role='button']:has-text('PAGO DEVOLUCIONES')"),
            page.locator("a:has-text('PAGO DEVOLUCIONES')"),
        )
        for probe in service_probes:
            if await _wait_visible(probe, timeout_ms=350):
                return

        generic_probes: Sequence[Locator] = (
            page.locator(".panels-row .panel"),
            page.locator(".panels-row button"),
            page.locator(".panels-row a"),
        )
        for probe in generic_probes:
            with suppress(Exception):
                if await probe.count() > 0:
                    return

        await page.wait_for_timeout(500)


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


async def select_representado(page: Page, cuit_representado: str) -> None:
    cuit_digits = only_digits(cuit_representado)
    if not cuit_digits:
        raise RuntimeError("CUIT_REPRESENTADO invalido")

    company_select = page.locator("#ctl00_ddlExtranetEmpresa, select[name='ctl00$ddlExtranetEmpresa']")
    if await company_select.count() > 0:
        select_box = company_select.first
        with suppress(PlaywrightTimeoutError):
            await select_box.wait_for(state="attached", timeout=10_000)

        option_values = await select_box.evaluate(
            """el => Array.from(el.options || []).map(opt => (opt.value || '').trim())"""
        )
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




async def bot_pago_devoluciones(
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

    browser = None
    context = None
    download_path: Optional[Path] = None
    download_dir = DOWNLOAD_DIR

    try:
        async with limit_browser(), async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=headless, proxy=proxy)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()

            try:
                await ensure_login(page, cuit_representante, clave_representante)
            except Exception as exc:
                _append_section_error(error_logs, section_errors, "login", str(exc))
                raise

            try:
                service_page = await open_service(page)
            except Exception as exc:
                _append_section_error(error_logs, section_errors, "servicio", str(exc))
                raise

            try:
                await select_representado(service_page, cuit_representado_value)
            except Exception as exc:
                _append_section_error(error_logs, section_errors, "representado", str(exc))
                raise

            try:
                download_path = await consultar_y_exportar(
                    service_page,
                    cuit_representado_value,
                    download_dir,
                )
            except Exception as exc:
                _append_section_error(error_logs, section_errors, "consulta", str(exc))
                raise

            results["archivo"] = download_path.name
            # Se registra la ruta temporal solo para trazabilidad; el archivo se elimina al finalizar.
            results["archivo_path"] = str(download_path.resolve())

    except Exception as exc:
        LOGGER.exception("bot_error_pago_devoluciones")
        if not error_logs:
            _append_section_error(
                error_logs,
                section_errors,
                "general",
                f"Error en bot Pago Devoluciones: {exc}",
            )
    finally:
        if context is not None:
            with suppress(Exception):
                await context.close()
        if browser is not None:
            with suppress(Exception):
                await browser.close()

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


async def run() -> Tuple[Dict[str, Any], List[str], bool]:
    load_dotenv()
    cuit_representante = os.getenv("CUIT_REPRESENTANTE", "").strip()
    cuit_representado = os.getenv("CUIT_REPRESENTADO", "").strip() or cuit_representante
    clave = os.getenv("CLAVE", "").strip()
    headless = os.getenv("HEADLESS", "True").strip().lower() in {"1", "true", "yes", "y", "on"}

    return await bot_pago_devoluciones(
        cuit_representante=cuit_representante,
        clave_representante=clave,
        cuit_representado=cuit_representado,
        proxy_request=False,
        headless=headless,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    result_data, result_errors, result_success = asyncio.run(run())
    if result_errors:
        for msg in result_errors:
            print(msg)
    if result_success:
        print(f"Archivo generado: {result_data.get('archivo')}")
