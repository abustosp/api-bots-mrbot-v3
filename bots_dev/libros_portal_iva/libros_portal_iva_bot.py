import asyncio
import contextlib
import logging
import os
import re
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
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
PORTAL_IVA_DIRECT_URL = "https://siapweb.cloud.afip.gob.ar/iva"
PORTAL_IVA_INIT_URL = "https://siapweb.cloud.afip.gob.ar/iva/#/init"
PORTAL_IVA_URL_PATTERN = re.compile(r"(siapweb\.cloud\.afip\.gob\.ar/iva|liva\.afip\.gob\.ar)", re.I)

DOWNLOAD_DIR.mkdir(exist_ok=True)


def _format_cuit(cuit: str) -> str:
    digits = re.sub(r"\D", "", cuit or "")
    if len(digits) == 11:
        return f"{digits[:2]}-{digits[2:10]}-{digits[10:]}"
    return cuit


def _looks_like_blank_html(content: str) -> bool:
    compact = re.sub(r"\s+", "", content or "").lower()
    if not compact:
        return True
    return len(compact) < 120


def _portal_iva_init_url_with_key() -> str:
    token = format(int(datetime.now().timestamp() * 1000), "x")[-6:]
    return f"{PORTAL_IVA_INIT_URL}?_k={token}"


def _build_filename(
    cuit_representado: str,
    periodo_raw: str,
    tipo: str,
    subtipo: str,
    extension: str = ".pdf",
) -> str:
    cuit_digits = re.sub(r"\D", "", cuit_representado or "")
    fin_cuit = cuit_digits[-1:] if cuit_digits else "0"
    cuit_val = cuit_digits if cuit_digits else INVALID_FILENAME_RE.sub("_", cuit_representado)
    periodo_norm = re.sub(r"\D", "", periodo_raw or "")
    if not periodo_norm or len(periodo_norm) != 6:
        if re.match(r"^\d{2}/\d{4}$", periodo_raw):
            periodo_norm = f"{periodo_raw[3:]}{periodo_raw[:2]}"
    tipo_clean = INVALID_FILENAME_RE.sub("_", tipo)
    subtipo_clean = INVALID_FILENAME_RE.sub("_", subtipo)
    return f"{fin_cuit} - {cuit_val} - {periodo_norm} - {tipo_clean} - {subtipo_clean}{extension}"


def _secuencia_to_tipo(secuencia: str) -> str:
    sec_lower = (secuencia or "").strip().lower()
    if "original" in sec_lower:
        return "Original"
    if "rectificativa" in sec_lower:
        num_match = re.search(r'(\d+)', secuencia)
        num = num_match.group(1) if num_match else "1"
        return f"Rectificativa {num}"
    return INVALID_FILENAME_RE.sub("_", secuencia)


def parse_periodo_from_input(p: str) -> str:
    p = p.strip()
    if re.match(r"^\d{4}-\d{2}$", p):
        return f"{p[5:]}/{p[:4]}"
    if re.match(r"^\d{6}$", p):
        return f"{p[4:]}/{p[:4]}"
    return p


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


async def _goto_libros_listado(page: Page) -> bool:
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
        return False
    await page.wait_for_timeout(1_000)
    libro_ok = await click_first([
        page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.LibroIVA.alt)"),
        page.locator("button").filter(has_text=re.compile(r"Libro IVA", re.I)),
    ], timeout=15_000)
    if not libro_ok:
        return False
    await page.wait_for_load_state("networkidle", timeout=20_000)
    await page.wait_for_timeout(2_000)
    return "/livas.anteriores" in page.url


