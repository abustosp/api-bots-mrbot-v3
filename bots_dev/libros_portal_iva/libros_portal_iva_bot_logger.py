import asyncio
import contextlib
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

BOT_NAME = "libros_portal_iva"
DOWNLOAD_DIR = Path("./descargas")
TEMP_DIR = Path("./temp")
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
PORTAL_IVA_DIRECT_URL = "https://siapweb.cloud.afip.gob.ar/iva"
PORTAL_IVA_INIT_URL = "https://siapweb.cloud.afip.gob.ar/iva/#/init"
PORTAL_IVA_URL_PATTERN = re.compile(r"(siapweb\.cloud\.afip\.gob\.ar/iva|liva\.afip\.gob\.ar)", re.I)

DOWNLOAD_DIR.mkdir(exist_ok=True)
TEMP_DIR.mkdir(exist_ok=True)


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def _format_cuit(cuit: str) -> str:
    digits = re.sub(r"\D", "", cuit or "")
    if len(digits) == 11:
        return f"{digits[:2]}-{digits[2:10]}-{digits[10:]}"
    return cuit


def _build_filename(
    cuit_representado: str,
    periodo_raw: str,
    tipo: str,
    subtipo: str,
    extension: str = ".pdf",
) -> str:
    """{finCUIT} - {cuit} - {AAAAMM} - {tipo} - {subtipo}.{ext}"""
    cuit_digits = re.sub(r"\D", "", cuit_representado or "")
    fin_cuit = cuit_digits[-1:] if cuit_digits else "0"
    cuit_val = cuit_digits if cuit_digits else INVALID_FILENAME_RE.sub("_", cuit_representado)
    
    # Normalizar periodo a AAAAMM
    periodo_norm = re.sub(r"\D", "", periodo_raw or "")
    if len(periodo_norm) == 6:
        pass
    elif re.match(r"^\d{2}/\d{4}$", periodo_raw):
        periodo_norm = f"{periodo_raw[3:]}{periodo_raw[:2]}"
    
    tipo_clean = INVALID_FILENAME_RE.sub("_", tipo)
    subtipo_clean = INVALID_FILENAME_RE.sub("_", subtipo)
    
    return f"{fin_cuit} - {cuit_val} - {periodo_norm} - {tipo_clean} - {subtipo_clean}{extension}"


def _secuencia_to_tipo(secuencia: str) -> str:
    """Convierte texto de secuencia AFIP a formato de tipo limpio"""
    sec_lower = (secuencia or "").strip().lower()
    if "original" in sec_lower:
        return "Original"
    if "rectificativa" in sec_lower:
        num_match = re.search(r'(\d+)', secuencia)
        num = num_match.group(1) if num_match else "1"
        return f"Rectificativa {num}"
    return INVALID_FILENAME_RE.sub("_", secuencia)


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
class LibrosPortalIvaConfig:
    cuit_representante: str
    clave: str
    cuit_representado: str
    periodos: list[str]
    headless: bool = True
    denominacion: str = ""
    operacion: str = "descargar"


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
        except Exception:
            self.logger.exception("screenshot_failed step=%s", step)

        try:
            content = await page.content()
            html_path.write_text(content, encoding="utf-8")
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


