import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
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

from shared.arca_login import launch_arca_browser_context, login_arca, open_arca_service, wait_page_ready


BOT_NAME = "portal_iva"
DOWNLOAD_DIR = Path("./descargas")
TEMP_DIR = Path("./temp")
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
PORTAL_IVA_DIRECT_URL = "https://siapweb.cloud.afip.gob.ar/iva"
PORTAL_IVA_INIT_URL = "https://siapweb.cloud.afip.gob.ar/iva/#/init"
PORTAL_IVA_URL_PATTERN = re.compile(r"(siapweb\.cloud\.afip\.gob\.ar/iva|liva\.afip\.gob\.ar)", re.I)
PORTAL_IVA_NO_ACCESS_PATTERN = re.compile(r"no posee activa la caracterizaci[oó]n requerida", re.I)
PORTAL_IVA_ERROR_NO_VALID_REPRESENTADOS = "CUIT sin representados válidos con acceso a liquidaciones de IVA"
DOWNLOAD_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si"}


def _format_cuit(cuit: str) -> str:
    digits = re.sub(r"\D", "", cuit or "")
    if len(digits) == 11:
        return f"{digits[:2]}-{digits[2:10]}-{digits[10:]}"
    return cuit


def _sanitize_filename_component(value: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")
    return cleaned


def _normalize_period_value(periodo: str) -> str:
    clean_periodo = (periodo or "").strip()
    for pattern in (r"(\d{2})/(\d{4})", r"(\d{2})-(\d{4})"):
        match = re.fullmatch(pattern, clean_periodo)
        if match:
            return f"{match.group(2)}{match.group(1)}"
    for pattern in (r"(\d{4})/(\d{2})", r"(\d{4})-(\d{2})"):
        match = re.fullmatch(pattern, clean_periodo)
        if match:
            return f"{match.group(1)}{match.group(2)}"
    return _normalize_period_label(clean_periodo)


def _build_download_path(
    base_dir: Path,
    cuit_representado: str,
    denominacion: str,
    book: str,
    periodo: str,
    suggested_filename: str,
) -> Path:
    suffix = Path(suggested_filename).suffix or ".csv"
    cuit_digits = re.sub(r"\D", "", cuit_representado or "")
    fin_cuit = cuit_digits[-1:] or _sanitize_filename_component(cuit_representado)[-1:] or "0"
    cuit_value = cuit_digits or _sanitize_filename_component(cuit_representado) or "SIN_CUIT"
    periodo_value = _sanitize_filename_component(_normalize_period_value(periodo)) or "SIN_PERIODO"
    denominacion_value = _sanitize_filename_component(denominacion) or "SIN DENOMINACION"
    book_value = "ventas" if book == "ventas" else "compras"
    filename = " - ".join([fin_cuit, cuit_value, periodo_value, book_value, denominacion_value])
    return base_dir / f"{filename}{suffix}"


def _normalize_period_label(label: str) -> str:
    clean_label = (label or "").strip()
    match = re.fullmatch(r"(\d{2})/(\d{4})", clean_label)
    if match:
        return f"{match.group(2)}{match.group(1)}"
    return clean_label


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


@dataclass
class PortalIvaConfig:
    cuit_representante: str
    cuit_representado: str
    clave_representado: str
    periodo: str
    operaciones_ng_o_e: bool
    prorrateo_global: bool
    prorrateo_asignacion_directa: bool
    prorrateo_ambos: bool
    importacion_definitiva_bienes: bool
    importacion_servicios: bool
    regimen_turiva: bool
    bienes_usados: bool
    ninguna_anteriores: bool
    liv_cbte: str
    liv_alicuota: str
    lic_cbte: str
    lic_alicuota: str
    descarga_CSV_ventas: bool
    descarga_CSV_compras: bool
    importar_txt_ventas: bool
    importar_txt_compras: bool
    denominacion: str = ""
    headless: bool = True
    # Tras las descargas se descarta el borrador que el bot creo (Vista previa -> Eliminar borrador).
    descartar_borrador: bool = True
    periodos_seleccionables: list[dict[str, str]] = field(default_factory=list)


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
        self.periodos_seleccionables: list[dict[str, str]] = []
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


async def is_session_expired(page: Page) -> bool:
    checks = [
        page.get_by_text("usuario no está logueado", exact=False),
        page.get_by_text("sesión ha expirado", exact=False),
        page.get_by_role("button", name=re.compile(r"IR A LA P[ÁA]GINA DE AFIP", re.I)),
    ]
    for locator in checks:
        if await is_visible(locator, timeout=500):
            return True
    return False


async def ensure_session_active(page: Page) -> None:
    if await is_session_expired(page):
        raise RuntimeError("La sesión de Portal IVA expiró")


async def try_continue_ddjj(page: Page) -> bool:
    return await click_first(
        [
            page.get_by_role("button", name=re.compile(r"Continuar", re.I)),
            page.get_by_role("link", name=re.compile(r"Continuar", re.I)),
            page.get_by_text("CONTINUAR", exact=False),
            page.get_by_text("Continuar", exact=False),
        ],
        timeout=15_000,
    )


async def maybe_close_modal(page: Page) -> bool:
    return await click_first(
        [
            page.locator(".bootstrap-dialog .close"),
            page.locator(".bootstrap-dialog [data-dismiss='modal']"),
            page.get_by_role("button", name=re.compile(r"Cancelar|Cerrar|Close", re.I)),
            page.get_by_role("button", name="Close"),
            page.get_by_role("button", name=re.compile(r"Cerrar|Close", re.I)),
            page.get_by_text("Close", exact=True),
            page.get_by_text("Cancelar", exact=True),
        ],
        timeout=5_000,
    )


async def maybe_click_processed(page: Page) -> bool:
    return await click_first(
        [
            page.get_by_text("Procesada", exact=False),
            page.get_by_text("Procesando", exact=False),
        ],
        timeout=5_000,
    )


async def return_to_home(page: Page, debug: DebugCapture) -> None:
    await ensure_session_active(page)
    for name, locators in (
        (
            "volver_libro_iva_borrador",
            [
                page.get_by_role("link", name=re.compile(r"Libro IVA - Borrador", re.I)),
                page.get_by_text("Libro IVA - Borrador", exact=False),
            ],
        ),
        (
            "volver_inicio",
            [
                page.get_by_role("link", name=re.compile(r"Inicio", re.I)),
                page.get_by_text("Inicio", exact=False),
            ],
        ),
        (
            "volver",
            [
                page.get_by_role("button", name=re.compile(r"Volver", re.I)),
                page.get_by_text("Volver", exact=False),
            ],
        ),
    ):
        if await click_first(locators, timeout=5_000):
            debug.log("navigation=%s", name)
            await page.wait_for_timeout(2_000)
            return


async def open_import_menu(page: Page) -> None:
    ok = await click_first(
        [
            page.get_by_role("button", name=re.compile(r"Importar", re.I)),
            page.get_by_text("Importar", exact=False),
        ],
        timeout=10_000,
    )
    if not ok:
        raise RuntimeError("No se encontró el botón Importar")


async def open_book(page: Page, debug: DebugCapture, book: str) -> None:
    await ensure_session_active(page)
    pattern = re.compile(r"Libro Ventas", re.I) if book == "ventas" else re.compile(r"Libro Compras", re.I)
    href_fragment = "verVentas.do" if book == "ventas" else "verCompras.do"
    continue_pattern = (
        re.compile(r"Continuar al Libro Ventas", re.I)
        if book == "ventas"
        else re.compile(r"Continuar al Libro Compras", re.I)
    )

    # Un solo libro por llamada: con ambos en el mismo patron ("Ventas|Compras"),
    # pedir compras abria ventas (verificado en vivo: URL verVentas.do).
    registro_pattern = re.compile(
        r"Libro\s+Ventas\s+Registr" if book == "ventas" else r"Libro\s+Compras\s+Registr",
        re.I,
    )

    async def _open_from_current() -> bool:
        return await click_first(
            [
                page.get_by_role("link", name=registro_pattern),
                page.get_by_role("link", name=continue_pattern),
                page.get_by_role("link", name=pattern),
                page.locator(f"a[href*='{href_fragment}']").filter(has_text=pattern),
                page.locator(f"a[href*='{href_fragment}']"),
                page.get_by_text(pattern, exact=False),
            ],
            timeout=10_000,
        )

    async def _advance_to_book_context() -> bool:
        moved = False

        if await click_first(
            [
                page.get_by_role("button", name=re.compile(r"Ingresar", re.I)),
                page.get_by_text("Ingresar", exact=False),
                page.get_by_label("Sin texto (iva.home.btn.nueva.declaracion.alt)"),
            ],
            timeout=8_000,
        ):
            debug.log("navigation=ingresar_home")
            await page.wait_for_timeout(2_000)
            moved = True

        if await click_first(
            [
                page.locator("#btnGuardar:not([disabled])"),
                page.get_by_role("button", name=re.compile(r"Continuar", re.I)),
                page.get_by_text("Continuar", exact=False),
            ],
            timeout=8_000,
        ):
            debug.log("navigation=continuar_datos_iniciales")
            await page.wait_for_timeout(2_000)
            moved = True

        if await try_continue_ddjj(page):
            debug.log("navigation=continuar_ddjj")
            await page.wait_for_timeout(2_000)
            moved = True

        return moved

    if await _open_from_current():
        await page.wait_for_timeout(3_000)
        return

    if await _advance_to_book_context():
        if await _open_from_current():
            await page.wait_for_timeout(3_000)
            return

    await return_to_home(page, debug)
    if await _advance_to_book_context():
        if await _open_from_current():
            await page.wait_for_timeout(3_000)
            return

    raise RuntimeError(f"No se pudo abrir el libro de {book}")


async def click_label_option(page: Page, text: str) -> None:
    locator = page.locator("label").filter(has_text=text).locator("span")
    if await locator.count() == 0:
        return
    await locator.first.click()


async def set_checkbox_if_present(page: Page, selector: str, checked: bool) -> None:
    if not checked:
        return
    locator = page.locator(selector).first
    if await locator.count() == 0:
        return
    try:
        await locator.set_checked(True, force=True)
    except Exception:
        await locator.evaluate(
            """el => {
                el.checked = true;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.click();
            }"""
        )


async def set_select_if_present(page: Page, selector: str, value: Optional[str]) -> None:
    if not value:
        return
    locator = page.locator(selector).first
    if await locator.count() == 0:
        return
    await locator.select_option(value)


async def store_selectable_periods(page: Page, config: PortalIvaConfig, debug: DebugCapture) -> list[dict[str, str]]:
    if config.periodos_seleccionables:
        return config.periodos_seleccionables

    periodos: list[dict[str, str]] = []
    option_locator = page.locator("#periodo option")
    if await option_locator.count() > 0:
        raw_periodos = await option_locator.evaluate_all(
            """options => options
                .map(option => ({
                    value: (option.value || '').trim(),
                    label: (option.textContent || '').trim()
                }))
                .filter(option => option.value || option.label)
            """
        )
        periodos = [
            {
                "value": item.get("value") or _normalize_period_label(item.get("label", "")),
                "label": item.get("label") or item.get("value", ""),
            }
            for item in raw_periodos
        ]
    elif await page.locator("#periodoPresentacion").count() > 0:
        label = (await page.locator("#periodoPresentacion").first.inner_text()).strip()
        if label:
            periodos = [{"value": _normalize_period_label(label), "label": label}]

    if periodos:
        config.periodos_seleccionables = periodos
        debug.periodos_seleccionables = periodos
        debug.log("periodos_seleccionables=%s", periodos)
        print(f"Períodos seleccionables: {periodos}")

    return periodos


async def login_and_open_portal(
    playwright: Playwright,
    config: PortalIvaConfig,
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
        },
    )
    page.set_default_timeout(20_000)

    async def _login():
        login_errors: list[str] = []
        login_result = await login_arca(
            page,
            config.cuit_representante,
            config.clave_representado,
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
            raise RuntimeError(login_errors[-1] if login_errors else "No se pudo iniciar sesión en ARCA")

    await execute_step(debug, page, "login_arca", _login)

    async def _open_service():
        open_errors: list[str] = []
        portal_page = await open_arca_service(
            page,
            service_name="Portal IVA",
            service_matchers=(
                re.compile(r"PORTAL\s+IVA", re.I),
                "PORTAL IVA Presentación del",
            ),
            error_logs=open_errors,
            click_ver_todos=True,
            service_unavailable_message="No se encuentra habilitado el servicio de Portal IVA",
        )
        for line in open_errors:
            debug.log("arca_service=%s", line)
        if portal_page is None:
            raise RuntimeError(open_errors[-1] if open_errors else "No se encuentra habilitado el servicio de Portal IVA")
        portal_page.set_default_timeout(20_000)

        try:
            await wait_page_ready(portal_page, timeout_ms=20_000)
            await portal_page.wait_for_url(PORTAL_IVA_URL_PATTERN, timeout=20_000)
        except PlaywrightTimeoutError:
            debug.log("portal_iva_load_timeout action=goto url=%s", PORTAL_IVA_DIRECT_URL)
            await portal_page.goto(PORTAL_IVA_DIRECT_URL, wait_until="domcontentloaded", timeout=20_000)
            await wait_page_ready(portal_page, timeout_ms=20_000)
            await portal_page.wait_for_url(PORTAL_IVA_URL_PATTERN, timeout=20_000)

        if await _is_blank_page(portal_page):
            init_url = _portal_iva_init_url_with_key()
            debug.log("portal_iva_blank_detected action=goto_init url=%s", init_url)
            await portal_page.goto(init_url, wait_until="domcontentloaded", timeout=20_000)
            await wait_page_ready(portal_page, timeout_ms=20_000)
            if await _is_blank_page(portal_page):
                debug.log("portal_iva_blank_persistent warning=continue")

        await portal_page.wait_for_timeout(5_000)
        return portal_page

    portal_page = await execute_step(debug, page, "abrir_servicio_portal_iva", _open_service)
    return browser, context, portal_page


async def select_representado(page: Page, config: PortalIvaConfig, debug: DebugCapture) -> None:
    formatted_cuit = _format_cuit(config.cuit_representado)
    target_cuit_digits = re.sub(r"\D", "", config.cuit_representado or "")
    representante_cuit_digits = re.sub(r"\D", "", config.cuit_representante or "")

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
        await ensure_session_active(page)
        try:
            await wait_page_ready(page, timeout_ms=15_000)
        except Exception:
            pass
        await page.wait_for_timeout(1_500)

        for attempt in range(2):
            if not await _is_blank_page(page):
                break
            init_url = _portal_iva_init_url_with_key()
            debug.log("portal_iva_blank_page action=goto_init attempt=%s url=%s", attempt + 1, init_url)
            await page.goto(init_url, wait_until="domcontentloaded", timeout=20_000)
            await wait_page_ready(page, timeout_ms=20_000)
            await page.wait_for_timeout(2_000)

        if await _is_blank_page(page):
            raise RuntimeError("No se pudo cargar Portal IVA")

        if await _is_target_selected():
            debug.log("representado_already_selected cuit=%s", formatted_cuit)
            return

        opened_switcher = await click_first(
            [
                page.get_by_title("cambio relación"),
                page.get_by_role("link", name=""),
                page.locator("[title*='cambio relación' i]"),
            ],
            timeout=8_000,
        )
        if opened_switcher:
            await page.wait_for_timeout(1_500)

        # El portal lista un enlace title="Representar a..." por cada persona
        # representable (31 en una cuenta real). Hacer clic en el primero cambia
        # al representado equivocado, por eso se elige el que contiene el CUIT.
        selected = await click_first(
            [
                page.get_by_title("Representar a...").filter(has_text=formatted_cuit),
                page.get_by_title("Representar a...").filter(has_text=config.cuit_representado),
            ],
            timeout=8_000,
        )
        if not selected:
            await click_first(
                [
                    page.get_by_title("Representar a..."),
                    page.get_by_text("Representar a...", exact=False),
                ],
                timeout=6_000,
            )
        await page.wait_for_timeout(1_000)

        selected = selected or await click_first(
            [
                page.get_by_role("button", name=formatted_cuit),
                page.get_by_text(formatted_cuit, exact=False),
                page.get_by_text(config.cuit_representado, exact=False),
            ],
            timeout=8_000,
        )
        if selected:
            await page.wait_for_timeout(2_000)

        try:
            await page.get_by_text(PORTAL_IVA_NO_ACCESS_PATTERN).first.wait_for(state="visible", timeout=2_000)
        except Exception:
            pass

        current_html = await page.content()
        for attempt in range(2):
            if not _looks_like_blank_html(current_html):
                break
            init_url = _portal_iva_init_url_with_key()
            debug.log("portal_iva_blank_page_after_select action=goto_init attempt=%s url=%s", attempt + 1, init_url)
            await page.goto(init_url, wait_until="domcontentloaded", timeout=20_000)
            await wait_page_ready(page, timeout_ms=20_000)
            await page.wait_for_timeout(2_000)
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

        if selected:
            try:
                await wait_page_ready(page, timeout_ms=10_000)
            except Exception:
                pass
            await page.wait_for_timeout(1_500)
            if await _is_target_selected():
                return

        if not selected and not target_present_in_options:
            raise RuntimeError(PORTAL_IVA_ERROR_NO_VALID_REPRESENTADOS)

        raise RuntimeError(f"No se pudo seleccionar el representado {formatted_cuit}")

    await execute_step(debug, page, "seleccionar_representado", _select)


async def prepare_period(page: Page, config: PortalIvaConfig, debug: DebugCapture) -> None:
    async def _prepare():
        await ensure_session_active(page)

        opened_nueva = await click_first(
            [
                page.get_by_role("button", name=re.compile(r"iva\.home\.btn\.nueva", re.I)),
                page.get_by_label("Sin texto (iva.home.btn.nueva"),
                page.get_by_text(re.compile(r"Nueva", re.I)),
            ],
            timeout=12_000,
        )
        if opened_nueva:
            await page.wait_for_timeout(2_000)
            await store_selectable_periods(page, config, debug)

        if await page.locator("#periodo").count() > 0:
            await store_selectable_periods(page, config, debug)
            await page.locator("#periodo").select_option(config.periodo)
            validated = await click_first(
                [
                    page.get_by_role("button", name=re.compile(r"Validar", re.I)),
                    page.get_by_label("Sin texto (iva.btn.home.validar.periodo.alt)"),
                ],
                timeout=12_000,
            )
            if not validated:
                raise RuntimeError(f"No se pudo validar el período {config.periodo}")
            await page.wait_for_timeout(2_000)

        opened_liva = await click_first(
            [
                page.get_by_role("button", name=re.compile(r"iva\.btn\.home\.liva", re.I)),
                page.get_by_label("Sin texto (iva.btn.home.liva."),
            ],
            timeout=10_000,
        )

        if opened_liva:
            await page.wait_for_timeout(1_500)
        await store_selectable_periods(page, config, debug)
        # El formulario "Datos iniciales" se renderiza tarde: sin esta espera el
        # bot lo daba por ausente, no marcaba nada y nunca llegaba a "Continuar".
        try:
            await page.locator("#operacionesNoGravadasExentas").first.wait_for(state="attached", timeout=20_000)
        except Exception:
            debug.log("datos_iniciales_form_not_found waited=20s")
        if await page.locator("#operacionesNoGravadasExentas").count() > 0:
            await set_checkbox_if_present(page, "#operacionesNoGravadasExentas", config.operaciones_ng_o_e)
            if config.operaciones_ng_o_e and config.prorrateo_ambos:
                await set_select_if_present(page, "#tipoProrrateo", "3")
            elif config.operaciones_ng_o_e and config.prorrateo_global:
                await set_select_if_present(page, "#tipoProrrateo", "2")
            elif config.operaciones_ng_o_e and config.prorrateo_asignacion_directa:
                await set_select_if_present(page, "#tipoProrrateo", "1")

            await set_checkbox_if_present(page, "#importacionDefinitivaBienes", config.importacion_definitiva_bienes)
            await set_checkbox_if_present(page, "#importacionServicios", config.importacion_servicios)
            await set_checkbox_if_present(page, "#regimenTurIVA", config.regimen_turiva)
            await set_checkbox_if_present(page, "#bienesUsadosMaterialReciclable", config.bienes_usados)
            await set_checkbox_if_present(page, "#ningunaOperacion", config.ninguna_anteriores)
            await page.wait_for_timeout(1_500)

            guardar = page.locator("#btnGuardar").first
            if await guardar.count() > 0:
                await page.wait_for_function(
                    "() => { const btn = document.querySelector('#btnGuardar'); return btn && !btn.disabled; }",
                    timeout=8_000,
                )
                await guardar.click(timeout=10_000)
                await page.wait_for_timeout(4_000)
                return

        if opened_liva:
            if config.operaciones_ng_o_e:
                await click_label_option(page, "Operaciones No Gravadas o")
            if config.operaciones_ng_o_e and config.prorrateo_ambos:
                if await click_first([page.get_by_role("button", name=re.compile(r"prorrateo", re.I))], timeout=5_000):
                    await page.get_by_role(
                        "option",
                        name="Con prorrateo por asignación directa y global",
                    ).click()
            elif config.operaciones_ng_o_e and config.prorrateo_asignacion_directa:
                if await click_first([page.get_by_role("button", name=re.compile(r"prorrateo", re.I))], timeout=5_000):
                    await page.get_by_role(
                        "option",
                        name="Con prorrateo por asignación directa",
                    ).click()
            elif config.operaciones_ng_o_e and config.prorrateo_global:
                if await click_first([page.get_by_role("button", name=re.compile(r"prorrateo", re.I))], timeout=5_000):
                    await page.get_by_role("option", name="Con prorrateo global").click()

            if config.importacion_definitiva_bienes:
                await click_label_option(page, "Importación definitiva de")
            if config.importacion_servicios:
                await click_label_option(page, "Importación de servicios")
            if config.regimen_turiva:
                await click_label_option(page, "Incluido en el régimen TurIVA")
            if config.bienes_usados:
                await click_label_option(page, "Compras de bienes usados o")
            if config.ninguna_anteriores:
                await click_label_option(page, "No realizo ninguna actividad")

        await try_continue_ddjj(page)
        await page.wait_for_timeout(4_000)

    await execute_step(debug, page, "preparar_periodo", _prepare)


async def import_from_arca(page: Page, debug: DebugCapture, book: str) -> None:
    async def _import():
        await ensure_session_active(page)
        await open_import_menu(page)
        opened = await click_first(
            [
                page.get_by_role("link", name=re.compile(r"Importar desde ARCA", re.I)),
                page.get_by_text(re.compile(r"Importar desde ARCA", re.I)),
            ],
            timeout=12_000,
        )
        if not opened:
            raise RuntimeError(f"No se encontró 'Importar desde ARCA' para {book}")

        await page.wait_for_timeout(2_000)
        if await page.locator("#btnImportarAFIPImportar").count() == 0:
            raise RuntimeError(f"No se encontró el botón de importación ARCA para {book}")

        await page.locator("#btnImportarAFIPImportar").click()
        await page.wait_for_timeout(8_000)
        await maybe_click_processed(page)
        await maybe_close_modal(page)
        await page.wait_for_timeout(3_000)

    await execute_step(debug, page, f"{book}_importar_desde_arca", _import)


async def delete_all_records(page: Page, debug: DebugCapture, book: str) -> None:
    async def _delete():
        await ensure_session_active(page)
        deleted = await click_first(
            [
                page.get_by_role("button", name="Eliminar Todos..."),
                page.get_by_text("Eliminar Todos...", exact=False),
            ],
            timeout=8_000,
        )
        if not deleted:
            debug.log("delete_skipped book=%s reason=no_button", book)
            return

        confirmed = await click_first(
            [
                page.locator(".bootstrap-dialog .btn-danger").filter(has_text="Eliminar"),
                page.locator(".modal.in .btn-danger"),
                page.locator(".modal-dialog .btn-danger"),
                page.get_by_text("Eliminar", exact=True),
                page.get_by_role("button", name=re.compile(r"Eliminar", re.I)),
            ],
            timeout=5_000,
        )
        if not confirmed:
            debug.log("delete_confirm_warning book=%s", book)
            await maybe_close_modal(page)
            return
        await page.wait_for_timeout(3_000)

    await execute_step(debug, page, f"{book}_eliminar_todo", _delete)


async def download_csv(page: Page, debug: DebugCapture, config: PortalIvaConfig, book: str) -> Path:
    async def _download() -> Path:
        await ensure_session_active(page)
        popup_task = asyncio.create_task(page.wait_for_event("popup", timeout=5_000))
        try:
            async with page.expect_download(timeout=45_000) as download_info:
                clicked = await click_first(
                    [
                        page.get_by_role("button", name=re.compile(r"CSV", re.I)),
                        page.get_by_role("link", name=re.compile(r"CSV", re.I)),
                        page.get_by_text("CSV", exact=True),
                    ],
                    timeout=10_000,
                )
                if not clicked:
                    raise RuntimeError(f"No se encontró el botón CSV para {book}")

            download = await download_info.value
            final_path = _build_download_path(
                debug.descargas_dir,
                config.cuit_representado,
                config.denominacion,
                book,
                config.periodo,
                download.suggested_filename,
            )
            await download.save_as(str(final_path))
            debug.log("download_saved book=%s path=%s", book, final_path)
            return final_path
        finally:
            popup_page: Optional[Page] = None
            try:
                popup_page = await popup_task
            except Exception:
                popup_task.cancel()
            if popup_page is not None:
                try:
                    await popup_page.close()
                except Exception:
                    pass

    return await execute_step(debug, page, f"{book}_descargar_csv", _download)


async def import_files(
    page: Page,
    debug: DebugCapture,
    book: str,
    comprobantes_path: str,
    alicuotas_path: str,
    comprobantes_label: str,
    alicuotas_label: str,
) -> None:
    async def _import():
        await ensure_session_active(page)
        await open_import_menu(page)
        opened = await click_first(
            [
                page.get_by_role("link", name=re.compile(r"Importar Archivos", re.I)),
                page.get_by_text(re.compile(r"Importar Archivos", re.I)),
            ],
            timeout=12_000,
        )
        if not opened:
            raise RuntimeError(f"No se encontró 'Importar Archivos' para {book}")

        await page.get_by_label(comprobantes_label).set_input_files(comprobantes_path)
        await page.locator("#btnImportarArchivosImportar").click()
        await page.wait_for_timeout(2_000)

        await page.get_by_label(alicuotas_label).set_input_files(alicuotas_path)
        await page.locator("#btnImportarArchivosImportar").click()
        await page.wait_for_timeout(2_000)

        await page.locator("#btnImportarArchivosImportar").click()
        await page.wait_for_timeout(5_000)
        await maybe_click_processed(page)
        await maybe_close_modal(page)
        await page.wait_for_timeout(3_000)

    await execute_step(debug, page, f"{book}_importar_archivos", _import)


async def ejecutar_descarga(page: Page, config: PortalIvaConfig, debug: DebugCapture) -> dict[str, Optional[Path]]:
    resultados: dict[str, Optional[Path]] = {"ventas": None, "compras": None}

    if config.descarga_CSV_ventas:
        await execute_step(debug, page, "abrir_libro_ventas_descarga", lambda: open_book(page, debug, "ventas"))
        try:
            await import_from_arca(page, debug, "ventas")
        except Exception as exc:
            debug.log("ventas_importar_desde_arca_warning=%s", exc)
        resultados["ventas"] = await download_csv(page, debug, config, "ventas")
        await delete_all_records(page, debug, "ventas")
        await return_to_home(page, debug)

    if config.descarga_CSV_compras:
        await execute_step(debug, page, "abrir_libro_compras_descarga", lambda: open_book(page, debug, "compras"))
        try:
            await import_from_arca(page, debug, "compras")
        except Exception as exc:
            debug.log("compras_importar_desde_arca_warning=%s", exc)
        resultados["compras"] = await download_csv(page, debug, config, "compras")
        await delete_all_records(page, debug, "compras")
        await return_to_home(page, debug)

    return resultados


async def ejecutar_carga(page: Page, config: PortalIvaConfig, debug: DebugCapture) -> dict[str, bool]:
    resultados = {"ventas": False, "compras": False}

    if config.importar_txt_ventas:
        await execute_step(debug, page, "abrir_libro_ventas_carga", lambda: open_book(page, debug, "ventas"))
        await delete_all_records(page, debug, "ventas")
        await import_files(
            page,
            debug,
            "ventas",
            config.liv_cbte,
            config.liv_alicuota,
            "Archivo Ventas *",
            "Archivo IVA Ventas *",
        )
        resultados["ventas"] = True
        await return_to_home(page, debug)

    if config.importar_txt_compras:
        await execute_step(debug, page, "abrir_libro_compras_carga", lambda: open_book(page, debug, "compras"))
        await delete_all_records(page, debug, "compras")
        await import_files(
            page,
            debug,
            "compras",
            config.lic_cbte,
            config.lic_alicuota,
            "Archivo Compras *",
            "Archivo IVA Compras *",
        )
        resultados["compras"] = True
        await maybe_close_modal(page)
        await delete_all_records(page, debug, "compras")
        await return_to_home(page, debug)

    return resultados


async def ejecutar_descartar_borrador(page: Page, debug: DebugCapture) -> bool:
    """Descarta el borrador de declaracion que el propio bot creo en el periodo.

    Flujo verificado en vivo: menu de presentacion -> "Vista previa" -> boton
    ``#btnEliminarBorrador`` -> modal "Descartar Borrador" -> "DESCARTAR BORRADOR".
    Solo se llama despues de terminar todas las descargas.
    """

    async def _descartar() -> bool:
        await ensure_session_active(page)

        # Las descargas terminan en el Libro Ventas/Compras o en el inicio del
        # portal: se vuelve al menu del borrador, donde vive "Vista previa".
        if await page.locator("#btnVistaPrevia").count() == 0:
            await click_first(
                [
                    page.get_by_role("link", name=re.compile(r"Borrador Presentaci[oó]n", re.I)),
                    page.get_by_role("link", name=re.compile(r"Libro IVA - Borrador", re.I)),
                    page.get_by_text("Libro IVA - Borrador", exact=False),
                ],
                timeout=8_000,
            )
            await page.wait_for_timeout(2_000)

        if not await click_first([page.locator("#btnVistaPrevia")], timeout=10_000):
            raise RuntimeError("No se encontro el boton 'Vista previa'")
        debug.log("navigation=vista_previa")
        await page.locator("#btnEliminarBorrador").first.wait_for(state="visible", timeout=15_000)

        try:
            await page.wait_for_load_state("networkidle", timeout=10_000)
        except Exception:
            pass

        boton_confirmar = page.locator("button.btn-danger").filter(
            has_text=re.compile(r"Descartar\s+Borrador", re.I)
        )
        # Verificado en vivo: si se hace clic antes de que la pagina enlace su
        # manejador, el boton queda resaltado pero el dialogo nunca se abre. Se
        # reintenta el clic hasta que el dialogo aparezca.
        for intento in range(1, 5):
            if not await click_first([page.locator("#btnEliminarBorrador")], timeout=10_000):
                raise RuntimeError("No se pudo hacer clic en 'btnEliminarBorrador'")
            debug.log("navigation=eliminar_borrador intento=%s", intento)
            try:
                await boton_confirmar.first.wait_for(state="visible", timeout=6_000)
                break
            except Exception:
                debug.log("descartar_dialogo_no_visible intento=%s", intento)
                await page.wait_for_timeout(1_500)

        confirmado = await click_first(
            [
                boton_confirmar,
                page.locator("[role='dialog'] button.btn-danger"),
                page.locator(".modal.in .btn-danger").filter(has_text=re.compile(r"Descartar Borrador", re.I)),
                page.locator(".bootstrap-dialog .btn-danger").filter(has_text=re.compile(r"Descartar", re.I)),
                page.get_by_role("button", name=re.compile(r"Descartar Borrador", re.I)),
            ],
            timeout=10_000,
        )
        if not confirmado:
            raise RuntimeError("No se pudo confirmar 'Descartar Borrador'")
        debug.log("navigation=descartar_borrador_confirmado")
        await page.wait_for_timeout(4_000)
        # El borrador descartado deja de existir: el boton ya no debe estar visible.
        quedo = await is_visible(page.locator("#btnEliminarBorrador"), timeout=2_000)
        if quedo:
            raise RuntimeError("El borrador sigue presente tras confirmar el descarte")
        return True

    return bool(await execute_step(debug, page, "descartar_borrador", _descartar))


async def run_with_config(playwright: Playwright, config: PortalIvaConfig) -> bool:
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("config periodo=%s headless=%s", config.periodo, config.headless)
    debug.log("operaciones descarga ventas=%s compras=%s carga ventas=%s carga compras=%s",
              config.descarga_CSV_ventas, config.descarga_CSV_compras,
              config.importar_txt_ventas, config.importar_txt_compras)

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    portal_page: Optional[Page] = None

    try:
        browser, context, portal_page = await login_and_open_portal(playwright, config, debug)
        debug.log("portal_page_url=%s", portal_page.url)

        await select_representado(portal_page, config, debug)
        await prepare_period(portal_page, config, debug)

        descargas: dict[str, Optional[Path]] = {}
        cargas: dict[str, bool] = {}

        if config.descarga_CSV_ventas or config.descarga_CSV_compras:
            descargas = await ejecutar_descarga(portal_page, config, debug)

        if config.importar_txt_ventas or config.importar_txt_compras:
            cargas = await ejecutar_carga(portal_page, config, debug)

        ok_descargas = (
            (not config.descarga_CSV_ventas or descargas.get("ventas") is not None)
            and (not config.descarga_CSV_compras or descargas.get("compras") is not None)
        )
        ok_cargas = (
            (not config.importar_txt_ventas or cargas.get("ventas") is True)
            and (not config.importar_txt_compras or cargas.get("compras") is True)
        )

        success = ok_descargas and ok_cargas
        debug.log("resultado success=%s descargas=%s cargas=%s", success, descargas, cargas)

        # Solo se descarta cuando TODAS las descargas pedidas terminaron bien: si algo
        # fallo, el borrador se conserva para poder inspeccionarlo.
        if config.descartar_borrador and success and (config.descarga_CSV_ventas or config.descarga_CSV_compras):
            await ejecutar_descartar_borrador(portal_page, debug)
        return success
    except Exception as exc:
        debug.log_error("run_failed error=%s", exc)
        await debug.capture(portal_page, "run_failed")
        return False
    finally:
        if context is not None:
            try:
                await context.close()
            except Exception as exc:
                debug.log_error("context_close_failed error=%s", exc)
        if browser is not None:
            try:
                await browser.close()
            except Exception as exc:
                debug.log_error("browser_close_failed error=%s", exc)

        har_path = debug.har_dir / f"{BOT_NAME}.har"
        if har_path.exists():
            debug.log("har_saved path=%s size=%s", har_path, har_path.stat().st_size)
        else:
            debug.log("har_missing path=%s", har_path)

        print(f"Logs y evidencias: {debug.run_dir}")


async def run(
    playwright: Playwright,
    cuit_representante: str,
    cuit_representado: str,
    clave_representado: str,
    periodo: str,
    operaciones_ng_o_e: bool,
    prorrateo_global: bool,
    prorrateo_asignacion_directa: bool,
    prorrateo_ambos: bool,
    importacion_definitiva_bienes: bool,
    importacion_servicios: bool,
    regimen_turiva: bool,
    bienes_usados: bool,
    ninguna_anteriores: bool,
    liv_cbte: str,
    liv_alicuota: str,
    lic_cbte: str,
    lic_alicuota: str,
    descarga_CSV_ventas: bool,
    descarga_CSV_compras: bool,
    importar_txt_ventas: bool,
    importar_txt_compras: bool,
    headless: bool = True,
    denominacion: str = "",
    descartar_borrador: bool = True,
) -> bool:
    config = PortalIvaConfig(
        cuit_representante=cuit_representante,
        cuit_representado=cuit_representado,
        clave_representado=clave_representado,
        periodo=periodo,
        operaciones_ng_o_e=operaciones_ng_o_e,
        prorrateo_global=prorrateo_global,
        prorrateo_asignacion_directa=prorrateo_asignacion_directa,
        prorrateo_ambos=prorrateo_ambos,
        importacion_definitiva_bienes=importacion_definitiva_bienes,
        importacion_servicios=importacion_servicios,
        regimen_turiva=regimen_turiva,
        bienes_usados=bienes_usados,
        ninguna_anteriores=ninguna_anteriores,
        liv_cbte=liv_cbte,
        liv_alicuota=liv_alicuota,
        lic_cbte=lic_cbte,
        lic_alicuota=lic_alicuota,
        descarga_CSV_ventas=descarga_CSV_ventas,
        descarga_CSV_compras=descarga_CSV_compras,
        importar_txt_ventas=importar_txt_ventas,
        importar_txt_compras=importar_txt_compras,
        denominacion=denominacion,
        headless=headless,
        descartar_borrador=descartar_borrador,
    )
    return await run_with_config(playwright, config)


async def main(
    cuit_representante: str,
    cuit_representado: str,
    clave_representado: str,
    periodo: str,
    operaciones_ng_o_e: bool,
    prorrateo_global: bool,
    prorrateo_asignacion_directa: bool,
    prorrateo_ambos: bool,
    importacion_definitiva_bienes: bool,
    importacion_servicios: bool,
    regimen_turiva: bool,
    bienes_usados: bool,
    ninguna_anteriores: bool,
    liv_cbte: str,
    liv_alicuota: str,
    lic_cbte: str,
    lic_alicuota: str,
    descarga_CSV_ventas: bool,
    descarga_CSV_compras: bool,
    importar_txt_ventas: bool,
    importar_txt_compras: bool,
    headless: bool = True,
    denominacion: str = "",
    descartar_borrador: bool = True,
) -> bool:
    async with async_playwright() as playwright:
        return await run(
            playwright,
            cuit_representante,
            cuit_representado,
            clave_representado,
            periodo,
            operaciones_ng_o_e,
            prorrateo_global,
            prorrateo_asignacion_directa,
            prorrateo_ambos,
            importacion_definitiva_bienes,
            importacion_servicios,
            regimen_turiva,
            bienes_usados,
            ninguna_anteriores,
            liv_cbte,
            liv_alicuota,
            lic_cbte,
            lic_alicuota,
            descarga_CSV_ventas,
            descarga_CSV_compras,
            importar_txt_ventas,
            importar_txt_compras,
            headless=headless,
            denominacion=denominacion,
            descartar_borrador=descartar_borrador,
        )


if __name__ == "__main__":
    load_dotenv()

    cuit_representante = os.getenv("CUIT_REPRESENTANTE_PORTAL_IVA", "").strip()
    cuit_representado = os.getenv("CUIT_REPRESENTADO_PORTAL_IVA", "").strip()
    clave_representado = os.getenv("CLAVE_PORTAL_IVA", "").strip()
    periodo = os.getenv("PERIODO_PORTAL_IVA", "").strip()
    denominacion = os.getenv("DENOMINACION_PORTAL_IVA", "").strip()

    operaciones_ng_o_e = _env_flag("OPERACIONES_NG_O_E_PORTAL_IVA", default=False)
    prorrateo_global = _env_flag("PRORRATEO_GLOBAL_PORTAL_IVA", default=False)
    prorrateo_asignacion_directa = _env_flag("PRORRATEO_ASIGNACION_DIRECTA_PORTAL_IVA", default=False)
    prorrateo_ambos = _env_flag("PRORRATEO_AMBOS_PORTAL_IVA", default=False)
    importacion_definitiva_bienes = _env_flag("IMPORTACION_DEFINITIVA_BIENES_PORTAL_IVA", default=False)
    importacion_servicios = _env_flag("IMPORTACION_SERVICIOS_PORTAL_IVA", default=False)
    regimen_turiva = _env_flag("REGIMEN_TURIVA_PORTAL_IVA", default=False)
    bienes_usados = _env_flag("BIENES_USADOS_PORTAL_IVA", default=False)
    ninguna_anteriores = _env_flag("NINGUNA_ANTERIORES_PORTAL_IVA", default=True)

    liv_cbte = os.getenv("LIV_CBTE_PORTAL_IVA", "liv_cbte.txt").strip().strip('"')
    liv_alicuota = os.getenv("LIV_ALICUOTA_PORTAL_IVA", "liv_alicuota.txt").strip().strip('"')
    lic_cbte = os.getenv("LIC_CBTE_PORTAL_IVA", "lic_cbte.txt").strip().strip('"')
    lic_alicuota = os.getenv("LIC_ALICUOTA_PORTAL_IVA", "lic_alicuota.txt").strip().strip('"')

    descarga_CSV_ventas = _env_flag("DESCARGA_CSV_VENTAS_PORTAL_IVA", default=False)
    descarga_CSV_compras = _env_flag("DESCARGA_CSV_COMPRAS_PORTAL_IVA", default=False)
    importar_txt_ventas = _env_flag("IMPORTAR_TXT_VENTAS_PORTAL_IVA", default=False)
    importar_txt_compras = _env_flag("IMPORTAR_TXT_COMPRAS_PORTAL_IVA", default=False)
    headless = _env_flag("HEADLESS_PORTAL_IVA", default=True)
    descartar_borrador = _env_flag("DESCARTAR_BORRADOR_PORTAL_IVA", default=True)

    print("Iniciando automatización Portal IVA Logger...")
    print(f"CUIT Representante: {cuit_representante}")
    print(f"CUIT Representado: {cuit_representado}")
    print(f"Período: {periodo}")
    print(f"Denominación: {denominacion}")
    print(f"Headless: {headless}")

    ok = asyncio.run(
        main(
            cuit_representante,
            cuit_representado,
            clave_representado,
            periodo,
            operaciones_ng_o_e,
            prorrateo_global,
            prorrateo_asignacion_directa,
            prorrateo_ambos,
            importacion_definitiva_bienes,
            importacion_servicios,
            regimen_turiva,
            bienes_usados,
            ninguna_anteriores,
            liv_cbte,
            liv_alicuota,
            lic_cbte,
            lic_alicuota,
            descarga_CSV_ventas,
            descarga_CSV_compras,
            importar_txt_ventas,
            importar_txt_compras,
            headless=headless,
            denominacion=denominacion,
            descartar_borrador=descartar_borrador,
        )
    )

    if ok:
        print("Proceso completado correctamente")
    else:
        print("Proceso finalizó con errores")