async def login_and_open_portal(
    playwright: Playwright,
    cuit_representante: str,
    clave: str,
    headless: bool,
) -> tuple[Browser, BrowserContext, Page]:
    browser, context, page = await launch_arca_browser_context(
        playwright,
        headless=headless,
        context_kwargs={
            "accept_downloads": True,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/154.0.8037.97 Safari/537.36"
        },
    )
    await Stealth().apply_stealth_async(page)
    page.set_default_timeout(30_000)

    login_errors: list[str] = []
    login_result = await login_arca(
        page,
        cuit_representante,
        clave,
        error_logs=login_errors,
        fail_on_captcha=True,
    )
    if login_result.captcha_detected:
        raise RuntimeError("Captcha detectado")
    if login_result.password_change_required:
        raise RuntimeError("Se requiere cambiar la clave fiscal")
    if not login_result.success:
        raise RuntimeError(login_errors[-1] if login_errors else "No se pudo iniciar sesión en ARCA")

    # Abrir Portal IVA manualmente
    await page.wait_for_timeout(3000)
    ver_todos = page.locator('a:has-text("Ver todos")')
    if await ver_todos.count() > 0:
        await ver_todos.first.click()
        await page.wait_for_timeout(2000)

    servicio = page.locator('a.panel[title="siap_web_iva"]')
    if await servicio.count() == 0:
        raise RuntimeError("No se encontró el servicio Portal IVA")

    try:
        async with page.context.expect_page(timeout=15000) as new_page_info:
            await servicio.first.click(force=True)
        portal_page = await new_page_info.value
    except PlaywrightTimeoutError:
        portal_page = page
        await servicio.first.click(force=True)

    await portal_page.wait_for_load_state("networkidle", timeout=30_000)
    await portal_page.wait_for_timeout(3000)
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


async def select_representado(page: Page, cuit_representado: str, cuit_representante: str) -> list[str]:
    formatted_cuit = _format_cuit(cuit_representado)
    target_cuit_digits = re.sub(r"\D", "", cuit_representado or "")
    representante_cuit_digits = re.sub(r"\D", "", cuit_representante or "")
    representados: list[str] = []

    async def _is_target_selected() -> bool:
        if await click_first([
            page.get_by_text(re.compile(rf"Representando a:.*{re.escape(formatted_cuit)}", re.I)),
        ], timeout=1200):
            return True
        if target_cuit_digits == representante_cuit_digits and await click_first([
            page.get_by_text(formatted_cuit, exact=False),
        ], timeout=1200):
            return True
        return False

    await page.wait_for_load_state("networkidle", timeout=30_000)
    await page.wait_for_timeout(2000)

    # Extraer desde #/init (usualmente vacio, pero por si acaso)
    representados = await get_representados_disponibles(page)
    logging.info("Representados disponibles: %s", representados)

    already_selected = await _is_target_selected()
    if already_selected and representados:
        return representados

    # Navegar a #/changeRelation para extraer la lista de contribuyentes
    opened_switcher = await click_first([
        page.get_by_title("cambio relación"),
        page.get_by_role("link", name=""),
        page.locator("[title*='cambio relación' i]"),
    ], timeout=8_000)

    selected = False

    if opened_switcher:
        await page.wait_for_timeout(1500)
        try:
            await page.wait_for_url("**/changeRelation**", timeout=10_000)
        except Exception:
            pass
        await page.wait_for_load_state("networkidle", timeout=15_000)
        await page.wait_for_timeout(1000)

        # EXTRAER AQUI: pagina en #/changeRelation con lista de contribuyentes
        representados = await get_representados_disponibles(page)
        logging.info("Representados disponibles (desde #/changeRelation): %s", representados)

        # INTENTAR DIRECTO: en #/changeRelation los botones ya estan visibles como button.list-group-item
        if representados and not already_selected:
            logging.info("Intentando seleccion directa en #/changeRelation...")
            selected = await click_first([
                page.locator("button.list-group-item").filter(has_text=formatted_cuit),
                page.locator("button.list-group-item").filter(has_text=cuit_representado),
                page.locator("a.list-group-item").filter(has_text=formatted_cuit),
                page.locator("a.list-group-item").filter(has_text=cuit_representado),
                page.get_by_role("button", name=formatted_cuit),
                page.get_by_text(formatted_cuit, exact=False),
                page.get_by_text(cuit_representado, exact=False),
            ], timeout=8_000)
            if selected:
                logging.info("Seleccion directa OK en #/changeRelation")
            else:
                logging.info("Seleccion directa no funciono en #/changeRelation, se probara via 'Representar a...'")

    if already_selected:
        return representados

    # Si no se selecciono directo en changeRelation, probar via "Representar a..."
    if not selected:
        await click_first([
            page.get_by_title("Representar a..."),
            page.get_by_text("Representar a...", exact=False),
        ], timeout=6_000)
        await page.wait_for_timeout(2000)

        # Reintentar extraccion si aun esta vacia
        if not representados:
            representados = await get_representados_disponibles(page)
            logging.info("Representados disponibles (post-click): %s", representados)

        selected = await click_first([
            page.get_by_role("button", name=formatted_cuit),
                page.locator("button.list-group-item").filter(has_text=formatted_cuit),
                page.locator("button.list-group-item").filter(has_text=cuit_representado),
                page.locator("a.list-group-item").filter(has_text=formatted_cuit),
                page.locator("a.list-group-item").filter(has_text=cuit_representado),
            page.get_by_text(formatted_cuit, exact=False),
            page.get_by_text(cuit_representado, exact=False),
        ], timeout=8_000)

    if not selected:
        raise RuntimeError(f"No se pudo seleccionar el representado {formatted_cuit}")

    await page.wait_for_load_state("networkidle", timeout=30_000)
    await page.wait_for_timeout(3000)

    if not await _is_target_selected():
        logging.warning("El representado no se reflejo en la interfaz, se continua igual")

    return representados