async def login_and_open_portal(
    playwright: Playwright,
    config: LibrosPortalIvaConfig,
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
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.8037.97 Safari/537.36"
        },
    )
    # playwright-stealth package syntax
    await Stealth().apply_stealth_async(page)
    page.set_default_timeout(45_000)

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
            raise RuntimeError("Captcha detectado")
        if login_result.password_change_required:
            raise RuntimeError("Se requiere cambiar la clave fiscal")
        if not login_result.success:
            raise RuntimeError(login_errors[-1] if login_errors else "No se pudo iniciar sesión en ARCA")

    await execute_step(debug, page, "login_arca", _login)

    async def _open_service():
        debug.log("Haciendo click manual en Portal IVA")
        await page.wait_for_timeout(3000)
        
        ver_todos = page.locator('a:has-text("Ver todos")')
        if await ver_todos.count() > 0:
            await ver_todos.first.click()
            await page.wait_for_timeout(2000)

        servicio = page.locator('a.panel[title="siap_web_iva"]')
        if await servicio.count() == 0:
            raise RuntimeError("No se encontró el servicio Portal IVA")
            
        debug.log("Click en Portal IVA...")
        try:
            async with page.context.expect_page(timeout=15000) as new_page_info:
                await servicio.first.click(force=True)
            portal_page = await new_page_info.value
            debug.log("Popup abierto exitosamente!")
        except PlaywrightTimeoutError:
            debug.log("No se abrió popup, usando la página actual...")
            portal_page = page
            await servicio.first.click(force=True)

        await portal_page.wait_for_load_state("networkidle", timeout=30_000)
        await portal_page.wait_for_timeout(3_000)
        
        # Guardar evidencia temprana de la página del portal
        await debug.capture(portal_page, "portal_iva_cargado")
        
        debug.log("Portal IVA cargado - URL: %s", portal_page.url)
        return portal_page

    portal_page = await execute_step(debug, page, "abrir_servicio_portal_iva", _open_service)
    return browser, context, portal_page


async def get_representados_disponibles(page: Page) -> list[str]:
    """Extrae la lista de contribuyentes disponibles para representar
    desde la seccion 'REPRESENTAR A:' de la pagina del Portal IVA.
    Prueba multiples estrategias para cubrir variantes de renderizado."""

    CUIT_PATTERN = re.compile(r"\d{2}-\d{8}-\d")

    # Estrategia 1: CSS selectors sobre ambas secciones (REPRESENTANDO A: + REPRESENTAR A:)
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
                text = (await items.nth(i).inner_text()).strip()
                if text and CUIT_PATTERN.search(text):
                    text = " ".join(text.split())
                    if text not in seen:
                        seen.add(text)
                        disponibles.append(text)
            if disponibles:
                return disponibles
        except Exception:
            continue

    # Estrategia 2: buscar cualquier elemento con texto CUIT y navegar al ancestro panel
    try:
        cuit_re_text = re.compile(r"\[?CUIT\s*\d{2}-\d{8}-\d\]?", re.I)
        nodos = page.locator(f"text=/{cuit_re_text.pattern}/")
        count = await nodos.count()
        if count > 0:
            disponibles = []
            seen = set()
            for i in range(min(count, 30)):
                try:
                    parent = nodos.nth(i).locator(
                        "xpath=ancestor::a[contains(@class,'panel')]|"
                        "ancestor::div[contains(@class,'panel')]|"
                        "ancestor::button[contains(@class,'list-group-item')]"
                    )
                    if await parent.count() > 0:
                        text = (await parent.first.inner_text()).strip()
                        text = " ".join(text.split())
                        if text not in seen:
                            seen.add(text)
                            disponibles.append(text)
                except Exception:
                    continue
            if disponibles:
                return disponibles
    except Exception:
        pass

    # Estrategia 3: botones/a de lista (vista modal tras hacer click en "Representar a...")
    try:
        items = page.locator("button.list-group-item, a.list-group-item")
        count = await items.count()
        if count > 0:
            disponibles = []
            for i in range(count):
                text = (await items.nth(i).inner_text()).strip()
                if text and CUIT_PATTERN.search(text):
                    text = " ".join(text.split())
                    disponibles.append(text)
            if disponibles:
                return disponibles
    except Exception:
        pass

    # Estrategia 4 (ultimo recurso): recorrer TODO el DOM con JS buscando CUITs
    try:
        raw = await page.evaluate("""
            () => {
                const cuitRe = /\\d{2}-\\d{8}-\\d/;
                const results = [];
                const seen = new Set();
                const walker = document.createTreeWalker(
                    document.body,
                    NodeFilter.SHOW_TEXT,
                    null,
                    false
                );
                let node;
                while (node = walker.nextNode()) {
                    const text = node.textContent.trim();
                    if (!cuitRe.test(text)) continue;
                    let el = node.parentElement;
                    while (el && el !== document.body) {
                        const tag = el.tagName;
                        const cls = (el.className && typeof el.className === 'string')
                            ? el.className : '';
                        if (tag === 'A' || tag === 'BUTTON' ||
                            cls.includes('panel') || cls.includes('list-group-item') ||
                            tag === 'H3' || tag === 'H2' || tag === 'LI') {
                            const full = el.textContent.trim().replace(/\\s+/g, ' ');
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
        """)
        if raw and isinstance(raw, list) and len(raw) > 0:
            return [str(r) for r in raw if CUIT_PATTERN.search(str(r))]
    except Exception:
        pass

    return []


