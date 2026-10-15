import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from playwright.async_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)
from playwright_stealth import Stealth

from shared.arca_login import launch_arca_browser_context, login_arca, open_arca_service, wait_page_ready


BOT_NAME = "portal_iva_ddjj_y_libros"
TEMP_DIR = Path("./temp")
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
PORTAL_IVA_DIRECT_URL = "https://siapweb.cloud.afip.gob.ar/iva"
PORTAL_IVA_INIT_URL = "https://siapweb.cloud.afip.gob.ar/iva/#/init"
PORTAL_IVA_URL_PATTERN = re.compile(r"(siapweb\.cloud\.afip\.gob\.ar/iva|liva\.afip\.gob\.ar)", re.I)
PORTAL_IVA_NO_ACCESS_PATTERN = re.compile(r"no posee activa la caracterizaci[oó]n requerida", re.I)
PORTAL_IVA_ERROR_NO_VALID_REPRESENTADOS = "CUIT sin representados validos con acceso a liquidaciones de IVA"

TEMP_DIR.mkdir(exist_ok=True)


@dataclass
class PortalIvaDdjjYLibrosConfig:
    cuit_representante: str
    clave: str
    cuit_representado: str
    periodos: list[str]
    descargar_libros_iva: bool = True
    descargar_ddjj: bool = True
    headless: bool = True
    denominacion: str = ""


@dataclass
class PresentedRow:
    table_index: int
    row_index: int
    periodo: str
    secuencia: str
    row_text: str


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si"}


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return default


def _format_cuit(cuit: str) -> str:
    digits = re.sub(r"\D", "", cuit or "")
    if len(digits) == 11:
        return f"{digits[:2]}-{digits[2:10]}-{digits[10:]}"
    return cuit