async def download_libros_iva(page: Page, cuit_representado: str, periodos: list[str], download_dir: Path):
    await ensure_session_active(page)
    if "/init" not in page.url:
        await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
        await page.wait_for_load_state("networkidle", timeout=20_000)
        await page.wait_for_timeout(1_500)
    await _goto_libros_listado(page)

    for idx, periodo in enumerate(periodos):
        periodo_normalizado = parse_periodo_from_input(periodo)
        logging.info("Procesando Libro IVA %s", periodo_normalizado)

        # Recolectar filas
        rows_info = []
        tables = page.locator("table")
        for ti in range(min(await tables.count(), 100)):
            table = tables.nth(ti)
            rows = table.locator("tbody tr")
            for ri in range(await rows.count()):
                cells = rows.nth(ri).locator("td")
                if await cells.count() < 5:
                    continue
                pc = (await cells.nth(2).inner_text()).strip()
                if pc == periodo_normalizado:
                    rows_info.append((ti, ri, (await cells.nth(3).inner_text()).strip()))

        if not rows_info:
            logging.info("No se encontraron filas para Libro IVA %s", periodo_normalizado)
            continue

        for row_idx, (ti, ri, secuencia) in enumerate(rows_info):
            tipo = _secuencia_to_tipo(secuencia)
            logging.info("Descargando Libro IVA %s - %s (%d/%d)", periodo_normalizado, tipo, row_idx + 1, len(rows_info))

            # Re-navegar al listado si es necesario
            if "/livas.anteriores" not in page.url:
                await _goto_libros_listado(page)

            ver_btn = page.locator("table").nth(ti).locator("tbody tr").nth(ri).locator('[title="Ver"]')
            if await ver_btn.count() == 0:
                ver_btn = page.locator("table").nth(ti).locator("tbody tr").nth(ri).get_by_title("Ver")
            await ver_btn.first.click()
            await page.wait_for_load_state("networkidle", timeout=20_000)
            await page.wait_for_timeout(2000)

            # Vista Previa
            try:
                popup_task = asyncio.create_task(page.wait_for_event("popup", timeout=10_000))
                try:
                    async with page.expect_download(timeout=45_000) as di:
                        await page.locator("#btnDescargarVistaPrevia").click()
                    d = await di.value
                finally:
                    try:
                        pp = await popup_task
                        if pp:
                            await pp.close()
                    except:
                        popup_task.cancel()
                fn = _build_filename(cuit_representado, periodo, tipo, "Vista Previa", ".zip")
                await d.save_as(str(download_dir / fn))
            except Exception as e:
                logging.error("Error Vista Previa %s: %s", tipo, e)

            # Acuse
            try:
                async with page.expect_download(timeout=45_000) as di:
                    await page.locator("#btnDescargarAcuse").click()
                fn = _build_filename(cuit_representado, periodo, tipo, "Acuse")
                await (await di.value).save_as(str(download_dir / fn))
            except Exception as e:
                logging.error("Error Acuse %s: %s", tipo, e)

            # Presentacion
            try:
                async with page.expect_download(timeout=45_000) as di:
                    await page.locator("#btnDescargarPresentacion").click()
                fn = _build_filename(cuit_representado, periodo, tipo, "Presentacion")
                await (await di.value).save_as(str(download_dir / fn))
            except Exception as e:
                logging.error("Error Presentacion %s: %s", tipo, e)

            # Volver a inicio directamente via goto
            await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
            await page.wait_for_load_state("networkidle", timeout=20_000)
            await page.wait_for_timeout(2_000)

        # Re-navegar para el siguiente periodo
        if idx < len(periodos) - 1:
            await _goto_libros_listado(page)