async def select_representado(page: Page, config: LibrosPortalIvaConfig, debug: DebugCapture) -> list[str]:
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
        nonlocal page, representados
        await ensure_session_active(page)

        await page.wait_for_load_state("networkidle", timeout=30_000)
        await page.wait_for_timeout(2_000)
        debug.log("Portal IVA URL en select: %s", page.url)

        # Extraer desde #/init (usualmente vacio, pero por si acaso)
        representados = await get_representados_disponibles(page)
        debug.log("Representados disponibles: %s", representados)

        if not representados:
            debug.log("WARNING: lista de representados vacia. URL=%s", page.url)
            try:
                content = await page.content()
                dump_path = debug.html_dir / "representados_vacio_debug.html"
                dump_path.write_text(content, encoding="utf-8")
                debug.log("HTML volcado en %s (%d chars)", dump_path.name, len(content))
            except Exception as exc:
                debug.log("No se pudo volcar HTML de debug: %s", exc)

        already_selected = await _is_target_selected()
        if already_selected and representados:
            debug.log("representado_already_selected cuit=%s (con lista)", formatted_cuit)
            return representados

        # Navegar a #/changeRelation para extraer la lista de contribuyentes
        opened_switcher = await click_first(
            [
                page.get_by_title("cambio relación"),
                page.get_by_role("link", name=""),
                page.locator("[title*='cambio relación' i]"),
            ],
            timeout=8_000,
        )

        selected = False

        if opened_switcher:
            await page.wait_for_timeout(1_500)
            try:
                await page.wait_for_url("**/changeRelation**", timeout=10_000)
            except Exception:
                pass
            await page.wait_for_load_state("networkidle", timeout=15_000)
            await page.wait_for_timeout(1_000)

            # EXTRAER AQUI: pagina en #/changeRelation con lista de contribuyentes
            representados = await get_representados_disponibles(page)
            debug.log("Representados disponibles (desde #/changeRelation): %s", representados)

            # INTENTAR DIRECTO: en #/changeRelation los botones ya estan visibles como button.list-group-item
            if representados and not already_selected:
                debug.log("Intentando seleccion directa en #/changeRelation...")
                selected = await click_first(
                    [
                        page.locator("button.list-group-item").filter(has_text=formatted_cuit),
                        page.locator("button.list-group-item").filter(has_text=config.cuit_representado),
                        page.locator("a.list-group-item").filter(has_text=formatted_cuit),
                        page.locator("a.list-group-item").filter(has_text=config.cuit_representado),
                        page.get_by_role("button", name=formatted_cuit),
                        page.get_by_text(formatted_cuit, exact=False),
                        page.get_by_text(config.cuit_representado, exact=False),
                    ],
                    timeout=8_000,
                )
                if selected:
                    debug.log("Seleccion directa OK en #/changeRelation")
                else:
                    debug.log("Seleccion directa no funciono en #/changeRelation, se probara via 'Representar a...'")

        if already_selected:
            debug.log("representado_already_selected cuit=%s (listo)", formatted_cuit)
            return representados

        # Si no se selecciono directo en changeRelation, probar via "Representar a..."
        if not selected:
            await click_first(
                [
                    page.get_by_title("Representar a..."),
                    page.get_by_text("Representar a...", exact=False),
                ],
                timeout=6_000,
            )
            await page.wait_for_timeout(2_000)

            # Si aun no se capturo, reintentar
            if not representados:
                representados = await get_representados_disponibles(page)
                debug.log("Representados disponibles (post-click): %s", representados)

            selected = await click_first(
                [
                    page.get_by_role("button", name=formatted_cuit),
                    page.locator("button.list-group-item").filter(has_text=formatted_cuit),
                    page.locator("button.list-group-item").filter(has_text=config.cuit_representado),
                    page.locator("a.list-group-item").filter(has_text=formatted_cuit),
                    page.locator("a.list-group-item").filter(has_text=config.cuit_representado),
                    page.get_by_text(formatted_cuit, exact=False),
                    page.get_by_text(config.cuit_representado, exact=False),
                ],
                timeout=8_000,
            )

        if selected:
            await page.wait_for_load_state("networkidle", timeout=30_000)
            await page.wait_for_timeout(3_000)
            if await _is_target_selected():
                debug.log("Representado seleccionado correctamente: %s", formatted_cuit)
                return
            debug.log("El representado parece no haberse aplicado correctamente")
        else:
            raise RuntimeError(f"No se pudo seleccionar el representado {formatted_cuit}")

    await execute_step(debug, page, "seleccionar_representado", _select)
    return representados


