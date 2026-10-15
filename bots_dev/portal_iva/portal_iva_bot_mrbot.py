import asyncio
import base64
import binascii
import os
import tempfile
import random
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

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

from shared.arca_login import (
    launch_arca_browser_context,
    login_arca,
    open_arca_service,
    wait_page_ready,
)
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser


BOT_NAME = "portal_iva"
DOWNLOAD_DIR = Path("./descargas")
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
PORTAL_IVA_DIRECT_URL = "https://siapweb.cloud.afip.gob.ar/iva"
PORTAL_IVA_INIT_URL = "https://siapweb.cloud.afip.gob.ar/iva/#/init"
PORTAL_IVA_URL_PATTERN = re.compile(r"(siapweb\.cloud\.afip\.gob\.ar/iva|liva\.afip\.gob\.ar)", re.I)
PORTAL_IVA_NO_ACCESS_PATTERN = re.compile(r"no posee activa la caracterizaci[oó]n requerida", re.I)
PORTAL_IVA_ERROR_NO_VALID_REPRESENTADOS = (
    "CUIT sin representados válidos con acceso a liquidaciones de IVA"
)
LIVA_BASE = "https://liva.afip.gob.ar/liva/jsp"
DOWNLOAD_DIR.mkdir(exist_ok=True)


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si"}