def _sanitize_filename_component(value: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned or "SIN_DATO"


def periodo_to_aaaamm(periodo_raw: str) -> str:
    text = (periodo_raw or "").strip()
    digits = re.sub(r"\D", "", text)
    if re.fullmatch(r"\d{6}", digits):
        if int(digits[:4]) > 1900:
            return digits
        return f"{digits[2:]}{digits[:2]}"

    match = re.fullmatch(r"(\d{4})[-/](\d{1,2})", text)
    if match:
        return f"{match.group(1)}{match.group(2).zfill(2)}"

    match = re.fullmatch(r"(\d{1,2})[-/](\d{4})", text)
    if match:
        return f"{match.group(2)}{match.group(1).zfill(2)}"

    return digits or _sanitize_filename_component(text)


def parse_periodo_from_input(periodo_raw: str) -> str:
    periodo = periodo_to_aaaamm(periodo_raw)
    if re.fullmatch(r"\d{6}", periodo):
        return f"{periodo[4:]}/{periodo[:4]}"
    return (periodo_raw or "").strip()


def _build_filename(
    cuit_representado: str,
    periodo_raw: str,
    tipo: str,
    subtipo: str,
    extension: str,
) -> str:
    cuit_digits = re.sub(r"\D", "", cuit_representado or "")
    fin_cuit = cuit_digits[-1:] if cuit_digits else "0"
    cuit_value = cuit_digits or _sanitize_filename_component(cuit_representado)
    periodo_value = periodo_to_aaaamm(periodo_raw)
    tipo_value = _sanitize_filename_component(tipo)
    subtipo_value = _sanitize_filename_component(subtipo)
    ext = extension if extension.startswith(".") else f".{extension}"
    return f"{fin_cuit} - {cuit_value} - {periodo_value} - {tipo_value} - {subtipo_value}{ext}"


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def _secuencia_to_tipo(secuencia: str) -> str:
    sec_lower = (secuencia or "").strip().lower()
    if "original" in sec_lower:
        return "Original"
    if "rectificativa" in sec_lower:
        num_match = re.search(r"(\d+)", secuencia or "")
        num = num_match.group(1) if num_match else "1"
        return f"Rectificativa {num}"
    return _sanitize_filename_component(secuencia or "Sin secuencia")


def _looks_like_blank_html(content: str) -> bool:
    compact = re.sub(r"\s+", "", content or "").lower()
    if not compact:
        return True
    if compact in {
        "<html><head></head><body></body></html>",
        "<html><head></head><body></body></html>",
    }:
        return True
    return len(compact) < 120


async def _is_blank_page(page: Page) -> bool:
    try:
        content = await page.content()
    except Exception:
        return True
    return _looks_like_blank_html(content)


def _portal_iva_init_url_with_key() -> str:
    token = format(int(datetime.now().timestamp() * 1000), "x")[-6:]
    return f"{PORTAL_IVA_INIT_URL}?_k={token}"


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME) -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.har_dir = self.run_dir / "har"
        self.descargas_dir = self.run_dir / "descargas"
        for path in (
            self.logs_dir,
            self.screenshots_dir,
            self.html_dir,
            self.har_dir,
            self.descargas_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.step = 0
        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False

        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

        file_handler = logging.FileHandler(self.logs_dir / f"{prefix}.log")
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        self.logger.addHandler(stream_handler)

        self.log("run_dir=%s", self.run_dir)

    def log(self, message: str, *args) -> None:
        self.logger.info(message, *args)

    def log_error(self, message: str, *args) -> None:
        self.logger.error(message, *args)

    async def capture(self, page: Optional[Page], step: str) -> None:
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


async def execute_step(debug: DebugCapture, page: Optional[Page], step_name: str, action):
    debug.log("step_start=%s url=%s", step_name, getattr(page, "url", ""))
    try:
        result = await action()
        await debug.capture(page, step_name)
        debug.log("step_end=%s status=OK url=%s", step_name, getattr(page, "url", ""))
        return result
    except Exception as exc:
        debug.log_error("step_end=%s status=ERROR url=%s error=%s", step_name, getattr(page, "url", ""), exc)
        await debug.capture(page, f"{step_name}_error")
        raise


async def try_click(locator: Locator, timeout: int = 10_000) -> bool:
    try:
        await locator.first.click(timeout=timeout)
        return True
    except Exception:
        return False


async def click_first(locators: list[Locator], timeout: int = 10_000) -> bool:
    for locator in locators:
        try:
            if await locator.count() == 0:
                continue
            if await try_click(locator, timeout=timeout):
                return True
        except Exception:
            continue
    return False


async def is_visible(locator: Locator, timeout: int = 1_500) -> bool:
    try:
        if await locator.count() == 0:
            return False
        await locator.first.wait_for(state="visible", timeout=timeout)
        return True
    except Exception:
        try:
            return await locator.first.is_visible()
        except Exception:
            return False


async def is_session_expired(page: Page) -> bool:
    checks = [
        page.get_by_text("usuario no esta logueado", exact=False),
        page.get_by_text("sesion ha expirado", exact=False),
        page.get_by_text("sesión ha expirado", exact=False),
        page.get_by_role("button", name=re.compile(r"IR A LA P[ÁA]GINA DE AFIP", re.I)),
    ]
    for locator in checks:
        if await is_visible(locator, timeout=500):
            return True
    return False


async def ensure_session_active(page: Page) -> None:
    if await is_session_expired(page):
        raise RuntimeError("La sesion de Portal IVA expiro")


async def wait_portal_ready(page: Page) -> None:
    try:
        await wait_page_ready(page, timeout_ms=20_000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=20_000)
    except Exception:
        pass


async def login_and_open_portal(
    playwright: Playwright,
    config: PortalIvaDdjjYLibrosConfig,
    debug: DebugCapture,
) -> tuple[Browser, BrowserContext, Page]:
    har_path = debug.har_dir / f"{BOT_NAME}.har"
    browser, context, page = await launch_arca_browser_context(
        playwright,
        headless=config.headless,
        context_kwargs={
            "accept_downloads": True,
            "record_har_path": str(har_path),
            "record_har_content": "embed",
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/154.0.8037.97 Safari/537.36",
        },
    )
    await Stealth().apply_stealth_async(page)
    page.set_default_timeout(35_000)

    async def _login():
        login_errors: list[str] = []
        login_result = await login_arca(
            page,
            config.cuit_representante,
            config.clave,
            error_logs=login_errors,
            fail_on_captcha=True,
        )
        for line in login_errors:
            debug.log("arca_login=%s", line)
        if login_result.captcha_detected:
            debug.log_error("Captcha detectado")
            raise RuntimeError("Captcha detectado")
        if login_result.password_change_required:
            debug.log_error("Se requiere cambiar la clave fiscal")
            raise RuntimeError("Se requiere cambiar la clave fiscal")
        if not login_result.success:
            raise RuntimeError(login_errors[-1] if login_errors else "No se pudo iniciar sesion en ARCA")

    await execute_step(debug, page, "login_arca", _login)

    async def _open_service():
        open_errors: list[str] = []
        portal_page = await open_arca_service(
            page,
            service_name="Portal IVA",
            service_matchers=(
                re.compile(r"PORTAL\s+IVA", re.I),
                "PORTAL IVA Presentación del",
                "siap_web_iva",
            ),
            error_logs=open_errors,
            click_ver_todos=True,
            service_unavailable_message="No se encuentra habilitado el servicio de Portal IVA",
            popup_timeout_ms=15_000,
        )
        for line in open_errors:
            debug.log("arca_service=%s", line)
        if portal_page is None:
            raise RuntimeError(open_errors[-1] if open_errors else "No se encuentra habilitado el servicio de Portal IVA")

        portal_page.set_default_timeout(35_000)
        try:
            await wait_page_ready(portal_page, timeout_ms=25_000)
            await portal_page.wait_for_url(PORTAL_IVA_URL_PATTERN, timeout=25_000)
        except PlaywrightTimeoutError:
            debug.log("portal_iva_load_timeout action=goto url=%s", PORTAL_IVA_DIRECT_URL)
            await portal_page.goto(PORTAL_IVA_DIRECT_URL, wait_until="domcontentloaded", timeout=25_000)
            await wait_page_ready(portal_page, timeout_ms=25_000)
            await portal_page.wait_for_url(PORTAL_IVA_URL_PATTERN, timeout=25_000)

        if await _is_blank_page(portal_page):
            init_url = _portal_iva_init_url_with_key()
            debug.log("portal_iva_blank_detected action=goto_init url=%s", init_url)
            await portal_page.goto(init_url, wait_until="domcontentloaded", timeout=25_000)
            await wait_page_ready(portal_page, timeout_ms=25_000)

        await portal_page.wait_for_timeout(3_000)
        return portal_page

    portal_page = await execute_step(debug, page, "abrir_servicio_portal_iva", _open_service)
    return browser, context, portal_page


async def get_representados_disponibles(page: Page) -> list[str]:
    cuit_pattern = re.compile(r"\d{2}-\d{8}-\d")
    css_selectors = [
        ".col-sm-7 a.panel.panel-default, .col-sm-7 .panel.panel-default",
        ".row.panels a.panel.panel-default",
        "a[title='Representar a...']",
        "h6:has-text('REPRESENTAR A:') ~ .row.panels a.panel",
        "h6:has-text('REPRESENTAR A:') + .row a.panel",
        "h6:has-text('REPRESENTANDO A:') + .m-y-2 .panel.panel-default",
        "a.panel.panel-default[title='Representar a...']",
        "div:has(> h6:has-text('REPRESENTAR A:')) a.panel",
    ]
    for selector in css_selectors:
        try:
            items = page.locator(selector)
            count = await items.count()
            if count == 0:
                continue
            disponibles = []
            seen = set()
            for i in range(count):
                text = " ".join((await items.nth(i).inner_text()).strip().split())
                if text and cuit_pattern.search(text) and text not in seen:
                    seen.add(text)
                    disponibles.append(text)
            if disponibles:
                return disponibles
        except Exception:
            continue

    try:
        cuit_re_text = re.compile(r"\[?CUIT\s*\d{2}-\d{8}-\d\]?", re.I)
        nodes = page.locator(f"text=/{cuit_re_text.pattern}/")
        count = await nodes.count()
        if count > 0:
            disponibles = []
            seen = set()
            for i in range(min(count, 30)):
                try:
                    parent = nodes.nth(i).locator(
                        "xpath=ancestor::a[contains(@class,'panel')]|"
                        "ancestor::div[contains(@class,'panel')]|"
                        "ancestor::button[contains(@class,'list-group-item')]"
                    )
                    if await parent.count() > 0:
                        text = " ".join((await parent.first.inner_text()).strip().split())
                        if text not in seen:
                            seen.add(text)
                            disponibles.append(text)
                except Exception:
                    continue
            if disponibles:
                return disponibles
    except Exception:
        pass

    try:
        raw = await page.evaluate(
            r"""
            () => {
                const cuitRe = /\d{2}-\d{8}-\d/;
                const results = [];
                const seen = new Set();
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while (node = walker.nextNode()) {
                    const text = node.textContent.trim();
                    if (!cuitRe.test(text)) continue;
                    let el = node.parentElement;
                    while (el && el !== document.body) {
                        const tag = el.tagName;
                        const cls = (el.className && typeof el.className === 'string') ? el.className : '';
                        if (tag === 'A' || tag === 'BUTTON' || cls.includes('panel') || cls.includes('list-group-item')) {
                            const full = el.textContent.trim().replace(/\s+/g, ' ');
                            if (!seen.has(full)) {
                                seen.add(full);
                                results.push(full);
                            }
                            break;
                        }
                        el = el.parentElement;
                    }
                }
                return results;
            }
            """
        )
        if raw and isinstance(raw, list):
            return [str(item) for item in raw if cuit_pattern.search(str(item))]
    except Exception:
        pass

    return []


async def select_representado(page: Page, config: PortalIvaDdjjYLibrosConfig, debug: DebugCapture) -> list[str]:
    formatted_cuit = _format_cuit(config.cuit_representado)
    target_cuit_digits = re.sub(r"\D", "", config.cuit_representado or "")
    representante_cuit_digits = re.sub(r"\D", "", config.cuit_representante or "")
    representados: list[str] = []

    async def _is_target_selected() -> bool:
        if await is_visible(
            page.get_by_text(re.compile(rf"Representando a:.*{re.escape(formatted_cuit)}", re.I)),
            timeout=1_200,
        ):
            return True
        if target_cuit_digits == representante_cuit_digits and await is_visible(
            page.get_by_text(formatted_cuit, exact=False),
            timeout=1_200,
        ):
            return True
        return False

    async def _select():
        nonlocal representados
        await ensure_session_active(page)
        await wait_portal_ready(page)
        await page.wait_for_timeout(1_500)

        for attempt in range(2):
            if not await _is_blank_page(page):
                break
            init_url = _portal_iva_init_url_with_key()
            debug.log("portal_iva_blank_page action=goto_init attempt=%s url=%s", attempt + 1, init_url)
            await page.goto(init_url, wait_until="domcontentloaded", timeout=25_000)
            await wait_portal_ready(page)

        if await _is_blank_page(page):
            raise RuntimeError("No se pudo cargar Portal IVA")

        representados = await get_representados_disponibles(page)
        debug.log("Representados disponibles: %s", representados)

        if await _is_target_selected():
            debug.log("representado_already_selected cuit=%s", formatted_cuit)
            return

        opened_switcher = await click_first(
            [
                page.get_by_title("cambio relación"),
                page.get_by_title("cambio relacion"),
                page.get_by_role("link", name=""),
                page.locator("[title*='cambio relación' i]"),
                page.locator("[title*='cambio relacion' i]"),
            ],
            timeout=8_000,
        )
        if opened_switcher:
            await page.wait_for_timeout(1_500)
            try:
                await page.wait_for_url("**/changeRelation**", timeout=10_000)
            except Exception:
                pass
            await wait_portal_ready(page)
            representados = await get_representados_disponibles(page)
            debug.log("Representados disponibles (changeRelation): %s", representados)

        await click_first(
            [
                page.get_by_title("Representar a..."),
                page.get_by_text("Representar a...", exact=False),
            ],
            timeout=6_000,
        )
        await page.wait_for_timeout(1_000)

        selected = await click_first(
            [
                page.get_by_role("button", name=formatted_cuit),
                page.get_by_text(formatted_cuit, exact=False),
                page.get_by_text(config.cuit_representado, exact=False),
            ],
            timeout=8_000,
        )
        if selected:
            await wait_portal_ready(page)
            await page.wait_for_timeout(2_000)

        current_html = await page.content()
        for attempt in range(2):
            if not _looks_like_blank_html(current_html):
                break
            init_url = _portal_iva_init_url_with_key()
            debug.log("portal_iva_blank_page_after_select action=goto_init attempt=%s url=%s", attempt + 1, init_url)
            await page.goto(init_url, wait_until="domcontentloaded", timeout=25_000)
            await wait_portal_ready(page)
            await page.wait_for_timeout(1_500)
            current_html = await page.content()
        if _looks_like_blank_html(current_html):
            raise RuntimeError("No se pudo cargar Portal IVA")

        has_no_access_warning = bool(PORTAL_IVA_NO_ACCESS_PATTERN.search(current_html))
        available_cuit_digits = {
            re.sub(r"\D", "", raw_cuit)
            for raw_cuit in re.findall(r"\b\d{2}-\d{8}-\d\b", current_html)
        }
        has_other_representados = any(cuit and cuit != target_cuit_digits for cuit in available_cuit_digits)
        target_present_in_options = target_cuit_digits in available_cuit_digits

        if has_no_access_warning and not has_other_representados:
            raise RuntimeError(PORTAL_IVA_ERROR_NO_VALID_REPRESENTADOS)
        if await _is_target_selected():
            return
        if has_no_access_warning and not target_present_in_options:
            raise RuntimeError(PORTAL_IVA_ERROR_NO_VALID_REPRESENTADOS)
        if not selected and not target_present_in_options:
            raise RuntimeError(PORTAL_IVA_ERROR_NO_VALID_REPRESENTADOS)

        raise RuntimeError(f"No se pudo seleccionar el representado {formatted_cuit}")

    await execute_step(debug, page, "seleccionar_representado", _select)
    return representados


async def go_home(page: Page, debug: DebugCapture) -> None:
    await ensure_session_active(page)
    if "/init" in page.url and not await _is_blank_page(page):
        return
    init_url = _portal_iva_init_url_with_key()
    debug.log("navigation=goto_home url=%s", init_url)
    await page.goto(init_url, wait_until="domcontentloaded", timeout=25_000)
    await wait_portal_ready(page)
    await page.wait_for_timeout(1_500)


async def open_consultar(page: Page, debug: DebugCapture) -> None:
    await go_home(page, debug)
    clicked = await click_first(
        [
            page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.alt)"),
            page.locator("button").filter(has_text=re.compile(r"CONSULTAR", re.I)),
            page.get_by_role("button", name=re.compile(r"CONSULTAR", re.I)),
            page.get_by_text(re.compile(r"CONSULTAR", re.I)),
        ],
        timeout=15_000,
    )
    if not clicked:
        raise RuntimeError("No se encontro el boton Consultar en Portal IVA")
    await wait_portal_ready(page)
    await page.wait_for_timeout(1_000)