async def download_ddjj(page: Page, cuit_representado: str, periodos: list[str], download_dir: Path):
    await ensure_session_active(page)
    if "/init" not in page.url:
        await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
        await page.wait_for_load_state("networkidle", timeout=20_000)
        await page.wait_for_timeout(1_500)
    consultar_ok = await click_first([
        page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.alt)"),
        page.locator("button").filter(has_text=re.compile(r"CONSULTAR", re.I)),
    ], timeout=15_000)
    if consultar_ok:
        await page.wait_for_timeout(1000)
        portal_ok = await click_first([
            page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.portal.alt)"),
            page.locator("button").filter(has_text=re.compile(r"Portal|Declaraciones Juradas", re.I)),
        ], timeout=15_000)
        if not portal_ok:
            await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/declaraciones.anteriores")
            await page.wait_for_load_state("networkidle", timeout=20_000)
            await page.wait_for_timeout(2_000)
        else:
            await page.wait_for_load_state("networkidle", timeout=20_000)
            await page.wait_for_timeout(2_000)
    else:
        await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/declaraciones.anteriores")
        await page.wait_for_load_state("networkidle", timeout=20_000)
        await page.wait_for_timeout(2_000)

    for periodo in periodos:
        periodo_normalizado = parse_periodo_from_input(periodo)
        logging.info("Procesando DDJJ %s", periodo_normalizado)

        if "declaraciones.anteriores" not in page.url and "/init" not in page.url:
            await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
            await page.wait_for_load_state("networkidle", timeout=20_000)
            await page.wait_for_timeout(2000)

        if "/init" in page.url:
            consultar_ok = await click_first([
                page.get_by_label("Sin texto (iva.home.btn.declaraciones.anteriores.alt)"),
                page.locator("button").filter(has_text=re.compile(r"CONSULTAR", re.I)),
            ], timeout=15_000)
            if not consultar_ok:
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
                    await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/declaraciones.anteriores")
                    await page.wait_for_load_state("networkidle", timeout=20_000)
                    await page.wait_for_timeout(2_000)
                else:
                    await page.wait_for_load_state("networkidle", timeout=20_000)
                    await page.wait_for_timeout(2_000)

        tables = page.locator("table")
        table_count = await tables.count()
        found = False
        for ti in range(min(table_count, 300)):
            table = tables.nth(ti)
            rows = table.locator("tbody tr")
            for ri in range(await rows.count()):
                cells = rows.nth(ri).locator("td")
                if await cells.count() < 5:
                    continue
                pc = (await cells.nth(1).inner_text()).strip()
                if pc != periodo_normalizado:
                    continue
                found = True
                secuencia = (await cells.nth(3).inner_text()).strip()
                tipo = _secuencia_to_tipo(secuencia)

                # Intentar "Descargar formulario", fallback a "Ver declaración jurada"
                desc_btn = rows.nth(ri).get_by_title("Descargar formulario")
                if await desc_btn.count() == 0:
                    desc_btn = rows.nth(ri).get_by_title("Ver declaración jurada")
                if await desc_btn.count() == 0:
                    desc_btn = rows.nth(ri).locator('[title="Descargar formulario"]')
                if await desc_btn.count() == 0:
                    desc_btn = rows.nth(ri).locator('[title="Ver declaración jurada"]')

                if await desc_btn.count() == 0:
                    logging.info("No se encontro boton de descarga/vision para DDJJ %s", periodo_normalizado)
                    break

                # Acepta descarga directa, popup o vista en la misma pestaña
                # (paridad V3: _capturar_ddjj del worker).
                popup_task = asyncio.create_task(page.wait_for_event("popup", timeout=8_000))
                download_task = asyncio.create_task(page.wait_for_event("download", timeout=8_000))
                try:
                    await desc_btn.first.click(timeout=15_000)
                except Exception as e:
                    for tarea in (popup_task, download_task):
                        tarea.cancel()
                    logging.error("Error click DDJJ %s: %s", tipo, e)
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
                        logging.info("Se abrio popup para DDJJ %s", tipo)
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

                # Si es "Ver declaración jurada" (F2002), hacer click en "visualizar formulario"
                if es_ver_declaracion:
                    try:
                        vis_btn = pdf_page.get_by_text("visualizar formulario", exact=False)
                        if await vis_btn.count() > 0:
                            logging.info("Haciendo click en 'visualizar formulario'")
                            await vis_btn.first.click(timeout=10_000)
                            try:
                                await pdf_page.wait_for_load_state("networkidle", timeout=30_000)
                            except Exception:
                                pass
                            await pdf_page.wait_for_timeout(3_000)
                            logging.info("URL tras visualizar formulario: %s", pdf_page.url)
                        else:
                            logging.info("No se encontro 'visualizar formulario', URL actual: %s", pdf_page.url)
                    except Exception as e:
                        logging.error("Error al hacer click en 'visualizar formulario': %s", e)

                fn = _build_filename(cuit_representado, periodo, tipo, "DDJJ")
                path = download_dir / fn
                try:
                    if descarga is not None:
                        # El formulario llegó como descarga directa del portal.
                        await descarga.save_as(str(path))
                    else:
                        await pdf_page.pdf(path=str(path))
                    if not path.read_bytes().startswith(b"%PDF"):
                        raise RuntimeError("el documento de DDJJ no es un PDF válido")
                    logging.info("Descargado DDJJ %s", path.name)
                except Exception as e:
                    logging.error("Error DDJJ %s: %s", tipo, e)
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
            if found:
                break

        if not found:
            logging.info("No se encontró DDJJ %s", periodo_normalizado)


