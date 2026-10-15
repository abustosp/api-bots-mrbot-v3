"""
Bot logger: descarga de sueldos Xubio.

Flujo:
1. Login en Visma Connect (connect.visma.com) con usuario y contraseña.
2. Selección de contribuyente (empresa) si difiere de la empresa activa.
3. Libro de Sueldos Digital: exportar TXT de liquidaciones y TXT de conceptos
   para el período de liquidación indicado.
4. Reporte de Sueldos: exportar Excel del reporte y Excel de control de liquidación
   para el mismo período.

Variables de entorno (`.env`):
- XUBIO_SUELDOS_USUARIO
- XUBIO_SUELDOS_CLAVE
- XUBIO_SUELDOS_CONTRIBUYENTE   (nombre de una empresa; si vacío usa la empresa activa)
- XUBIO_SUELDOS_CONTRIBUYENTES  (lista de empresas separadas por | ; o ,; si se setea, itera sobre todas)
- XUBIO_SUELDOS_PERIODO        (ej. 202607)
- XUBIO_SUELDOS_NRO_LIQUIDACION (entero 0-10, default 0)
- XUBIO_SUELDOS_HEADLESS       (1/true/si -> headless, default true)
- XUBIO_SUELDOS_PROXY          (1/true/si -> usa proxy config de proxy.py)

Estructura de evidencias por ejecución: ./temp/xubio_sueldos_{YYYYMMDD_HHMMSS}/
  - logs/, screenshots/, html/, har/, descargas/
"""

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field, replace
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
    async_playwright,
)

from shared.arca_login import launch_arca_browser_context
from shared.proxy import get_proxy_config


BOT_NAME = "xubio_sueldos"
DOWNLOAD_DIR = Path("./descargas")
TEMP_DIR = Path("./temp")
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')

XUBIO_HOME_URL = "https://xubio.com/NXV/home"
XUBIO_VISMA_LOGIN_URL = "https://xubio.com/NXV/vismaConnect/login"
XUBIO_LIBRO_SUELDOS_DIGITAL_URL = "https://xubio.com/NXV/sueldos/informes/afip-libro-sueldos-digital"
XUBIO_REPORTE_SUELDOS_URL = "https://xubio.com/NXV/sueldos/informes/reporte-sueldos"
VISMA_LOGIN_HOST = "connect.visma.com"

DOWNLOAD_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)


# ---------------------------------------------------------------------------
# Helpers generales
# ---------------------------------------------------------------------------

def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si"}


def _format_periodo_xubio(periodo: str) -> str:
    """202607 -> 2026-07 ; 2026-07 -> 2026-07 ; 2026 -> 2026."""
    clean = re.sub(r"\D", "", periodo or "")
    if len(clean) == 6:
        return f"{clean[:4]}-{clean[4:]}"
    if len(clean) == 4:
        return clean
    return (periodo or "").strip()


def _format_nro_liquidacion(nro: int) -> str:
    """0 -> '00', 7 -> '07', 10 -> '10'."""
    return f"{int(nro):02d}"


def _normalize_empresa(nombre: str) -> str:
    return re.sub(r"\s+", " ", (nombre or "").strip()).upper()