async def open_libros_iva_list(page: Page, debug: DebugCapture) -> None:
    await open_consultar(page, debug)
    clicked = await click_first(
        [
            page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.LibroIVA.alt)"),
            page.locator("button").filter(has_text=re.compile(r"Libro\s+IVA", re.I)),
            page.get_by_role("button", name=re.compile(r"Libro\s+IVA", re.I)),
            page.get_by_text(re.compile(r"Libro\s+IVA", re.I)),
        ],
        timeout=15_000,
    )
    if not clicked:
        raise RuntimeError("No se encontro la opcion Libro IVA en declaraciones presentadas")
    await wait_portal_ready(page)
    await page.wait_for_timeout(2_000)


async def open_ddjj_list(page: Page, debug: DebugCapture) -> None:
    await open_consultar(page, debug)
    clicked = await click_first(
        [
            page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.portal.alt)"),
            page.locator("button").filter(has_text=re.compile(r"Portal|Declaraciones\s+Juradas|DDJJ", re.I)),
            page.get_by_role("button", name=re.compile(r"Portal|Declaraciones\s+Juradas|DDJJ", re.I)),
            page.get_by_text(re.compile(r"Portal|Declaraciones\s+Juradas|DDJJ", re.I)),
        ],
        timeout=15_000,
    )
    if not clicked:
        raise RuntimeError("No se encontro la opcion Declaraciones Juradas en declaraciones presentadas")
    await wait_portal_ready(page)
    await page.wait_for_timeout(2_000)