def parse_periodo_from_input(p: str) -> str:
    """Normaliza un periodo como 2025-11 o 202511 a MM/YYYY (11/2025) como suele aparecer en la AFIP"""
    p = p.strip()
    if re.match(r"^\d{4}-\d{2}$", p):
        return f"{p[5:]}/{p[:4]}"
    if re.match(r"^\d{6}$", p):
        return f"{p[4:]}/{p[:4]}"
    return p


async def _goto_libros_listado(page: Page, debug: DebugCapture) -> bool:
    """Navega desde /init hasta el listado de libros IVA (#/livas.anteriores).
    Retorna True si se llego, False si falla."""
    if "/livas.anteriores" in page.url:
        return True
    if "/init" not in page.url:
        await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
        await page.wait_for_load_state("networkidle", timeout=20_000)
        await page.wait_for_timeout(1_500)
    consultar_ok = await click_first([
        page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.alt)"),
        page.locator("button").filter(has_text=re.compile(r"CONSULTAR", re.I)),
    ], timeout=15_000)
    if not consultar_ok:
        debug.log("goto_libros_listado: no se pudo hacer click en CONSULTAR")
        return False
    await page.wait_for_timeout(1_000)
    libro_ok = await click_first([
        page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.LibroIVA.alt)"),
        page.locator("button").filter(has_text=re.compile(r"Libro IVA", re.I)),
    ], timeout=15_000)
    if not libro_ok:
        debug.log("goto_libros_listado: no se pudo hacer click en Libro IVA")
        return False
    await page.wait_for_load_state("networkidle", timeout=20_000)
    await page.wait_for_timeout(2_000)
    return "/livas.anteriores" in page.url