def _normalize_text_for_match(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_only = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return ascii_only.lower()


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


def _extract_available_period_values(periodos: list[dict[str, str]]) -> set[str]:
    values: set[str] = set()
    for item in periodos:
        normalized_value = _normalize_period_value(item.get("value", ""))
        normalized_label = _normalize_period_value(item.get("label", ""))
        if normalized_value:
            values.add(normalized_value)
        if normalized_label:
            values.add(normalized_label)
    return values


def _normalize_portal_iva_error_message(
    exc: Exception,
    config: "PortalIvaConfig",
    periodos_seleccionables: list[dict[str, str]],
) -> str:
    raw_message = (str(exc) or "").strip()
    normalized = _normalize_text_for_match(raw_message)

    requested_period = _normalize_period_value(config.periodo)
    available_periods = sorted(_extract_available_period_values(periodos_seleccionables))
    requested_period_available = bool(
        requested_period and available_periods and requested_period in available_periods
    )

    if "no se encuentra habilitado el servicio de portal iva" in normalized:
        return "No se encuentra habilitado el servicio de Portal IVA"

    if (
        "cuit sin representados validos con acceso a liquidaciones de iva" in normalized
        or "no posee activa la caracterizacion requerida" in normalized
    ):
        return PORTAL_IVA_ERROR_NO_VALID_REPRESENTADOS

    if (
        available_periods
        and requested_period
        and not requested_period_available
        and (
            "timeout" in normalized
            or "no se pudo abrir el libro" in normalized
            or "no se pudo validar el periodo" in normalized
            or "periodo" in normalized
            or "period" in normalized
        )
    ):
        return (
            f"Periodo invalido para Portal IVA: {requested_period}. "
            f"Periodos habilitados: {', '.join(available_periods)}"
        )

    if "no se pudo abrir el libro" in normalized:
        return "No se encuentra habilitado el servicio de Portal IVA"

    if "timeout" in normalized and not available_periods:
        return "No se encuentra habilitado el servicio de Portal IVA"

    return raw_message or "Error desconocido en Portal IVA"


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
    if compact == "<html><head></head><body></body></html>":
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


def _decode_base64_file_payload(
    payload: str,
    *,
    filename: str,
    output_dir: Path,
) -> Path:
    raw_payload = (payload or "").strip()
    if not raw_payload:
        raise ValueError(f"El archivo '{filename}' en base64 no puede estar vacio")

    if "," in raw_payload and "base64" in raw_payload[:80].lower():
        raw_payload = raw_payload.split(",", 1)[1]

    normalized_payload = re.sub(r"\s+", "", raw_payload)
    try:
        file_bytes = base64.b64decode(normalized_payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"El archivo '{filename}' no contiene un base64 valido") from exc

    file_path = output_dir / filename
    file_path.write_bytes(file_bytes)
    return file_path


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

    stealth_mode: bool = True
    proxy: Optional[dict[str, str]] = None
    output_dir: Path = DOWNLOAD_DIR
    periodos_seleccionables: list[dict[str, str]] = field(default_factory=list)


class RuntimeState:
    def __init__(self, descargas_dir: Path = DOWNLOAD_DIR) -> None:
        self.descargas_dir = descargas_dir
        self.periodos_seleccionables: list[dict[str, str]] = []


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


async def _human_pause(page: Page, ms: int = 1_500, jitter: int = 500) -> None:
    min_ms = max(300, ms - max(0, jitter))
    max_ms = max(min_ms, ms + max(0, jitter))
    await page.wait_for_timeout(random.randint(min_ms, max_ms))


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


async def return_to_home(page: Page) -> None:
    await ensure_session_active(page)
    await maybe_click_processed(page)
    cur_url = getattr(page, "url", "")

    # Si ya estamos en menuPresentacion, no hacer nada
    if "menuPresentacion" in cur_url:
        return

    # 1) Intentar navegación directa a menuPresentacion si estamos en LIVA
    if "liva.afip.gob.ar" in cur_url:
        try:
            await page.goto(f"{LIVA_BASE}/menuPresentacion.do", wait_until="domcontentloaded", timeout=15_000)
            await wait_page_ready(page, timeout_ms=10_000)
            if "menuPresentacion" in getattr(page, "url", ""):
                return
        except Exception:
            pass

    # 2) Intentar clics en elementos de navegación
    for locators in (
        [
            page.locator("a[href*='menuPresentacion.do']"),
            page.get_by_role("link", name=re.compile(r"Libro IVA - Borrador", re.I)),
            page.get_by_text("Libro IVA - Borrador", exact=False),
        ],
        [
            page.get_by_role("link", name=re.compile(r"Inicio", re.I)),
            page.get_by_text("Inicio", exact=False),
        ],
        [
            page.get_by_role("button", name=re.compile(r"Volver", re.I)),
            page.get_by_text("Volver", exact=False),
        ],
    ):
        if await click_first(locators, timeout=5_000):
            await _human_pause(page, 2_000, 600)
            return

    # 3) Último recurso: navegación directa
    try:
        await page.goto(f"{LIVA_BASE}/menuPresentacion.do", wait_until="domcontentloaded", timeout=15_000)
        await wait_page_ready(page, timeout_ms=10_000)
    except Exception:
        pass


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


async def open_book(page: Page, book: str) -> None:
    await ensure_session_active(page)
    pattern = re.compile(r"Libro Ventas", re.I) if book == "ventas" else re.compile(r"Libro Compras", re.I)
    href_fragment = "verVentas.do" if book == "ventas" else "verCompras.do"
    continue_pattern = (
        re.compile(r"Continuar al Libro Ventas", re.I)
        if book == "ventas"
        else re.compile(r"Continuar al Libro Compras", re.I)
    )
    registro_pattern = re.compile(
        rf"Libro\s+Ventas\s+Registr" if book == "ventas" else rf"Libro\s+Compras\s+Registr",
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
            await _human_pause(page, 2_000, 700)
            moved = True

        if await click_first(
            [
                page.locator("#btnGuardar:not([disabled])"),
                page.get_by_role("button", name=re.compile(r"Continuar", re.I)),
                page.get_by_text("Continuar", exact=False),
            ],
            timeout=8_000,
        ):
            await _human_pause(page, 2_000, 700)
            moved = True

        if await try_continue_ddjj(page):
            await _human_pause(page, 2_000, 700)
            moved = True

        return moved

    if await _open_from_current():
        await _human_pause(page, 3_000, 900)
        return

    if await _advance_to_book_context():
        if await _open_from_current():
            await _human_pause(page, 3_000, 900)
            return

    await return_to_home(page)
    if await _advance_to_book_context():
        if await _open_from_current():
            await _human_pause(page, 3_000, 900)
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


async def store_selectable_periods(page: Page, config: PortalIvaConfig, state: RuntimeState) -> list[dict[str, str]]:
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
        state.periodos_seleccionables = periodos

    return periodos


async def login_and_open_portal(playwright: Playwright, config: PortalIvaConfig) -> tuple[Browser, BrowserContext, Page]:
    launch_args: Optional[list[str]] = None
    context_kwargs: dict[str, Any] = {"accept_downloads": True}

    if config.stealth_mode:
        launch_args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ]
        context_kwargs.update(
            {
                "locale": "es-AR",
                "timezone_id": "America/Argentina/Buenos_Aires",
                "extra_http_headers": {"Accept-Language": "es-AR,es;q=0.9"},
            }
        )
        device = playwright.devices.get("Desktop Chrome")
        if device:
            context_kwargs.update(device)

    browser, context, page = await launch_arca_browser_context(
        playwright,
        headless=config.headless,
        proxy=config.proxy,
        launch_args=launch_args,
        context_kwargs=context_kwargs,
    )

    if config.stealth_mode:
        try:
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        except Exception:
            pass

    page.set_default_timeout(20_000)

    login_errors: list[str] = []
    login_result = await login_arca(
        page,
        config.cuit_representante,
        config.clave_representado,
        error_logs=login_errors,
        fail_on_captcha=True,
    )
    if login_result.captcha_detected:
        raise RuntimeError("Captcha detectado")
    if login_result.password_change_required:
        raise RuntimeError("Se requiere cambiar la clave fiscal")
    if not login_result.success:
        raise RuntimeError(login_errors[-1] if login_errors else "No se pudo iniciar sesión en ARCA")

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
    if portal_page is None:
        raise RuntimeError(open_errors[-1] if open_errors else "No se encuentra habilitado el servicio de Portal IVA")

    portal_page.set_default_timeout(20_000)
    try:
        await wait_page_ready(portal_page, timeout_ms=20_000)
        await portal_page.wait_for_url(PORTAL_IVA_URL_PATTERN, timeout=20_000)
    except PlaywrightTimeoutError:
        await portal_page.goto(PORTAL_IVA_DIRECT_URL, wait_until="domcontentloaded", timeout=20_000)
        await wait_page_ready(portal_page, timeout_ms=20_000)
        await portal_page.wait_for_url(PORTAL_IVA_URL_PATTERN, timeout=20_000)

    if await _is_blank_page(portal_page):
        init_url = _portal_iva_init_url_with_key()
        await portal_page.goto(init_url, wait_until="domcontentloaded", timeout=20_000)
        await wait_page_ready(portal_page, timeout_ms=20_000)
        if await _is_blank_page(portal_page):
            raise RuntimeError("No se pudo cargar Portal IVA")

    await _human_pause(portal_page, 4_000, 1_200)

    return browser, context, portal_page


async def select_representado(page: Page, config: PortalIvaConfig) -> None:
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

    await ensure_session_active(page)
    try:
        await wait_page_ready(page, timeout_ms=15_000)
    except Exception:
        pass
    await _human_pause(page, 1_500, 500)

    for _ in range(2):
        if not await _is_blank_page(page):
            break
        init_url = _portal_iva_init_url_with_key()
        await page.goto(init_url, wait_until="domcontentloaded", timeout=20_000)
        await wait_page_ready(page, timeout_ms=20_000)
        await _human_pause(page, 2_000, 700)

    if await _is_blank_page(page):
        raise RuntimeError("No se pudo cargar Portal IVA")

    if await _is_target_selected():
        return

    opened_switcher = await click_first(
        [
            page.get_by_title("cambio relación"),
            page.get_by_role("link", name=""),
            page.locator("[title*='cambio relación' i]"),
        ],
        timeout=8_000,
    )
    if opened_switcher:
        await _human_pause(page, 1_500, 500)

    # El portal lista un enlace title="Representar a..." por cada persona
    # representable (31 en una cuenta real). Hacer clic en el primero cambia
    # al representado equivocado, por eso se elige el que contiene el CUIT.
    selected = await click_first(
        [
            page.get_by_title("Representar a...").filter(has_text=formatted_cuit),
            page.get_by_title("Representar a...").filter(has_text=target_cuit_digits),
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
    await _human_pause(page, 1_000, 400)

    selected = selected or await click_first(
        [
            page.get_by_role("button", name=formatted_cuit),
            page.get_by_text(formatted_cuit, exact=False),
            page.get_by_text(config.cuit_representado, exact=False),
        ],
        timeout=8_000,
    )
    if selected:
        await _human_pause(page, 2_000, 700)

    try:
        await page.get_by_text(PORTAL_IVA_NO_ACCESS_PATTERN).first.wait_for(
            state="visible",
            timeout=2_000,
        )
    except Exception:
        pass

    current_html = await page.content()
    for _ in range(2):
        if not _looks_like_blank_html(current_html):
            break
        init_url = _portal_iva_init_url_with_key()
        await page.goto(init_url, wait_until="domcontentloaded", timeout=20_000)
        await wait_page_ready(page, timeout_ms=20_000)
        await _human_pause(page, 2_000, 700)
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
        await _human_pause(page, 1_500, 500)
        if await _is_target_selected():
            return

    if not selected and not target_present_in_options:
        raise RuntimeError(PORTAL_IVA_ERROR_NO_VALID_REPRESENTADOS)

    raise RuntimeError(f"No se pudo seleccionar el representado {formatted_cuit}")


async def prepare_period(page: Page, config: PortalIvaConfig, state: RuntimeState) -> None:
    await ensure_session_active(page)

    current_url = page.url
    if "#/changeRelation" in current_url or "changeRelation" in current_url:
        init_url = _portal_iva_init_url_with_key()
        await page.goto(init_url, wait_until="domcontentloaded", timeout=20_000)
        await wait_page_ready(page, timeout_ms=15_000)
        await _human_pause(page, 2_000, 700)

    opened_nueva = await click_first(
        [
            page.get_by_role("button", name=re.compile(r"iva\.home\.btn\.nueva", re.I)),
            page.get_by_label("Sin texto (iva.home.btn.nueva"),
            page.get_by_text(re.compile(r"Nueva", re.I)),
        ],
        timeout=12_000,
    )
    if opened_nueva:
        await _human_pause(page, 2_000, 700)
        await store_selectable_periods(page, config, state)

    if await page.locator("#periodo").count() > 0:
        await store_selectable_periods(page, config, state)
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
        await _human_pause(page, 2_000, 700)

    opened_liva = await click_first(
        [
            page.get_by_role("button", name=re.compile(r"iva\.btn\.home\.liva", re.I)),
            page.get_by_label("Sin texto (iva.btn.home.liva."),
        ],
        timeout=10_000,
    )

    if opened_liva:
        await _human_pause(page, 1_500, 500)
    await store_selectable_periods(page, config, state)
    # El formulario "Datos iniciales" se renderiza tarde: sin esta espera el
    # bot lo daba por ausente, no marcaba nada y nunca llegaba a "Continuar".
    try:
        await page.locator("#operacionesNoGravadasExentas").first.wait_for(state="attached", timeout=20_000)
    except Exception:
        pass

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
        await _human_pause(page, 1_500, 500)

        guardar = page.locator("#btnGuardar").first
        if await guardar.count() > 0:
            await page.wait_for_function(
                "() => { const btn = document.querySelector('#btnGuardar'); return btn && !btn.disabled; }",
                timeout=8_000,
            )
            await guardar.click(timeout=10_000)
            await _human_pause(page, 4_000, 1_200)
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
    await _human_pause(page, 4_000, 1_200)


async def import_from_arca(page: Page, book: str) -> None:
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

    await _human_pause(page, 2_000, 700)
    if await page.locator("#btnImportarAFIPImportar").count() == 0:
        raise RuntimeError(f"No se encontró el botón de importación ARCA para {book}")

    await page.locator("#btnImportarAFIPImportar").click()
    await _human_pause(page, 8_000, 1_500)
    await maybe_click_processed(page)
    await maybe_close_modal(page)
    await _human_pause(page, 3_000, 1_000)


async def delete_all_records(page: Page, book: str) -> None:
    await ensure_session_active(page)
    deleted = await click_first(
        [
            page.get_by_role("button", name="Eliminar Todos..."),
            page.get_by_text("Eliminar Todos...", exact=False),
        ],
        timeout=8_000,
    )
    if not deleted:
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
        await maybe_close_modal(page)
        return
    await _human_pause(page, 3_000, 1_000)


async def download_csv(page: Page, state: RuntimeState, config: PortalIvaConfig, book: str) -> Path:
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
            state.descargas_dir,
            config.cuit_representado,
            config.denominacion,
            book,
            config.periodo,
            download.suggested_filename,
        )
        await download.save_as(str(final_path))
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


async def import_files(
    page: Page,
    book: str,
    comprobantes_path: str,
    alicuotas_path: str,
    comprobantes_label: str,
    alicuotas_label: str,
) -> None:
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
    await _human_pause(page, 2_000, 700)

    await page.get_by_label(alicuotas_label).set_input_files(alicuotas_path)
    await page.locator("#btnImportarArchivosImportar").click()
    await _human_pause(page, 2_000, 700)

    await page.locator("#btnImportarArchivosImportar").click()
    await _human_pause(page, 5_000, 1_200)
    await maybe_click_processed(page)
    await maybe_close_modal(page)
    await _human_pause(page, 3_000, 1_000)


async def ejecutar_descarga(page: Page, config: PortalIvaConfig, state: RuntimeState) -> dict[str, Optional[Path]]:
    resultados: dict[str, Optional[Path]] = {"ventas": None, "compras": None}

    if config.descarga_CSV_ventas:
        await open_book(page, "ventas")
        try:
            await import_from_arca(page, "ventas")
        except Exception:
            pass
        resultados["ventas"] = await download_csv(page, state, config, "ventas")
        await delete_all_records(page, "ventas")
        await return_to_home(page)

    if config.descarga_CSV_compras:
        await open_book(page, "compras")
        try:
            await import_from_arca(page, "compras")
        except Exception:
            pass
        resultados["compras"] = await download_csv(page, state, config, "compras")
        await delete_all_records(page, "compras")
        await return_to_home(page)

    return resultados


async def ejecutar_carga(page: Page, config: PortalIvaConfig) -> dict[str, bool]:
    resultados = {"ventas": False, "compras": False}

    if config.importar_txt_ventas:
        await open_book(page, "ventas")
        await delete_all_records(page, "ventas")
        await import_files(
            page,
            "ventas",
            config.liv_cbte,
            config.liv_alicuota,
            "Archivo Ventas *",
            "Archivo IVA Ventas *",
        )
        resultados["ventas"] = True
        await return_to_home(page)

    if config.importar_txt_compras:
        await open_book(page, "compras")
        await delete_all_records(page, "compras")
        await import_files(
            page,
            "compras",
            config.lic_cbte,
            config.lic_alicuota,
            "Archivo Compras *",
            "Archivo IVA Compras *",
        )
        resultados["compras"] = True
        await maybe_close_modal(page)
        await delete_all_records(page, "compras")
        await return_to_home(page)

    return resultados


async def ejecutar_descartar_borrador(page: Page) -> bool:
    """Descarta el borrador de declaracion que el propio bot creo en el periodo.

    Flujo verificado en vivo: menu de presentacion -> "Vista previa" -> boton
    ``#btnEliminarBorrador`` -> modal "Descartar Borrador" -> "DESCARTAR BORRADOR".
    Solo se llama despues de terminar todas las descargas.
    """
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
    for _intento in range(1, 5):
        if not await click_first([page.locator("#btnEliminarBorrador")], timeout=10_000):
            raise RuntimeError("No se pudo hacer clic en 'btnEliminarBorrador'")
        try:
            await boton_confirmar.first.wait_for(state="visible", timeout=6_000)
            break
        except Exception:
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
    await page.wait_for_timeout(4_000)
    # El borrador descartado deja de existir: el boton ya no debe estar visible.
    if await is_visible(page.locator("#btnEliminarBorrador"), timeout=2_000):
        raise RuntimeError("El borrador sigue presente tras confirmar el descarte")
    return True


async def execute_with_config(
    playwright: Playwright,
    config: PortalIvaConfig,
) -> tuple[dict[str, Any], list[str], bool]:
    temp_download_dir = tempfile.TemporaryDirectory(prefix="portal_iva_downloads_")
    state = RuntimeState(Path(temp_download_dir.name))
    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    error_logs: list[str] = []
    results: dict[str, Any] = {
        "archivos": [],
        "importaciones": [],
        "periodos_seleccionables": [],
    }

    def _sync_periodos_seleccionables() -> None:
        results["periodos_seleccionables"] = (
            state.periodos_seleccionables or config.periodos_seleccionables
        )

    try:
        browser, context, portal_page = await login_and_open_portal(playwright, config)
        await select_representado(portal_page, config)
        await prepare_period(portal_page, config, state)

        descargas: dict[str, Optional[Path]] = {}
        cargas: dict[str, bool] = {}

        if config.descarga_CSV_ventas or config.descarga_CSV_compras:
            descargas = await ejecutar_descarga(portal_page, config, state)
            for libro, path in descargas.items():
                if path is None:
                    continue
                archivo_info: dict[str, Optional[str]] = {
                    "libro": libro,
                    "archivo": path.name,
                    "archivo_path": None,
                }
                results["archivos"].append(archivo_info)

        if config.importar_txt_ventas or config.importar_txt_compras:
            cargas = await ejecutar_carga(portal_page, config)
            for libro, ok in cargas.items():
                if libro == "ventas" and not config.importar_txt_ventas:
                    continue
                if libro == "compras" and not config.importar_txt_compras:
                    continue
                results["importaciones"].append({"libro": libro, "success": bool(ok)})

        _sync_periodos_seleccionables()

        ok_descargas = (
            (not config.descarga_CSV_ventas or descargas.get("ventas") is not None)
            and (not config.descarga_CSV_compras or descargas.get("compras") is not None)
        )
        ok_cargas = (
            (not config.importar_txt_ventas or cargas.get("ventas") is True)
            and (not config.importar_txt_compras or cargas.get("compras") is True)
        )
        success = ok_descargas and ok_cargas
        # Solo se descarta cuando TODAS las descargas pedidas terminaron bien: si algo
        # fallo, el borrador se conserva para poder inspeccionarlo.
        if config.descartar_borrador and success and (config.descarga_CSV_ventas or config.descarga_CSV_compras):
            await ejecutar_descartar_borrador(portal_page)
        return results, error_logs, success
    except Exception as exc:
        _sync_periodos_seleccionables()
        normalized_error = _normalize_portal_iva_error_message(
            exc,
            config,
            results.get("periodos_seleccionables") or [],
        )
        error_logs.append(f"Error Portal IVA: {normalized_error}")
        return results, error_logs, False
    finally:
        _sync_periodos_seleccionables()
        if context is not None:
            try:
                await context.close()
            except Exception:
                pass
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        temp_download_dir.cleanup()


async def bot_portal_iva(
    cuit_representante: str,
    cuit_representado: Optional[str],
    clave_representado: str,
    periodo: str,
    operaciones_ng_o_e: bool = False,
    prorrateo_global: bool = False,
    prorrateo_asignacion_directa: bool = False,
    prorrateo_ambos: bool = False,
    importacion_definitiva_bienes: bool = False,
    importacion_servicios: bool = False,
    regimen_turiva: bool = False,
    bienes_usados: bool = False,
    ninguna_anteriores: bool = True,
    liv_cbte: str = "",
    liv_alicuota: str = "",
    lic_cbte: str = "",
    lic_alicuota: str = "",
    descarga_CSV_ventas: bool = False,
    descarga_CSV_compras: bool = False,
    importar_txt_ventas: bool = False,
    importar_txt_compras: bool = False,
    denominacion: str = "",

    proxy_request: bool = False,
    headless: bool = True,
    descartar_borrador: bool = True,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {
        "archivos": [],
        "importaciones": [],
        "periodos_seleccionables": [],
    }
    error_logs: list[str] = []

    cuit_objetivo = (cuit_representado or cuit_representante or "").strip()

    if not (cuit_representante or "").strip():
        error_logs.append("El CUIT del representante es obligatorio")
        return results, error_logs, False
    if not (clave_representado or "").strip():
        error_logs.append("La clave fiscal es obligatoria")
        return results, error_logs, False
    if not cuit_objetivo:
        error_logs.append("El CUIT del representado es obligatorio")
        return results, error_logs, False
    if not (periodo or "").strip():
        error_logs.append("El periodo es obligatorio")
        return results, error_logs, False
    if not any(
        [
            descarga_CSV_ventas,
            descarga_CSV_compras,
            importar_txt_ventas,
            importar_txt_compras,
        ]
    ):
        error_logs.append(
            "Debe seleccionar al menos una accion: descargar CSV de ventas/compras o importar TXT de ventas/compras."
        )
        return results, error_logs, False

    proxy = get_proxy_config() if proxy_request else None

    input_temp_dir = None

    liv_cbte_path = ""
    liv_alicuota_path = ""
    lic_cbte_path = ""
    lic_alicuota_path = ""

    if importar_txt_ventas or importar_txt_compras:
        input_temp_dir = tempfile.TemporaryDirectory(prefix="portal_iva.inputs_")
        input_dir = Path(input_temp_dir.name)

        archivos_requeridos: list[tuple[str, str, str]] = []
        if importar_txt_ventas:
            archivos_requeridos.extend(
                [
                    ("liv_cbte", liv_cbte, "liv_cbte.txt"),
                    ("liv_alicuota", liv_alicuota, "liv_alicuota.txt"),
                ]
            )
        if importar_txt_compras:
            archivos_requeridos.extend(
                [
                    ("lic_cbte", lic_cbte, "lic_cbte.txt"),
                    ("lic_alicuota", lic_alicuota, "lic_alicuota.txt"),
                ]
            )

        decoded_files: dict[str, str] = {}
        for label, payload, filename in archivos_requeridos:
            try:
                decoded_path = _decode_base64_file_payload(
                    payload,
                    filename=filename,
                    output_dir=input_dir,
                )
                decoded_files[label] = str(decoded_path)
            except ValueError as exc:
                error_logs.append(str(exc))

        if error_logs:
            return results, error_logs, False

        liv_cbte_path = decoded_files.get("liv_cbte", "")
        liv_alicuota_path = decoded_files.get("liv_alicuota", "")
        lic_cbte_path = decoded_files.get("lic_cbte", "")
        lic_alicuota_path = decoded_files.get("lic_alicuota", "")

    config = PortalIvaConfig(
        cuit_representante=(cuit_representante or "").strip(),
        cuit_representado=cuit_objetivo,
        clave_representado=(clave_representado or "").strip(),
        periodo=(periodo or "").strip(),
        operaciones_ng_o_e=operaciones_ng_o_e,
        prorrateo_global=prorrateo_global,
        prorrateo_asignacion_directa=prorrateo_asignacion_directa,
        prorrateo_ambos=prorrateo_ambos,
        importacion_definitiva_bienes=importacion_definitiva_bienes,
        importacion_servicios=importacion_servicios,
        regimen_turiva=regimen_turiva,
        bienes_usados=bienes_usados,
        ninguna_anteriores=ninguna_anteriores,
        liv_cbte=liv_cbte_path,
        liv_alicuota=liv_alicuota_path,
        lic_cbte=lic_cbte_path,
        lic_alicuota=lic_alicuota_path,
        descarga_CSV_ventas=descarga_CSV_ventas,
        descarga_CSV_compras=descarga_CSV_compras,
        importar_txt_ventas=importar_txt_ventas,
        importar_txt_compras=importar_txt_compras,
        denominacion=(denominacion or "").strip(),
        headless=headless,
        descartar_borrador=descartar_borrador,

        stealth_mode=_env_flag("PORTAL_IVA_STEALTH", False),
        proxy=proxy,
        output_dir=DOWNLOAD_DIR / BOT_NAME,
    )

    try:
        async with limit_browser(), async_playwright() as playwright:
            return await execute_with_config(playwright, config)
    finally:
        if input_temp_dir is not None:
            input_temp_dir.cleanup()