async def collect_rows_for_period(
    page: Page,
    periodo_normalizado: str,
    period_indices: list[int],
    sequence_indices: list[int],
    debug: DebugCapture,
    source_label: str,
) -> list[PresentedRow]:
    rows_info: list[PresentedRow] = []
    tables = page.locator("table")
    table_count = await tables.count()
    available: list[str] = []

    for table_index in range(min(table_count, 100)):
        table = tables.nth(table_index)
        rows = table.locator("tbody tr")
        row_count = await rows.count()
        for row_index in range(row_count):
            row = rows.nth(row_index)
            cells = row.locator("td")
            cell_count = await cells.count()
            if cell_count == 0:
                continue

            periodo_cell = ""
            for period_index in period_indices:
                if period_index < cell_count:
                    periodo_candidate = (await cells.nth(period_index).inner_text()).strip()
                    if periodo_candidate:
                        periodo_cell = periodo_candidate
                        break

            secuencia_cell = ""
            for sequence_index in sequence_indices:
                if sequence_index < cell_count:
                    secuencia_candidate = (await cells.nth(sequence_index).inner_text()).strip()
                    if secuencia_candidate:
                        secuencia_cell = secuencia_candidate
                        break

            if periodo_cell:
                available.append(f"{periodo_cell} | {secuencia_cell}")

            if periodo_cell == periodo_normalizado:
                rows_info.append(
                    PresentedRow(
                        table_index=table_index,
                        row_index=row_index,
                        periodo=periodo_cell,
                        secuencia=secuencia_cell,
                        row_text=" ".join((await row.inner_text()).split()),
                    )
                )

    debug.log("%s disponibles: %s", source_label, available)
    return rows_info