async def download_libros_iva(page: Page, config: LibrosPortalIvaConfig, debug: DebugCapture):
    async def _navigate():
        await ensure_session_active(page)
        if "/init" not in page.url:
            await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
            await page.wait_for_load_state("networkidle", timeout=20_000)
            await page.wait_for_timeout(1_500)
        await _goto_libros_listado(page, debug)

        # Extraer todos los periodos disponibles con su secuencia
        disponibles = []
        tables = page.locator("table")
        table_count = await tables.count()
        for ti in range(min(table_count, 20)):
            table = tables.nth(ti)
            rows = table.locator("tbody tr")
            for ri in range(await rows.count()):
                row = rows.nth(ri)
                cells = row.locator("td")
                if await cells.count() >= 5:
                    periodo_text = (await cells.nth(2).inner_text()).strip()  # col 2: Periodo Fiscal
                    secuencia_text = (await cells.nth(3).inner_text()).strip()  # col 3: Secuencia
                    if periodo_text:
                        disponibles.append(f"{periodo_text} | {secuencia_text}")
        debug.log("Libros IVA disponibles: %s", disponibles)

    await execute_step(debug, page, "navegar_libros_iva", _navigate)

    for idx, periodo in enumerate(config.periodos):
        periodo_normalizado = parse_periodo_from_input(periodo)
        debug.log("Procesando Libro IVA para periodo %s", periodo_normalizado)

        async def _descargar_libros_del_periodo():
            # Buscar TODAS las filas para este periodo recolectando info primero
            rows_info = []  # (table_index, row_index, secuencia)
            
            tables = page.locator("table")
            table_count = await tables.count()
            for ti in range(min(table_count, 100)):
                table = tables.nth(ti)
                rows = table.locator("tbody tr")
                row_count = await rows.count()
                for ri in range(row_count):
                    row = rows.nth(ri)
                    cells = row.locator("td")
                    if await cells.count() < 5:
                        continue
                    periodo_cell = (await cells.nth(2).inner_text()).strip()
                    if periodo_cell == periodo_normalizado:
                        secuencia = (await cells.nth(3).inner_text()).strip()
                        rows_info.append((ti, ri, secuencia))
            
            if not rows_info:
                debug.log("No se encontró Libro IVA para el periodo %s", periodo_normalizado)
                return
            
            debug.log("Encontradas %d filas para Libro IVA %s", len(rows_info), periodo_normalizado)
            
            # Procesar cada fila
            for row_idx, (ti, ri, secuencia) in enumerate(rows_info):
                debug.log("Procesando fila %d/%d: %s", row_idx + 1, len(rows_info), secuencia)
                tipo = _secuencia_to_tipo(secuencia)
                
                # Re-navegar al listado (usar helper robusto)
                if "/livas.anteriores" not in page.url:
                    await _goto_libros_listado(page, debug)
                
                # Localizar la fila de nuevo y hacer click
                tables = page.locator("table")
                table = tables.nth(ti)
                rows = table.locator("tbody tr")
                row = rows.nth(ri)
                
                ver_btn = row.locator('[title="Ver"]')
                if await ver_btn.count() == 0:
                    ver_btn = row.get_by_title("Ver")
                await ver_btn.first.click()
                await page.wait_for_load_state("networkidle", timeout=20_000)
                await page.wait_for_timeout(2000)

                async def _download_vista_previa():
                    debug.log("Descargando Vista Previa Libro IVA %s", tipo)
                    try:
                        popup_task = asyncio.create_task(page.wait_for_event("popup", timeout=10_000))
                        try:
                            async with page.expect_download(timeout=45_000) as download_info:
                                await page.locator("#btnDescargarVistaPrevia").click()
                            download = await download_info.value
                        finally:
                            popup_page = None
                            try: popup_page = await popup_task
                            except: popup_task.cancel()
                            if popup_page:
                                try: await popup_page.close()
                                except: pass
                        fname = _build_filename(config.cuit_representado, periodo, tipo, "Vista Previa", ".zip")
                        await download.save_as(str(debug.descargas_dir / fname))
                        debug.log("Descargado: %s", fname)
                    except Exception as e:
                        debug.log_error("Error Vista Previa %s: %s", tipo, e)

                async def _download_simple(btn_selector: str, file_label: str, ext: str = ".pdf"):
                    debug.log("Descargando %s Libro IVA %s", file_label, tipo)
                    try:
                        async with page.expect_download(timeout=45_000) as download_info:
                            await page.locator(btn_selector).click()
                        download = await download_info.value
                        fname = _build_filename(config.cuit_representado, periodo, tipo, file_label, ext)
                        await download.save_as(str(debug.descargas_dir / fname))
                        debug.log("Descargado: %s", fname)
                    except Exception as e:
                        debug.log_error("Error %s %s: %s", file_label, tipo, e)

                await _download_vista_previa()
                await _download_simple("#btnDescargarAcuse", "Acuse")
                await _download_simple("#btnDescargarPresentacion", "Presentacion")

                # Volver al inicio directamente via goto
                await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
                await page.wait_for_load_state("networkidle", timeout=20_000)
                await page.wait_for_timeout(2_000)

        await execute_step(debug, page, f"descargar_libro_iva_{periodo}", _descargar_libros_del_periodo)

        # Re-navegar si no es el ultimo (para seguir con el sig periodo)
        if idx < len(config.periodos) - 1:
            async def _renavegar_libros():
                await ensure_session_active(page)
                if "/livas.anteriores" not in page.url:
                    if "/init" not in page.url:
                        await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
                        await page.wait_for_load_state("networkidle", timeout=20_000)
                        await page.wait_for_timeout(1_500)
                    await _goto_libros_listado(page, debug)
            await execute_step(debug, page, f"renavegar_libros_iva_{periodo}", _renavegar_libros)

    async def _volver_inicio():
        if "/init" in page.url:
            debug.log("Ya estamos en Inicio")
            await page.wait_for_load_state("networkidle", timeout=15_000)
            return
        await ensure_session_active(page)
        await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
        await page.wait_for_load_state("networkidle", timeout=20_000)
        await page.wait_for_timeout(1_500)
    await execute_step(debug, page, "volver_inicio_desde_libros", _volver_inicio)