async def main() -> None:
    load_dotenv()

    cuit_representante = os.getenv("PORTAL_IVA_LIBROS_CUIT_REPRESENTANTE", "").strip()
    clave = os.getenv("PORTAL_IVA_LIBROS_CLAVE", "").strip()
    cuit_representado = os.getenv("PORTAL_IVA_LIBROS_CUIT_REPRESENTADO", "").strip()
    periodos_str = os.getenv("PORTAL_IVA_LIBROS_PERIODOS", "").strip()
    headless = os.getenv("PORTAL_IVA_HEADLESS", "true").lower() in ("true", "1", "yes", "si")
    operacion = os.getenv("PORTAL_IVA_LIBROS_OPERACION", "descargar").strip().lower() or "descargar"

    if not cuit_representante or not clave or not cuit_representado:
        print("Faltan variables de entorno")
        return

    periodos = [p.strip() for p in periodos_str.split(",") if p.strip()]
    if not periodos:
        print("No se especificaron periodos")
        return

    if operacion not in ("descargar", "descargar_libros", "descargar_ddjj"):
        print(f"Operación inválida: {operacion}")
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(f"Iniciando Libros Portal IVA (slim) - headless={headless} - operacion={operacion}")
    print(f"Periodos: {periodos}")

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    portal_page: Optional[Page] = None

    try:
        async with async_playwright() as playwright:
            browser, context, portal_page = await login_and_open_portal(
                playwright, cuit_representante, clave, headless,
            )
            representados = await select_representado(portal_page, cuit_representado, cuit_representante)
            print(f"Representados disponibles: {representados}")

            if operacion in ("descargar", "descargar_libros"):
                await download_libros_iva(portal_page, cuit_representado, periodos, DOWNLOAD_DIR)
            if operacion in ("descargar", "descargar_ddjj"):
                await download_ddjj(portal_page, cuit_representado, periodos, DOWNLOAD_DIR)

            print(f"Descargas guardadas en: {DOWNLOAD_DIR.resolve()}")
            print("Proceso completado exitosamente")

    except Exception as e:
        logging.error("Error: %s", e)
        print(f"Proceso finalizó con errores: {e}")
        raise
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


if __name__ == "__main__":
    asyncio.run(main())