async def resolve_row(page: Page, row_info: PresentedRow) -> Locator:
    table = page.locator("table").nth(row_info.table_index)
    rows = table.locator("tbody tr")
    if await rows.count() > row_info.row_index:
        return rows.nth(row_info.row_index)

    row_text = row_info.row_text
    if row_text:
        matching = page.locator("table tbody tr").filter(has_text=row_info.periodo).filter(has_text=row_info.secuencia)
        if await matching.count() > 0:
            return matching.first
    raise RuntimeError(f"No se pudo relocalizar la fila {row_info.periodo} {row_info.secuencia}")


async def click_row_action(row: Locator, page: Page, labels: list[str]) -> bool:
    locators: list[Locator] = []
    for label in labels:
        pattern = re.compile(label, re.I)
        locators.extend(
            [
                row.get_by_title(pattern),
                row.locator(f"[title*='{label}' i]"),
                row.get_by_role("button", name=pattern),
                row.get_by_role("link", name=pattern),
                row.locator("button").filter(has_text=pattern),
                row.locator("a").filter(has_text=pattern),
            ]
        )
    locators.append(row.locator("button, a").last)

    for locator in locators:
        try:
            if await locator.count() == 0:
                continue
            await locator.first.scroll_into_view_if_needed(timeout=3_000)
            await locator.first.click(timeout=8_000)
            await wait_portal_ready(page)
            await page.wait_for_timeout(1_500)
            return True
        except Exception:
            continue
    return False


async def close_optional_popup(popup_task: asyncio.Task) -> None:
    popup_page: Optional[Page] = None
    if not popup_task.done():
        popup_task.cancel()
        return
    try:
        popup_page = popup_task.result()
    except Exception:
        popup_task.cancel()
    if popup_page is not None:
        try:
            await popup_page.close()
        except Exception:
            pass


async def download_from_candidates(
    page: Page,
    candidates: list[Locator],
    destination: Path,
    debug: DebugCapture,
    label: str,
) -> bool:
    destination = _unique_path(destination)
    for locator in candidates:
        try:
            if await locator.count() == 0:
                continue

            popup_task = asyncio.create_task(page.wait_for_event("popup", timeout=8_000))
            try:
                async with page.expect_download(timeout=45_000) as download_info:
                    await locator.first.scroll_into_view_if_needed(timeout=3_000)
                    await locator.first.click(timeout=10_000)
                download = await download_info.value
            finally:
                await close_optional_popup(popup_task)

            await download.save_as(str(destination))
            debug.log("download_saved label=%s path=%s suggested=%s", label, destination, download.suggested_filename)
            return True
        except Exception as exc:
            debug.log("download_candidate_failed label=%s error=%s", label, exc)
            continue
    return False