async def download_ddjj(page: Page, config: LibrosPortalIvaConfig, debug: DebugCapture):
    async def _navigate():
        await ensure_session_active(page)
        if "/init" not in page.url:
            await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
            await page.wait_for_load_state("networkidle", timeout=20_000)
            await page.wait_for_timeout(1_500)
        await click_first([
            page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.alt)"),
            page.locator("button").filter(has_text=re.compile(r"CONSULTAR", re.I)),
        ], timeout=15_000)
        await page.wait_for_timeout(1000)
        await click_first([
            page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.portal.alt)"),
            page.locator("button").filter(has_text=re.compile(r"Portal|Declaraciones Juradas", re.I)),
        ], timeout=15_000)
        await page.wait_for_load_state("networkidle", timeout=20_000)
        await page.wait_for_timeout(2000)

        disponibles = []
        tables = page.locator("table")
        table_count = await tables.count()
        for ti in range(min(table_count, 20)):
            table = tables.nth(ti)
            rows = table.locator("tbody tr")
            for ri in range(await rows.count()):
                row = rows.nth(ri)
                cells = row.locator("td")
                if await cells.count() >= 5:
                    periodo_text = (await cells.nth(1).inner_text()).strip()  # col 1: Periodo Fiscal
                    secuencia_text = (await cells.nth(3).inner_text()).strip()  # col 3: Secuencia
                    if periodo_text:
                        disponibles.append(f"{periodo_text} | {secuencia_text}")
        debug.log("DDJJ disponibles: %s", disponibles)

    await execute_step(debug, page, "navegar_ddjj", _navigate)

    for periodo in config.periodos:
        periodo_normalizado = parse_periodo_from_input(periodo)
        debug.log("Procesando DDJJ para periodo %s", periodo_normalizado)

        async def _download_period_ddjj():
            # Asegurarnos de estar en la pagina correcta
            if "declaraciones.anteriores" not in page.url and "/init" not in page.url:
                await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
                await page.wait_for_load_state("networkidle", timeout=20_000)
                await page.wait_for_timeout(2000)
            
            # Si estamos en init, navegar a DDJJ
            if "/init" in page.url:
                consultar_ok = await click_first([
                    page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.alt)"),
                    page.locator("button").filter(has_text=re.compile(r"CONSULTAR", re.I)),
                ], timeout=15_000)
                if not consultar_ok:
                    debug.log("WARNING: no se pudo click en CONSULTAR para DDJJ, intentando goto directo")
                    await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/declaraciones.anteriores")
                    await page.wait_for_load_state("networkidle", timeout=20_000)
                    await page.wait_for_timeout(2_000)
                else:
                    await page.wait_for_timeout(1000)
                    portal_ok = await click_first([
                        page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.portal.alt)"),
                        page.locator("button").filter(has_text=re.compile(r"Portal|Declaraciones Juradas", re.I)),
                    ], timeout=15_000)
                    if not portal_ok:
                        debug.log("WARNING: no se pudo click en Portal/DDJJ, intentando goto directo")
                        await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/declaraciones.anteriores")
                        await page.wait_for_load_state("networkidle", timeout=20_000)
                        await page.wait_for_timeout(2_000)
                    else:
                        await page.wait_for_load_state("networkidle", timeout=20_000)
                        await page.wait_for_timeout(2_000)
            
            tables = page.locator("table")
            table_count = await tables.count()
            found_any = False
            
            for ti in range(min(table_count, 300)):
                table = tables.nth(ti)
                rows = table.locator("tbody tr")
                row_count = await rows.count()
                if row_count == 0:
                    continue
                    
                for ri in range(row_count):
                    row = rows.nth(ri)
                    cells = row.locator("td")
                    cell_count = await cells.count()
                    if cell_count < 5:
                        continue
                    
                    periodo_cell = (await cells.nth(1).inner_text()).strip()  # col 1: Periodo Fiscal
                    if periodo_cell != periodo_normalizado:
                        continue
                    
                    found_any = True
                    secuencia = (await cells.nth(3).inner_text()).strip()  # col 3: Secuencia
                    debug.log("Encontrada DDJJ %s - Secuencia: %s", periodo_normalizado, secuencia)
                    
                    tipo = _secuencia_to_tipo(secuencia)
                    
                    # Intentar "Descargar formulario", fallback a "Ver declaración jurada"
                    desc_btn = row.get_by_title("Descargar formulario")
                    if await desc_btn.count() == 0:
                        desc_btn = row.get_by_title("Ver declaración jurada")
                    if await desc_btn.count() == 0:
                        desc_btn = row.locator('[title="Descargar formulario"]')
                    if await desc_btn.count() == 0:
                        desc_btn = row.locator('[title="Ver declaración jurada"]')
                    
                    if await desc_btn.count() == 0:
                        debug.log("No se encontro boton de descarga/vision para DDJJ %s", periodo_normalizado)
                        break  # No se puede descargar, pasar al sig periodo
                    
                    # Acepta descarga directa, popup o vista en la misma pestaña
                    # (paridad V3: _capturar_ddjj del worker).
                    popup_task = asyncio.create_task(page.wait_for_event("popup", timeout=8_000))
                    download_task = asyncio.create_task(page.wait_for_event("download", timeout=8_000))
                    try:
                        await desc_btn.first.click(timeout=15_000)
                    except Exception as e:
                        for tarea in (popup_task, download_task):
                            tarea.cancel()
                        debug.log_error("Error click DDJJ %s: %s", tipo, e)
                        break
                    
                    descarga = None
                    pdf_page = page
                    es_ver_declaracion = False
                    try:
                        completadas, _ = await asyncio.wait(
                            {popup_task, download_task},
                            timeout=8,
                            return_when=asyncio.FIRST_COMPLETED,
                        )
                    except Exception:
                        completadas = set()
                    for tarea in completadas:
                        try:
                            resultado = tarea.result()
                        except Exception:
                            continue
                        if tarea is download_task and resultado is not None:
                            descarga = resultado
                        elif tarea is popup_task and resultado is not None:
                            debug.log("Se abrio popup para DDJJ %s", tipo)
                            es_ver_declaracion = True
                            try:
                                await resultado.wait_for_load_state("networkidle", timeout=20_000)
                            except Exception:
                                pass
                            pdf_page = resultado
                    
                    if descarga is None:
                        try:
                            await pdf_page.wait_for_load_state("networkidle", timeout=15_000)
                        except Exception:
                            pass
                        await pdf_page.wait_for_timeout(3_000)
                    
                    debug.log("URL tras popup: %s", pdf_page.url)
                    
                    # Si es "Ver declaración jurada" (F2002), hacer click en "visualizar formulario"
                    if es_ver_declaracion:
                        try:
                            vis_btn = pdf_page.get_by_text("visualizar formulario", exact=False)
                            if await vis_btn.count() > 0:
                                debug.log("Haciendo click en 'visualizar formulario'")
                                await vis_btn.first.click(timeout=10_000)
                                try:
                                    await pdf_page.wait_for_load_state("networkidle", timeout=30_000)
                                except Exception:
                                    pass
                                await pdf_page.wait_for_timeout(3_000)
                                debug.log("URL tras visualizar formulario: %s", pdf_page.url)
                            else:
                                debug.log("No se encontro 'visualizar formulario', se continua con URL actual: %s", pdf_page.url)
                        except Exception as e:
                            debug.log("Error al hacer click en 'visualizar formulario': %s", e)
                    
                    try:
                        fname = _build_filename(config.cuit_representado, periodo, tipo, "DDJJ", ".pdf")
                        path = debug.descargas_dir / fname
                        counter = 1
                        while path.exists():
                            path = debug.descargas_dir / f"{fname.replace('.pdf', '')}_{counter}.pdf"
                            counter += 1
                        if descarga is not None:
                            # El formulario llegó como descarga directa del portal.
                            await descarga.save_as(str(path))
                        else:
                            await pdf_page.pdf(path=str(path))
                        if not path.read_bytes().startswith(b"%PDF"):
                            raise RuntimeError("el documento de DDJJ no es un PDF válido")
                        debug.log("Descargado DDJJ: %s", path.name)
                    except Exception as e:
                        debug.log_error("Error guardando PDF DDJJ %s: %s", tipo, e)
                    finally:
                        for tarea in (popup_task, download_task):
                            if not tarea.done():
                                tarea.cancel()
                            with contextlib.suppress(asyncio.CancelledError, Exception):
                                await tarea
                        if pdf_page != page:
                            try:
                                await pdf_page.close()
                            except Exception:
                                pass
                    
                    break
                if found_any:
                    break
            
            if not found_any:
                debug.log("No se encontró DDJJ para el periodo %s", periodo_normalizado)
                # Intentar navegar a la pagina de DDJJ directamente
                await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/declaraciones.anteriores")
                await page.wait_for_load_state("networkidle", timeout=20_000)
                await page.wait_for_timeout(2000)

        await execute_step(debug, page, f"descargar_ddjj_{periodo}", _download_period_ddjj)