def _sanitize_empresa(nombre: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", _normalize_empresa(nombre))
    return re.sub(r"\s+", "_", cleaned).strip("_")


def _build_xubio_filename(
    base_dir: Path,
    tipo: str,
    empresa: str,
    periodo: str,
    suggested_filename: str,
) -> Path:
    suffix = Path(suggested_filename or "").suffix or (".txt" if "TXT" in tipo else ".xls")
    empresa_value = _sanitize_empresa(empresa) or "SIN_EMPRESA"
    periodo_value = re.sub(r"\D", "", periodo or "") or "SIN_PERIODO"
    filename = "_".join([tipo, periodo_value, empresa_value])
    return base_dir / f"{filename}{suffix}"


def is_visma_login_page(page: Page) -> bool:
    return VISMA_LOGIN_HOST in (page.url or "")


async def is_xubio_login_page(page: Page) -> bool:
    """Detecta el login clásico de Xubio (formulario con userName)."""
    if "xubio.com" not in (page.url or ""):
        return False
    try:
        return await page.locator("#userName").count() > 0
    except Exception:
        return False


async def ensure_xubio_session(page: Page) -> None:
    if is_visma_login_page(page):
        raise RuntimeError("La sesión de Xubio expiró, se requiere volver a iniciar sesión")
    if await is_xubio_login_page(page):
        raise RuntimeError("La sesión de Xubio expiró (login clásico detectado)")


# ---------------------------------------------------------------------------
# DebugCapture (patrón del repo)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Helpers de UI
# ---------------------------------------------------------------------------

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


async def _wait_for_count(locator: Locator, target: int = 1, timeout: int = 30_000) -> bool:
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        try:
            if await locator.count() >= target:
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


async def _click_robust(locator: Locator, timeout: int = 10_000) -> bool:
    """Click normal con fallback forzado (modales/overlays de Angular)."""
    if await try_click(locator, timeout=timeout):
        return True
    try:
        await locator.first.click(force=True, timeout=5_000)
        return True
    except Exception:
        return False


async def _close_modals(page: Page, frame) -> None:
    """Cierra los modales/anuncios de Xubio (✕, Cerrar/Close, Escape)."""
    x = frame.get_by_role("button", name="✕")
    close = frame.get_by_role("button", name=re.compile(r"Cerrar|Close", re.I))
    for _ in range(4):
        closed = False
        if await x.count():
            try:
                await x.first.click(force=True, timeout=3_000)
                closed = True
            except Exception:
                try:
                    await x.first.dispatch_event("click")
                    closed = True
                except Exception:
                    pass
        if not closed and await close.count():
            try:
                await close.first.click(force=True, timeout=3_000)
                closed = True
            except Exception:
                try:
                    await close.first.dispatch_event("click")
                    closed = True
                except Exception:
                    pass
        if closed:
            await page.wait_for_timeout(800)
            continue
        # sin botones de cierre: probar Escape y salir si no hay overlays
        try:
            if await frame.locator(".cdk-overlay-container").count() == 0:
                return
        except Exception:
            return
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class XubioSueldosConfig:
    usuario: str
    clave: str
    contribuyente: str = ""
    contribuyentes: list[str] = field(default_factory=list)
    periodo: str = ""
    nro_liquidacion: int = 0
    headless: bool = True
    proxy: bool = False


def _nro_liquidacion_from_env() -> int:
    raw = os.getenv("XUBIO_SUELDOS_NRO_LIQUIDACION", "").strip()
    if not raw:
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise ValueError("XUBIO_SUELDOS_NRO_LIQUIDACION debe ser un entero entre 0 y 10")
    if not 0 <= value <= 10:
        raise ValueError("XUBIO_SUELDOS_NRO_LIQUIDACION debe estar entre 0 y 10")
    return value


def _contribuyentes_from_env() -> list[str]:
    raw_lista = os.getenv("XUBIO_SUELDOS_CONTRIBUYENTES", "").strip()
    if raw_lista:
        return [c.strip() for c in re.split(r"[|;,]", raw_lista) if c.strip()]
    single = os.getenv("XUBIO_SUELDOS_CONTRIBUYENTE", "").strip()
    return [single] if single else []


def _load_config_from_env() -> XubioSueldosConfig:
    return XubioSueldosConfig(
        usuario=os.getenv("XUBIO_SUELDOS_USUARIO", "").strip(),
        clave=os.getenv("XUBIO_SUELDOS_CLAVE", "").strip(),
        contribuyente=os.getenv("XUBIO_SUELDOS_CONTRIBUYENTE", "").strip(),
        contribuyentes=_contribuyentes_from_env(),
        periodo=os.getenv("XUBIO_SUELDOS_PERIODO", "").strip(),
        nro_liquidacion=_nro_liquidacion_from_env(),
        headless=_env_flag("XUBIO_SUELDOS_HEADLESS", default=True),
        proxy=_env_flag("XUBIO_SUELDOS_PROXY", default=False),
    )


# ---------------------------------------------------------------------------
# Pasos del flujo
# ---------------------------------------------------------------------------

async def _wait_back_to_xubio(page: Page, debug: DebugCapture, timeout_s: int = 40) -> None:
    for _ in range(timeout_s):
        if not is_visma_login_page(page):
            return
        await page.wait_for_timeout(1_000)
    raise RuntimeError("Timeout esperando la sesión de Xubio tras el login Visma")


async def _handle_active_session(page: Page, debug: DebugCapture) -> bool:
    """Xubio muestra 'Tu cuenta ya tiene una sesión activa' -> click en Continuar inicio."""
    button = page.get_by_role("button", name="Continuar inicio")
    if not await is_visible(button, timeout=4_000):
        return False
    debug.log("active_session_detected url=%s", page.url)
    try:
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
            await button.click()
    except Exception as exc:
        debug.log_error("active_session_continue_error error=%s", exc)
    await page.wait_for_timeout(2_000)
    return True


async def login_visma(page: Page, debug: DebugCapture, config: XubioSueldosConfig) -> None:
    """Entra por el flujo OAuth de Xubio hacia Visma Connect y completa el login."""
    await page.goto(XUBIO_VISMA_LOGIN_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2_500)

    if not is_visma_login_page(page):
        if await is_xubio_login_page(page):
            raise RuntimeError("Xubio requiere login clásico (no Visma Connect)")
        debug.log("visma_login_skipped session_exists url=%s", page.url)
        await ensure_xubio_session(page)
        return

    debug.log("visma_login_start url=%s", page.url)
    await try_click(page.get_by_role("button", name="Rechazarlas todas"), timeout=5_000)

    email_box = page.get_by_role("textbox", name="Email")
    await email_box.fill(config.usuario)
    await page.get_by_role("button", name="Continuar").click()
    await page.wait_for_timeout(1_000)

    password_box = page.get_by_role("textbox", name="Contraseña")
    await password_box.fill(config.clave)
    await page.get_by_role("button", name="Iniciar sesión").click()

    await _wait_back_to_xubio(page, debug)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(1_000)

    await _handle_active_session(page, debug)
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_timeout(1_000)

    if is_visma_login_page(page):
        raise RuntimeError("No se pudo iniciar sesión en Xubio (Visma Connect)")
    if await is_xubio_login_page(page):
        raise RuntimeError("El login Visma no completó la sesión en Xubio")
    debug.log("visma_login_ok url=%s", page.url)


async def _leer_empresa_activa(page: Page) -> str:
    try:
        loc = page.locator("#resaltarCompania")
        if await loc.count() > 0:
            text = (await loc.first.inner_text()).strip()
            if text:
                return text
    except Exception:
        pass
    try:
        loc = page.locator("li.compania a span").last
        if await loc.count() > 0:
            text = (await loc.inner_text()).strip()
            if text:
                return text
    except Exception:
        pass
    return ""


async def seleccionar_contribuyente(page: Page, debug: DebugCapture, config: XubioSueldosConfig) -> str:
    """Devuelve el nombre efectivo de la empresa con la que se trabajará."""
    await page.goto(XUBIO_HOME_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(1_500)
    await _handle_active_session(page, debug)
    await ensure_xubio_session(page)

    empresa_activa = await _leer_empresa_activa(page)
    debug.log("empresa_activa=%s", empresa_activa)

    objetivo = _normalize_empresa(config.contribuyente) if config.contribuyente else ""
    if objetivo and empresa_activa and _normalize_empresa(empresa_activa) == objetivo:
        debug.log("contribuyente_ya_activo empresa=%s", empresa_activa)
        return _normalize_empresa(empresa_activa)

    if not objetivo:
        debug.log("contribuyente_sin_variable usa_activa=%s", empresa_activa)
        return _normalize_empresa(empresa_activa)

    compania = page.locator("li.compania")
    if await is_visible(compania, timeout=3_000):
        try:
            await compania.first.hover()
        except Exception:
            await try_click(compania.first, timeout=3_000)
        await page.wait_for_timeout(800)

    buscador = page.locator("#buscador-empresa")
    if await is_visible(buscador, timeout=3_000):
        await buscador.fill(objetivo)
        await page.wait_for_timeout(800)

    link = page.locator("li.licomp a").filter(has_text=objetivo).first
    if await is_visible(link, timeout=3_000):
        async with page.expect_navigation(wait_until="domcontentloaded", timeout=20_000):
            await link.click()
        await page.wait_for_timeout(1_000)
        debug.log("contribuyente_seleccionado empresa=%s url=%s", objetivo, page.url)
        return objetivo

    raise RuntimeError(f"No se encuentra el contribuyente {config.contribuyente}")


async def _set_periodo_mensual(page: Page, frame, periodo: str) -> None:
    """Rellena el rango de Periodo Mensual (inputs con placeholder)."""
    candidates = [
        frame.get_by_placeholder("Periodo Mensual"),
        frame.get_by_role("textbox", name="Periodo Mensual"),
    ]
    for fields in candidates:
        n = await fields.count()
        for i in range(n):
            try:
                await fields.nth(i).click()
                await fields.nth(i).fill(periodo)
                await fields.nth(i).press("Enter")
                await page.wait_for_timeout(600)
            except Exception:
                pass
        for i in range(n):
            try:
                if periodo in (await fields.nth(i).input_value()):
                    return
            except Exception:
                pass
    raise RuntimeError(f"No se pudo establecer el período mensual {periodo}")


async def abrir_libro_sueldos_digital(page: Page, debug: DebugCapture, periodo_xubio: str) -> None:
    await ensure_xubio_session(page)
    await page.goto(XUBIO_LIBRO_SUELDOS_DIGITAL_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2_000)
    await _handle_active_session(page, debug)
    frame = page.frame_locator("#iframe")

    if not await _wait_for_count(frame.get_by_role("button", name="Exportar TXT Liquidaciones"), timeout=30_000):
        raise RuntimeError("No cargó la pantalla de Libro de Sueldos Digital")
    await _close_modals(page, frame)
    await _set_periodo_mensual(page, frame, periodo_xubio)


async def exportar_txt_liquidaciones(
    page: Page,
    debug: DebugCapture,
    config: XubioSueldosConfig,
    empresa: str,
) -> Path:
    frame = page.frame_locator("#iframe")
    await ensure_xubio_session(page)
    await _close_modals(page, frame)

    btn = frame.get_by_role("button", name="Exportar TXT Liquidaciones")
    if not await _wait_for_count(btn, timeout=30_000):
        raise RuntimeError("No se encontró el botón Exportar TXT Liquidaciones")
    if not await _click_robust(btn, timeout=10_000):
        raise RuntimeError("No se pudo clickear Exportar TXT Liquidaciones")
    await page.wait_for_timeout(1_000)

    nro = _format_nro_liquidacion(config.nro_liquidacion)
    spin = frame.get_by_role("spinbutton", name="Número de Liquidación AFIP")
    if not await _wait_for_count(spin, timeout=15_000):
        raise RuntimeError("No se encontró el campo Número de Liquidación AFIP")
    await _close_modals(page, frame)
    await _click_robust(spin, timeout=10_000)
    try:
        await spin.first.fill(nro)
    except Exception:
        await spin.first.click(force=True)
        await spin.first.fill(nro)
    debug.log("nro_liquidacion_afip=%s", nro)
    await page.wait_for_timeout(300)

    async with page.expect_download(timeout=45_000) as download_info:
        exp = frame.get_by_role("button", name="Exportar", exact=True)
        if not await _wait_for_count(exp, timeout=10_000):
            raise RuntimeError("No se encontró el botón Exportar de liquidaciones")
        if not await _click_robust(exp, timeout=10_000):
            raise RuntimeError("No se pudo clickear Exportar de liquidaciones")

    download = await download_info.value
    final_path = _build_xubio_filename(
        debug.descargas_dir, "TXT_liquidaciones", empresa, config.periodo, download.suggested_filename
    )
    await download.save_as(str(final_path))
    debug.log("download_saved tipo=TXT_liquidaciones path=%s", final_path)
    return final_path


async def exportar_txt_conceptos(
    page: Page,
    debug: DebugCapture,
    config: XubioSueldosConfig,
    empresa: str,
) -> Path:
    frame = page.frame_locator("#iframe")
    await ensure_xubio_session(page)
    await _close_modals(page, frame)

    async with page.expect_download(timeout=45_000) as download_info:
        btn = frame.get_by_role("button", name="Exportar TXT Conceptos")
        if not await _wait_for_count(btn, timeout=30_000):
            raise RuntimeError("No se encontró el botón Exportar TXT Conceptos")
        if not await _click_robust(btn, timeout=10_000):
            raise RuntimeError("No se pudo clickear Exportar TXT Conceptos")

    download = await download_info.value
    final_path = _build_xubio_filename(
        debug.descargas_dir, "TXT_conceptos", empresa, config.periodo, download.suggested_filename
    )
    await download.save_as(str(final_path))
    debug.log("download_saved tipo=TXT_conceptos path=%s", final_path)
    return final_path


async def _set_periodo_via_calendar(page: Page, frame, label: str, periodo: str) -> None:
    year, month = periodo.split("-")
    month_btn = f"-{month}"

    form_field = frame.locator("mat-form-field").filter(has_text=label)
    opened = await click_first(
        [
            form_field.get_by_label("Open calendar"),
            frame.get_by_label("Open calendar").first,
        ],
        timeout=4_000,
    )
    if not opened:
        raise RuntimeError(f"No se pudo abrir el calendario de {label}")
    await page.wait_for_timeout(800)

    chooser = frame.get_by_role("button", name="Choose month and year")
    if await is_visible(chooser, timeout=2_000):
        await chooser.click()
        await page.wait_for_timeout(500)
        year_loc = frame.get_by_role("button", name=year)
        if await is_visible(year_loc, timeout=2_000):
            await year_loc.click()
            await page.wait_for_timeout(500)
            month_loc = frame.get_by_role("button", name=month_btn)
            if await is_visible(month_loc, timeout=2_000):
                await month_loc.click()
                await page.wait_for_timeout(500)
                return

    # Fallback: navegar con "Next 24 years" (patrón del draft)
    for _ in range(6):
        next_btn = frame.get_by_role("button", name="Next 24 years")
        if await is_visible(next_btn, timeout=1_500):
            await next_btn.click()
            await page.wait_for_timeout(400)
        year_loc = frame.get_by_role("button", name=year)
        if await is_visible(year_loc, timeout=1_500):
            await year_loc.click()
            await page.wait_for_timeout(400)
            month_loc = frame.get_by_role("button", name=month_btn)
            if await is_visible(month_loc, timeout=1_500):
                await month_loc.click()
                await page.wait_for_timeout(400)
                return

    raise RuntimeError(f"No se pudo seleccionar el periodo {periodo} en {label}")


async def _set_periodo_reporte(page: Page, frame, label: str, periodo: str) -> None:
    field = frame.locator("mat-form-field").filter(has_text=label).locator('input[type="month"]')
    if await field.count() == 0:
        field = frame.get_by_role("textbox", name=label)
    for _ in range(3):
        try:
            await field.first.click()
            await field.first.fill(periodo)
            await field.first.press("Enter")
            await page.wait_for_timeout(700)
            value = (await field.first.input_value()).strip()
            if periodo in value:
                return
        except Exception:
            await page.wait_for_timeout(500)
    raise RuntimeError(f"No se pudo establecer el período {label} = {periodo}")


async def abrir_reporte_sueldos(page: Page, debug: DebugCapture, periodo_xubio: str) -> None:
    await ensure_xubio_session(page)
    await page.goto(XUBIO_REPORTE_SUELDOS_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(2_000)
    await _handle_active_session(page, debug)
    frame = page.frame_locator("#iframe")

    filtros = frame.get_by_role("button", name="Filtros")
    if not await _wait_for_count(filtros, timeout=30_000):
        raise RuntimeError("No se encontró el botón Filtros del Reporte de Sueldos")
    await _close_modals(page, frame)
    if not await _click_robust(filtros, timeout=10_000):
        raise RuntimeError("No se pudo clickear Filtros del Reporte de Sueldos")
    await page.wait_for_timeout(800)

    await _set_periodo_reporte(page, frame, "Periodo de Liquidación Desde", periodo_xubio)
    await _set_periodo_reporte(page, frame, "Periodo de Liquidación Hasta", periodo_xubio)


async def exportar_excel_reporte(
    page: Page,
    debug: DebugCapture,
    config: XubioSueldosConfig,
    empresa: str,
) -> Path:
    frame = page.frame_locator("#iframe")
    await ensure_xubio_session(page)
    await _close_modals(page, frame)

    exp = frame.get_by_role("button", name="Exportar", exact=True)
    if not await _wait_for_count(exp, timeout=30_000):
        raise RuntimeError("No se encontró el botón Exportar del reporte")
    async with page.expect_download(timeout=60_000) as download_info:
        for _ in range(6):
            try:
                if await exp.count():
                    await exp.first.click(force=True, timeout=5_000)
            except Exception:
                pass
            await page.wait_for_timeout(4_000)

    download = await download_info.value
    final_path = _build_xubio_filename(
        debug.descargas_dir, "Excel_reporte_sueldos", empresa, config.periodo, download.suggested_filename
    )
    await download.save_as(str(final_path))
    debug.log("download_saved tipo=Excel_reporte_sueldos path=%s", final_path)
    return final_path


async def exportar_excel_control(
    page: Page,
    debug: DebugCapture,
    config: XubioSueldosConfig,
    empresa: str,
) -> Path:
    frame = page.frame_locator("#iframe")
    await ensure_xubio_session(page)
    await _close_modals(page, frame)

    async with page.expect_download(timeout=45_000) as download_info:
        btn = frame.get_by_role("button", name="Control de Liquidación")
        if not await _wait_for_count(btn, timeout=30_000):
            raise RuntimeError("No se encontró el botón Control de Liquidación")
        if not await _click_robust(btn, timeout=10_000):
            raise RuntimeError("No se pudo clickear Control de Liquidación")

    download = await download_info.value
    final_path = _build_xubio_filename(
        debug.descargas_dir, "Excel_control_liquidacion", empresa, config.periodo, download.suggested_filename
    )
    await download.save_as(str(final_path))
    debug.log("download_saved tipo=Excel_control_liquidacion path=%s", final_path)
    return final_path


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

async def descargar_contribuyente(
    page: Page,
    debug: DebugCapture,
    config: XubioSueldosConfig,
    contribuyente: str,
) -> tuple[str, dict[str, Optional[Path]]]:
    """Descarga los 4 archivos de sueldos para un contribuyente.

    Devuelve (empresa_efectiva, descargas) donde descargas es un dict con
    claves liquidaciones/conceptos/reporte/control y Paths como valores.
    """
    cfg = replace(config, contribuyente=contribuyente)
    periodo_xubio = _format_periodo_xubio(config.periodo)
    debug.log("contribuyente_procesar=%s", contribuyente or "(empresa activa)")

    empresa = await execute_step(
        debug, page, "seleccionar_contribuyente",
        lambda: seleccionar_contribuyente(page, debug, cfg),
    )
    debug.log("empresa_efectiva=%s", empresa)

    await execute_step(
        debug, page, "abrir_libro_sueldos_digital",
        lambda: abrir_libro_sueldos_digital(page, debug, periodo_xubio),
    )

    path_liquidaciones = await execute_step(
        debug, page, "exportar_txt_liquidaciones",
        lambda: exportar_txt_liquidaciones(page, debug, cfg, empresa),
    )
    path_conceptos = await execute_step(
        debug, page, "exportar_txt_conceptos",
        lambda: exportar_txt_conceptos(page, debug, cfg, empresa),
    )

    await execute_step(
        debug, page, "abrir_reporte_sueldos",
        lambda: abrir_reporte_sueldos(page, debug, periodo_xubio),
    )

    path_reporte = await execute_step(
        debug, page, "exportar_excel_reporte",
        lambda: exportar_excel_reporte(page, debug, cfg, empresa),
    )
    path_control = await execute_step(
        debug, page, "exportar_excel_control",
        lambda: exportar_excel_control(page, debug, cfg, empresa),
    )

    descargas = {
        "liquidaciones": path_liquidaciones,
        "conceptos": path_conceptos,
        "reporte": path_reporte,
        "control": path_control,
    }
    return empresa, descargas


async def run_with_config(playwright: Playwright, config: XubioSueldosConfig) -> bool:
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log(
        "config periodo=%s nro_liquidacion=%s contribuyente=%s contribuyentes=%s headless=%s proxy=%s",
        config.periodo, config.nro_liquidacion, config.contribuyente, config.contribuyentes,
        config.headless, config.proxy,
    )

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    page: Optional[Page] = None

    try:
        proxy = get_proxy_config() if config.proxy else None
        har_path = debug.har_dir / f"{BOT_NAME}.har"
        browser, context, page = await launch_arca_browser_context(
            playwright,
            headless=config.headless,
            proxy=proxy,
            context_kwargs={
                "accept_downloads": True,
                "record_har_path": str(har_path),
                "record_har_content": "embed",
            },
        )
        page.set_default_timeout(20_000)
        debug.log("browser_launched url=%s", page.url)

        periodo_xubio = _format_periodo_xubio(config.periodo)
        debug.log("periodo_xubio=%s", periodo_xubio)

        await execute_step(debug, page, "login_visma", lambda: login_visma(page, debug, config))

        lista = config.contribuyentes or [config.contribuyente] or [""]
        debug.log("contribuyentes_a_procesar=%s", lista)

        resultados: dict[str, dict[str, Optional[Path]]] = {}
        for contribuyente in lista:
            empresa, descargas = await descargar_contribuyente(page, debug, config, contribuyente)
            resultados[empresa] = descargas

        success = all(
            p is not None
            for descargas in resultados.values()
            for p in descargas.values()
        )
        debug.log("resultado success=%s contribuyentes=%s", success, resultados)
        return success
    except Exception as exc:
        debug.log_error("run_failed error=%s", exc)
        await debug.capture(page, "run_failed")
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


async def main() -> bool:
    config = _load_config_from_env()
    async with async_playwright() as playwright:
        return await run_with_config(playwright, config)


if __name__ == "__main__":
    try:
        load_dotenv()
    except Exception:
        pass

    config = _load_config_from_env()
    print("Iniciando automatización Xubio Sueldos Logger...")
    print(f"Contribuyente: {config.contribuyente or '(empresa activa)'}")
    print(f"Período: {config.periodo}")
    print(f"Nro Liquidación: {config.nro_liquidacion}")
    print(f"Headless: {config.headless}")

    ok = asyncio.run(main())
    print("OK" if ok else "ERROR")
    raise SystemExit(0 if ok else 1)