async def download_libro_iva_xml(page: Page, config: PortalIvaDdjjYLibrosConfig, debug: DebugCapture) -> None:
    async def _navigate():
        await open_libros_iva_list(page, debug)

    await execute_step(debug, page, "navegar_libros_iva_presentados", _navigate)

    for periodo in config.periodos:
        periodo_normalizado = parse_periodo_from_input(periodo)
        debug.log("Procesando Libro IVA XML periodo=%s", periodo_normalizado)

        async def _process_period():
            await open_libros_iva_list(page, debug)
            rows_info = await collect_rows_for_period(
                page,
                periodo_normalizado,
                period_indices=[2, 1, 0],
                sequence_indices=[3, 4, 2],
                debug=debug,
                source_label="Libros IVA",
            )
            if not rows_info:
                debug.log("No se encontro Libro IVA para periodo=%s", periodo_normalizado)
                return

            debug.log("Libros IVA encontrados periodo=%s cantidad=%s", periodo_normalizado, len(rows_info))
            for index, row_info in enumerate(rows_info, start=1):
                await open_libros_iva_list(page, debug)
                row = await resolve_row(page, row_info)
                tipo = _secuencia_to_tipo(row_info.secuencia)
                debug.log("Libro IVA fila=%s/%s periodo=%s tipo=%s", index, len(rows_info), periodo_normalizado, tipo)

                opened = await click_row_action(row, page, ["Ver", "Consultar", "Seleccionar"])
                if not opened:
                    raise RuntimeError(f"No se pudo abrir el Libro IVA {periodo_normalizado} {tipo}")

                await debug.capture(page, f"libro_iva_detalle_{periodo_to_aaaamm(periodo)}_{_safe_step_name(tipo)}")

                filename = _build_filename(config.cuit_representado, periodo, "Libro IVA", f"{tipo} - XML", ".zip")
                destination = debug.descargas_dir / filename
                downloaded = await download_from_candidates(
                    page,
                    [
                        page.locator("#btnDescargarXML"),
                        page.locator("#btnDescargarXml"),
                        page.locator("[id*='Descargar'][id*='XML' i]"),
                        page.locator("[title*='XML' i]"),
                        page.get_by_role("button", name=re.compile(r"XML|Descargar\s+XML", re.I)),
                        page.get_by_role("link", name=re.compile(r"XML|Descargar\s+XML", re.I)),
                        page.locator("button, a").filter(has_text=re.compile(r"XML|Descargar\s+XML", re.I)),
                    ],
                    destination,
                    debug,
                    f"Libro IVA XML {periodo_normalizado} {tipo}",
                )
                if not downloaded:
                    await debug.capture(page, f"libro_iva_xml_no_encontrado_{periodo_to_aaaamm(periodo)}_{_safe_step_name(tipo)}")
                    debug.log_error("No se pudo descargar XML Libro IVA periodo=%s tipo=%s", periodo_normalizado, tipo)

        await execute_step(debug, page, f"descargar_libro_iva_xml_{periodo}", _process_period)

    await execute_step(debug, page, "volver_inicio_desde_libros_iva", lambda: go_home(page, debug))


async def click_continuar(page: Page) -> bool:
    return await click_first(
        [
            page.locator("#btnContinuar"),
            page.get_by_role("button", name=re.compile(r"Continuar", re.I)),
            page.get_by_role("link", name=re.compile(r"Continuar", re.I)),
            page.locator("button, a").filter(has_text=re.compile(r"Continuar", re.I)),
        ],
        timeout=12_000,
    )


async def advance_to_preimpresion(page: Page, debug: DebugCapture) -> None:
    preimpresion = page.locator("div.preimpresion").first
    for attempt in range(5):
        if await is_visible(preimpresion, timeout=1_000):
            return
        clicked = await click_continuar(page)
        if not clicked:
            break
        await wait_portal_ready(page)
        await page.wait_for_timeout(2_000)
        await debug.capture(page, f"ddjj_continuar_{attempt + 1}")

    try:
        await preimpresion.wait_for(state="visible", timeout=12_000)
        return
    except Exception:
        pass
    raise RuntimeError("No se encontro la preimpresion de la Declaracion Jurada")


async def extract_preimpresion_data(page: Page, config: PortalIvaDdjjYLibrosConfig, periodo: str, tipo: str) -> dict:
    preimpresion = page.locator("div.preimpresion").first
    extracted = await preimpresion.evaluate(
        r"""
        root => {
            const clean = value => (value || '').replace(/\s+/g, ' ').trim();
            const tables = Array.from(root.querySelectorAll('table')).map((table, tableIndex) => ({
                index: tableIndex,
                headers: Array.from(table.querySelectorAll('thead th')).map(th => clean(th.textContent)),
                rows: Array.from(table.querySelectorAll('tbody tr, tr')).map(tr =>
                    Array.from(tr.querySelectorAll('th, td')).map(td => clean(td.textContent))
                ).filter(row => row.length > 0),
            }));
            const campos = {};
            Array.from(root.querySelectorAll('tr')).forEach(tr => {
                const cells = Array.from(tr.querySelectorAll('th, td')).map(td => clean(td.textContent)).filter(Boolean);
                if (cells.length === 2 && cells[0].length <= 80) campos[cells[0]] = cells[1];
            });
            Array.from(root.querySelectorAll('label')).forEach(label => {
                const key = clean(label.textContent).replace(/:$/, '');
                const parent = label.parentElement;
                const value = parent ? clean(parent.textContent).replace(clean(label.textContent), '').trim() : '';
                if (key && value && key.length <= 80) campos[key] = value;
            });
            return {
                html: root.outerHTML,
                texto: clean(root.innerText || root.textContent),
                tablas: tables,
                campos,
            };
        }
        """
    )
    extracted["metadata"] = {
        "cuit_representado": re.sub(r"\D", "", config.cuit_representado or ""),
        "periodo_input": periodo,
        "periodo": periodo_to_aaaamm(periodo),
        "tipo": tipo,
        "url": page.url,
        "scraped_at": datetime.now().isoformat(timespec="seconds"),
    }
    return extracted