async def run_with_config(playwright: Playwright, config: LibrosPortalIvaConfig) -> bool:
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("config periodos=%s headless=%s operacion=%s", config.periodos, config.headless, config.operacion)

    operacion_norm = (config.operacion or "descargar").strip().lower()
    if operacion_norm not in ("descargar", "descargar_libros", "descargar_ddjj"):
        debug.log_error("Operación inválida: %s", config.operacion)
        return False

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    portal_page: Optional[Page] = None

    try:
        browser, context, portal_page = await login_and_open_portal(playwright, config, debug)
        await select_representado(portal_page, config, debug)
        # La lista ya se loguea dentro de select_representado
        debug.log("Iniciando descargas (operacion=%s)", operacion_norm)
        if operacion_norm in ("descargar", "descargar_libros"):
            await download_libros_iva(portal_page, config, debug)
        if operacion_norm in ("descargar", "descargar_ddjj"):
            await download_ddjj(portal_page, config, debug)
        
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
            except:
                pass
        if browser:
            try:
                await browser.close()
            except:
                pass
        print(f"Logs y evidencias guardados en: {debug.run_dir}")


async def main() -> None:
    load_dotenv()
    
    cuit_representante = os.getenv("PORTAL_IVA_LIBROS_CUIT_REPRESENTANTE", "").strip()
    clave = os.getenv("PORTAL_IVA_LIBROS_CLAVE", "").strip()
    cuit_representado = os.getenv("PORTAL_IVA_LIBROS_CUIT_REPRESENTADO", "").strip()
    periodos_str = os.getenv("PORTAL_IVA_LIBROS_PERIODOS", "").strip()
    headless_str = os.getenv("PORTAL_IVA_HEADLESS", "false").lower()
    headless = headless_str in ("true", "1", "yes", "si")
    operacion = os.getenv("PORTAL_IVA_LIBROS_OPERACION", "descargar").strip().lower() or "descargar"
    
    if not cuit_representante or not clave or not cuit_representado:
        print("Faltan variables de entorno (PORTAL_IVA_LIBROS_CUIT_REPRESENTANTE, PORTAL_IVA_LIBROS_CLAVE, PORTAL_IVA_LIBROS_CUIT_REPRESENTADO)")
        return
        
    periodos = [p.strip() for p in periodos_str.split(",") if p.strip()]
    if not periodos:
        print("No se especificaron periodos en PORTAL_IVA_LIBROS_PERIODOS")
        return

    print("Iniciando automatización de Libros y DDJJ Portal IVA Logger...")
    print(f"CUIT Representante: {cuit_representante}")
    print(f"CUIT Representado: {cuit_representado}")
    print(f"Periodos a procesar: {periodos}")
    print(f"Operación: {operacion}")

    config = LibrosPortalIvaConfig(
        cuit_representante=cuit_representante,
        clave=clave,
        cuit_representado=cuit_representado,
        periodos=periodos,
        headless=headless,
        operacion=operacion
    )

    async with async_playwright() as playwright:
        success = await run_with_config(playwright, config)
        if success:
            print("Descarga finalizada correctamente.")
        else:
            print("El proceso finalizó con errores.")


if __name__ == "__main__":
    asyncio.run(main())