async def build_print_html(page: Page, preimpresion_html: str) -> str:
    styles = await page.evaluate(
        r"""
        () => Array.from(document.querySelectorAll('style, link[rel="stylesheet"]'))
            .map(el => el.outerHTML)
            .join('\n')
        """
    )
    base_href = page.url
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<base href="{base_href}">
{styles}
<style>
body {{ background: white; margin: 24px; }}
.preimpresion {{ margin: 0 auto; }}
</style>
</head>
<body>{preimpresion_html}</body>
</html>"""


async def save_preimpresion_artifacts(
    page: Page,
    config: PortalIvaDdjjYLibrosConfig,
    debug: DebugCapture,
    periodo: str,
    tipo: str,
) -> None:
    preimpresion = page.locator("div.preimpresion").first
    data = await extract_preimpresion_data(page, config, periodo, tipo)

    base_pdf = _build_filename(config.cuit_representado, periodo, "DDJJ", f"{tipo} - Declaracion", ".pdf")
    base_png = _build_filename(config.cuit_representado, periodo, "DDJJ", f"{tipo} - Declaracion", ".png")
    base_html = _build_filename(config.cuit_representado, periodo, "DDJJ", f"{tipo} - Preimpresion", ".html")
    base_json = _build_filename(config.cuit_representado, periodo, "DDJJ", f"{tipo} - Datos", ".json")

    html_path = _unique_path(debug.descargas_dir / base_html)
    json_path = _unique_path(debug.descargas_dir / base_json)
    png_path = _unique_path(debug.descargas_dir / base_png)
    pdf_path = _unique_path(debug.descargas_dir / base_pdf)

    html_path.write_text(data["html"], encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    debug.log("ddjj_scrape_saved html=%s json=%s", html_path, json_path)

    try:
        await preimpresion.screenshot(path=str(png_path))
        debug.log("ddjj_image_saved path=%s", png_path)
    except Exception as exc:
        debug.log_error("ddjj_image_failed error=%s", exc)
        await page.screenshot(path=str(png_path), full_page=True)
        debug.log("ddjj_image_fallback_full_page path=%s", png_path)

    print_page: Optional[Page] = None
    try:
        print_html = await build_print_html(page, data["html"])
        print_page = await page.context.new_page()
        await print_page.set_content(print_html, wait_until="load", timeout=20_000)
        await print_page.pdf(path=str(pdf_path), format="A4", print_background=True)
        debug.log("ddjj_pdf_saved path=%s", pdf_path)
    finally:
        if print_page is not None:
            try:
                await print_page.close()
            except Exception:
                pass


async def download_ddjj_presentadas(page: Page, config: PortalIvaDdjjYLibrosConfig, debug: DebugCapture) -> None:
    async def _navigate():
        await open_ddjj_list(page, debug)

    await execute_step(debug, page, "navegar_ddjj_presentadas", _navigate)

    for periodo in config.periodos:
        periodo_normalizado = parse_periodo_from_input(periodo)
        debug.log("Procesando DDJJ periodo=%s", periodo_normalizado)

        async def _process_period():
            await open_ddjj_list(page, debug)
            rows_info = await collect_rows_for_period(
                page,
                periodo_normalizado,
                period_indices=[1, 2, 0],
                sequence_indices=[3, 4, 2],
                debug=debug,
                source_label="DDJJ",
            )
            if not rows_info:
                debug.log("No se encontro DDJJ para periodo=%s", periodo_normalizado)
                return

            debug.log("DDJJ encontradas periodo=%s cantidad=%s", periodo_normalizado, len(rows_info))
            for index, row_info in enumerate(rows_info, start=1):
                await open_ddjj_list(page, debug)
                row = await resolve_row(page, row_info)
                tipo = _secuencia_to_tipo(row_info.secuencia)
                debug.log("DDJJ fila=%s/%s periodo=%s tipo=%s", index, len(rows_info), periodo_normalizado, tipo)

                opened = await click_row_action(
                    row,
                    page,
                    ["Ver", "Seleccionar", "Consultar", "Continuar", "Descargar formulario"],
                )
                if not opened:
                    raise RuntimeError(f"No se pudo abrir la DDJJ {periodo_normalizado} {tipo}")

                await debug.capture(page, f"ddjj_detalle_{periodo_to_aaaamm(periodo)}_{_safe_step_name(tipo)}")
                await advance_to_preimpresion(page, debug)
                await debug.capture(page, f"ddjj_preimpresion_{periodo_to_aaaamm(periodo)}_{_safe_step_name(tipo)}")
                await save_preimpresion_artifacts(page, config, debug, periodo, tipo)

        await execute_step(debug, page, f"descargar_ddjj_{periodo}", _process_period)

    await execute_step(debug, page, "volver_inicio_desde_ddjj", lambda: go_home(page, debug))


async def run_with_config(playwright: Playwright, config: PortalIvaDdjjYLibrosConfig) -> bool:
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log(
        "config periodos=%s descargar_libros_iva=%s descargar_ddjj=%s headless=%s",
        config.periodos,
        config.descargar_libros_iva,
        config.descargar_ddjj,
        config.headless,
    )

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    portal_page: Optional[Page] = None

    try:
        browser, context, portal_page = await login_and_open_portal(playwright, config, debug)
        await select_representado(portal_page, config, debug)

        if config.descargar_libros_iva:
            await download_libro_iva_xml(portal_page, config, debug)
        if config.descargar_ddjj:
            await download_ddjj_presentadas(portal_page, config, debug)

        debug.log("Proceso completado exitosamente")
        return True
    except Exception as exc:
        debug.log_error("run_failed error=%s", exc)
        await debug.capture(portal_page, "run_failed")
        return False
    finally:
        if context:
            try:
                await context.close()
            except Exception:
                pass
        if browser:
            try:
                await browser.close()
            except Exception:
                pass
        print(f"Logs y evidencias guardados en: {debug.run_dir}")


async def main() -> None:
    load_dotenv()

    cuit_representante = _env_first(
        "PORTAL_IVA_DDJJ_LIBROS_CUIT_REPRESENTANTE",
        "PORTAL_IVA_LIBROS_CUIT_REPRESENTANTE",
        "PORTAL_IVA_CUIT_REPRESENTANTE",
    )
    clave = _env_first("PORTAL_IVA_DDJJ_LIBROS_CLAVE", "PORTAL_IVA_LIBROS_CLAVE", "PORTAL_IVA_CLAVE")
    cuit_representado = _env_first(
        "PORTAL_IVA_DDJJ_LIBROS_CUIT_REPRESENTADO",
        "PORTAL_IVA_LIBROS_CUIT_REPRESENTADO",
        "PORTAL_IVA_CUIT_REPRESENTADO",
    )
    periodos_str = _env_first(
        "PORTAL_IVA_DDJJ_LIBROS_PERIODOS",
        "PORTAL_IVA_LIBROS_PERIODOS",
        "PORTAL_IVA_DDJJ_LIBROS_PERIODO",
        "PORTAL_IVA_LIBROS_PERIODO",
    )
    headless = _env_flag("PORTAL_IVA_HEADLESS", default=False)
    descargar_libros_iva = _env_flag("PORTAL_IVA_DDJJ_LIBROS_DESCARGAR_LIBROS_IVA", default=True)
    descargar_ddjj = _env_flag("PORTAL_IVA_DDJJ_LIBROS_DESCARGAR_DDJJ", default=True)
    denominacion = _env_first(
        "PORTAL_IVA_DDJJ_LIBROS_DENOMINACION",
        "PORTAL_IVA_LIBROS_DENOMINACION",
        "PORTAL_IVA_DENOMINACION",
    )

    if not cuit_representante or not clave or not cuit_representado:
        print(
            "Faltan variables de entorno: PORTAL_IVA_DDJJ_LIBROS_CUIT_REPRESENTANTE, "
            "PORTAL_IVA_DDJJ_LIBROS_CLAVE, PORTAL_IVA_DDJJ_LIBROS_CUIT_REPRESENTADO"
        )
        return

    periodos = [p.strip() for p in periodos_str.split(",") if p.strip()]
    if not periodos:
        print("No se especificaron periodos en PORTAL_IVA_DDJJ_LIBROS_PERIODOS")
        return

    if not descargar_libros_iva and not descargar_ddjj:
        print("No hay descargas habilitadas. Active libros IVA y/o DDJJ.")
        return

    print("Iniciando Portal IVA DDJJ y Libros Logger...")
    print(f"CUIT Representante: {cuit_representante}")
    print(f"CUIT Representado: {cuit_representado}")
    print(f"Periodos: {periodos}")
    print(f"Descargar Libro IVA XML: {descargar_libros_iva}")
    print(f"Descargar DDJJ: {descargar_ddjj}")

    config = PortalIvaDdjjYLibrosConfig(
        cuit_representante=cuit_representante,
        clave=clave,
        cuit_representado=cuit_representado,
        periodos=periodos,
        descargar_libros_iva=descargar_libros_iva,
        descargar_ddjj=descargar_ddjj,
        headless=headless,
        denominacion=denominacion,
    )

    async with async_playwright() as playwright:
        success = await run_with_config(playwright, config)
        if success:
            print("Proceso finalizado correctamente.")
        else:
            print("El proceso finalizo con errores.")


if __name__ == "__main__":
    asyncio.run(main())
