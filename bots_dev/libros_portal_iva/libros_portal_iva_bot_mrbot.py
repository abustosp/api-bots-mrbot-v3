import asyncio
import contextlib
import logging
import os
import tempfile
import re
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

from playwright_stealth import Stealth

from shared.arca_login import (
    launch_arca_browser_context,
    login_arca,
    wait_page_ready,
)
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser

load_dotenv()

_STEALTH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/154.0.8037.97 Safari/537.36"
)

BOT_NAME = "libros_portal_iva"
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')
PORTAL_IVA_DIRECT_URL = "https://siapweb.cloud.afip.gob.ar/iva"
PORTAL_IVA_INIT_URL = "https://siapweb.cloud.afip.gob.ar/iva/#/init"
PORTAL_IVA_URL_PATTERN = re.compile(r"(siapweb\.cloud\.afip\.gob\.ar/iva|liva\.afip\.gob\.ar)", re.I)


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
        num_match = re.search(r"(\d+)", secuencia)
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


def generate_period_range(desde: str, hasta: str) -> list[str]:
    desde = desde.strip()
    hasta = hasta.strip()
    year_from = int(desde[:4])
    month_from = int(desde[4:6])
    year_to = int(hasta[:4])
    month_to = int(hasta[4:6])
    periods = []
    y, m = year_from, month_from
    while True:
        periods.append(f"{y:04d}{m:02d}")
        if y == year_to and m == month_to:
            break
        m += 1
        if m > 12:
            m = 1
            y += 1
    return periods


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
        page.get_by_role("button", name=re.compile(r"IR A LA P[ÁA]GINA DE AFIP", re.I)),
    ]
    for locator in checks:
        if await is_visible(locator, timeout=500):
            return True
    return False


async def ensure_session_active(page: Page) -> None:
    if await is_session_expired(page):
        raise RuntimeError("La sesion de Portal IVA expiro")


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


async def _scrape_periodos(page: Page) -> list[str]:
    """Extrae los periodos disponibles en la tabla de Libros IVA o DDJJ. Retorna lista de AAAAMM."""
    periodos: list[str] = []
    seen = set()
    try:
        tables = page.locator("table")
        for ti in range(min(await tables.count(), 100)):
            table = tables.nth(ti)
            rows = table.locator("tbody tr")
            for ri in range(await rows.count()):
                cells = rows.nth(ri).locator("td")
                cell_count = await cells.count()
                if cell_count < 3:
                    continue
                periodo_text = ""
                for ci in range(cell_count):
                    txt = (await cells.nth(ci).inner_text()).strip()
                    if re.match(r"^\d{2}/\d{4}$", txt):
                        periodo_text = txt
                        break
                if not periodo_text:
                    continue
                month, year = periodo_text.split("/")
                value = f"{year}{month}"
                if value not in seen:
                    seen.add(value)
                    periodos.append(value)
    except Exception:
        pass
    return periodos


async def _login_and_open_portal(
    playwright: Playwright,
    cuit_representante: str,
    clave: str,
    headless: bool,
    proxy: Optional[dict] = None,
) -> tuple[Browser, BrowserContext, Page]:
    context_kwargs: dict = {
        "accept_downloads": True,
        "user_agent": _STEALTH_USER_AGENT,
    }
    browser, context, page = await launch_arca_browser_context(
        playwright,
        headless=headless,
        proxy=proxy,
        context_kwargs=context_kwargs,
    )
    await Stealth().apply_stealth_async(page)
    page.set_default_timeout(45_000)

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
        raise RuntimeError(login_errors[-1] if login_errors else "No se pudo iniciar sesion en ARCA")

    await ensure_session_active(page)

    await page.wait_for_timeout(3000)
    ver_todos = page.locator('a:has-text("Ver todos")')
    if await ver_todos.count() > 0:
        await ver_todos.first.click()
        await page.wait_for_timeout(2000)

    servicio = page.locator('a.panel[title="siap_web_iva"]')
    if await servicio.count() == 0:
        servicio = page.locator('a.full-width:has-text("Portal IVA")')
    if await servicio.count() == 0:
        servicio = page.locator('a:has-text("Portal IVA"):visible')
    if await servicio.count() == 0:
        raise RuntimeError("No se encontro el servicio Portal IVA")

    try:
        async with page.context.expect_page(timeout=15000) as new_page_info:
            await servicio.first.click(force=True)
        portal_page = await new_page_info.value
    except PlaywrightTimeoutError:
        portal_page = page
        await servicio.first.click(force=True)

    try:
        await portal_page.wait_for_load_state("networkidle", timeout=30_000)
    except PlaywrightTimeoutError:
        pass
    await portal_page.wait_for_timeout(3000)
    return browser, context, portal_page


_CUIT_PATTERN = re.compile(r"\d{2}-\d{8}-\d")


def _extract_cuits(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for text in items:
        cuit_match = _CUIT_PATTERN.search(text)
        value = cuit_match.group(0) if cuit_match else text
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


async def _get_representados_disponibles(page: Page) -> list[str]:
    """Extrae la lista de contribuyentes disponibles para representar desde la pagina
    del Portal IVA. Prueba multiples estrategias para cubrir variantes de renderizado."""

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
            disponibles: list[str] = []
            seen: set[str] = set()
            for i in range(count):
                text = (await items.nth(i).inner_text()).strip()
                if text and _CUIT_PATTERN.search(text):
                    text = " ".join(text.split())
                    if text not in seen:
                        seen.add(text)
                        disponibles.append(text)
            if disponibles:
                return _extract_cuits(disponibles)
        except Exception:
            continue

    # Estrategia 2: buscar cualquier elemento con texto CUIT y navegar al ancestro
    try:
        cuit_re_text = re.compile(r"\[?CUIT\s*\d{2}-\d{8}-\d\]?", re.I)
        nodos = page.locator(f"text=/{cuit_re_text.pattern}/")
        count = await nodos.count()
        if count > 0:
            disponibles: list[str] = []
            seen: set[str] = set()
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
                return _extract_cuits(disponibles)
    except Exception:
        pass

    # Estrategia 3: botones/a de lista (vista modal)
    try:
        items = page.locator("button.list-group-item, a.list-group-item")
        count = await items.count()
        if count > 0:
            disponibles: list[str] = []
            for i in range(count):
                text = (await items.nth(i).inner_text()).strip()
                if text and _CUIT_PATTERN.search(text):
                    text = " ".join(text.split())
                    disponibles.append(text)
            if disponibles:
                return _extract_cuits(disponibles)
    except Exception:
        pass

    # Estrategia 4 (ultimo recurso): recorrer todo el DOM con JS buscando CUITs
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
            return _extract_cuits([str(r) for r in raw if _CUIT_PATTERN.search(str(r))])
    except Exception:
        pass

    return []


async def _select_representado(page: Page, cuit_representado: str, cuit_representante: str) -> list[str]:
    """Selecciona el representado y retorna la lista de contribuyentes disponibles
    usando multiples estrategias de extraccion."""
    formatted_cuit = _format_cuit(cuit_representado)
    target_cuit_digits = re.sub(r"\D", "", cuit_representado or "")
    representante_cuit_digits = re.sub(r"\D", "", cuit_representante or "")

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

    await page.wait_for_load_state("networkidle", timeout=30_000)
    await page.wait_for_timeout(2000)

    await ensure_session_active(page)

    # Extraer desde #/init (usualmente limitado, pero por si acaso)
    contribuyentes = await _get_representados_disponibles(page)
    logging.info("Contribuyentes disponibles (desde inicio): %d", len(contribuyentes))

    already_selected = await _is_target_selected()
    if already_selected and contribuyentes:
        logging.info("Representado ya seleccionado: %s, contribuyentes: %d", formatted_cuit, len(contribuyentes))
        return contribuyentes

    # Navegar a #/changeRelation para extraer la lista completa de contribuyentes
    opened_switcher = await click_first([
        page.get_by_title("cambio relacion"),
        page.get_by_role("link", name="\uf0c0"),
        page.locator("[title*='cambio relacion' i]"),
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

        contribuyentes = await _get_representados_disponibles(page)
        logging.info("Contribuyentes disponibles (desde #/changeRelation): %d", len(contribuyentes))

        # INTENTAR DIRECTO: en #/changeRelation los botones ya estan visibles como button.list-group-item
        if contribuyentes and not already_selected:
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
        logging.info("Representado ya seleccionado: %s, contribuyentes: %d", formatted_cuit, len(contribuyentes))
        return contribuyentes

    # Si no se selecciono directo en changeRelation, probar via "Representar a..."
    if not selected:
        await click_first([
            page.get_by_title("Representar a..."),
            page.get_by_text("Representar a...", exact=False),
        ], timeout=6_000)
        await page.wait_for_timeout(2000)

        # Reintentar extraccion si aun esta vacia
        if not contribuyentes:
            contribuyentes = await _get_representados_disponibles(page)
            logging.info("Contribuyentes disponibles (post-click): %d", len(contribuyentes))

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

    return contribuyentes


async def _download_libros_iva(
    page: Page,
    cuit_representado: str,
    periodos: list[str],
    download_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    descargados: list[dict[str, Any]] = []
    errores: list[dict[str, Any]] = []

    await ensure_session_active(page)
    if "/init" not in page.url:
        await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
        await page.wait_for_load_state("networkidle", timeout=20_000)
        await page.wait_for_timeout(1_500)
    await _goto_libros_listado(page)

    periodos_seleccionables = await _scrape_periodos(page)

    for idx, periodo in enumerate(periodos):
        periodo_normalizado = parse_periodo_from_input(periodo)
        logging.info("Procesando Libro IVA %s", periodo_normalizado)

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
            err = {"periodo": periodo, "tipo": None, "detalle": "Libro IVA", "error": f"No se encontraron filas para {periodo_normalizado}"}
            errores.append(err)
            logging.info("No se encontraron filas para Libro IVA %s", periodo_normalizado)
            continue

        for row_idx, (ti, ri, secuencia) in enumerate(rows_info):
            tipo = _secuencia_to_tipo(secuencia)
            logging.info("Descargando Libro IVA %s - %s (%d/%d)", periodo_normalizado, tipo, row_idx + 1, len(rows_info))

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
                    except Exception:
                        popup_task.cancel()
                fn = _build_filename(cuit_representado, periodo, tipo, "Vista Previa", ".zip")
                local_path = download_dir / fn
                await d.save_as(str(local_path))

                descargados.append({
                    "periodo": periodo,
                    "tipo": tipo,
                    "detalle": "Vista Previa",
                    "archivo": fn,

                })
            except Exception as e:
                errores.append({"periodo": periodo, "tipo": tipo, "detalle": "Vista Previa", "error": str(e)})
                logging.error("Error Vista Previa %s: %s", tipo, e)

            # Acuse
            try:
                async with page.expect_download(timeout=45_000) as di:
                    await page.locator("#btnDescargarAcuse").click()
                fn = _build_filename(cuit_representado, periodo, tipo, "Acuse")
                local_path = download_dir / fn
                await (await di.value).save_as(str(local_path))

                descargados.append({
                    "periodo": periodo,
                    "tipo": tipo,
                    "detalle": "Acuse",
                    "archivo": fn,

                })
            except Exception as e:
                errores.append({"periodo": periodo, "tipo": tipo, "detalle": "Acuse", "error": str(e)})
                logging.error("Error Acuse %s: %s", tipo, e)

            # Presentacion
            try:
                async with page.expect_download(timeout=45_000) as di:
                    await page.locator("#btnDescargarPresentacion").click()
                fn = _build_filename(cuit_representado, periodo, tipo, "Presentacion")
                local_path = download_dir / fn
                await (await di.value).save_as(str(local_path))

                descargados.append({
                    "periodo": periodo,
                    "tipo": tipo,
                    "detalle": "Presentacion",
                    "archivo": fn,

                })
            except Exception as e:
                errores.append({"periodo": periodo, "tipo": tipo, "detalle": "Presentacion", "error": str(e)})
                logging.error("Error Presentacion %s: %s", tipo, e)

            await page.goto("https://siapweb.cloud.afip.gob.ar/iva/#/init")
            await page.wait_for_load_state("networkidle", timeout=20_000)
            await page.wait_for_timeout(2_000)

        if idx < len(periodos) - 1:
            await _goto_libros_listado(page)

    return descargados, errores, periodos_seleccionables


async def _download_ddjj(
    page: Page,
    cuit_representado: str,
    periodos: list[str],
    download_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    descargados: list[dict[str, Any]] = []
    errores: list[dict[str, Any]] = []

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

                # Si es "Ver declaraciOn jurada" (F2002), hacer click en "visualizar formulario"
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
                local_path = download_dir / fn

                try:
                    if descarga is not None:
                        # El formulario llegó como descarga directa del portal.
                        await descarga.save_as(str(local_path))
                    else:
                        await pdf_page.pdf(path=str(local_path))
                    if not local_path.read_bytes().startswith(b"%PDF"):
                        raise RuntimeError("el documento de DDJJ no es un PDF válido")

                    descargados.append({
                        "periodo": periodo,
                        "tipo": tipo,
                        "detalle": "DDJJ",
                        "archivo": fn,

                    })
                    logging.info("Descargado DDJJ %s", fn)
                except Exception as e:
                    errores.append({"periodo": periodo, "tipo": tipo, "detalle": "DDJJ", "error": str(e)})
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
            errores.append({"periodo": periodo, "tipo": None, "detalle": "DDJJ", "error": f"No se encontro DDJJ para {periodo_normalizado}"})
            logging.info("No se encontro DDJJ %s", periodo_normalizado)

    return descargados, errores


async def bot_libros_portal_iva(
    cuit_representante: str,
    clave: str,
    cuit_representado: Optional[str] = None,
    periodo_desde: str = "",
    periodo_hasta: str = "",
    denominacion: str = "",
    proxy_request: bool = False,
    headless: bool = True,
    operacion: str = "descargar",
) -> tuple[dict[str, Any], list[str], bool]:
    """Descarga libros IVA y/o DDJJ presentadas del Portal IVA.

    ``operacion`` (paridad con las operaciones del worker V3):
    ``descargar`` = libros + DDJJ (default, flujo V1), ``descargar_libros`` =
    solo libros IVA, ``descargar_ddjj`` = solo DDJJ presentadas.
    """
    results: dict[str, Any] = {
        "periodos_descargados": [],
        "periodos_error": [],
        "periodos_seleccionables": [],
        "contribuyentes_seleccionables": [],
    }
    error_logs: list[str] = []

    operacion_norm = (operacion or "descargar").strip().lower()
    if operacion_norm not in ("descargar", "descargar_libros", "descargar_ddjj"):
        error_logs.append(f"Operación inválida: {operacion}")
        return results, error_logs, False

    cuit_rep = (cuit_representante or "").strip()
    cuit_obj = (cuit_representado or cuit_rep).strip()

    if not cuit_rep:
        error_logs.append("El CUIT del representante es obligatorio")
        return results, error_logs, False
    if not (clave or "").strip():
        error_logs.append("La clave fiscal es obligatoria")
        return results, error_logs, False
    if not periodo_desde or not periodo_hasta:
        error_logs.append("Los periodos desde y hasta son obligatorios")
        return results, error_logs, False

    periodos = generate_period_range(periodo_desde, periodo_hasta)
    if not periodos:
        error_logs.append("Rango de periodos invalido")
        return results, error_logs, False

    proxy = get_proxy_config() if proxy_request else None

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None
    portal_page: Optional[Page] = None

    try:
        async with limit_browser(), async_playwright() as playwright:
            browser, context, portal_page = await _login_and_open_portal(
                playwright, cuit_rep, clave, headless, proxy,
            )

            contribuyentes = await _select_representado(portal_page, cuit_obj, cuit_rep)
            results["contribuyentes_seleccionables"] = contribuyentes

            with tempfile.TemporaryDirectory(prefix="libros_iva_") as tmpdir:
                download_dir = Path(tmpdir)

                if operacion_norm in ("descargar", "descargar_libros"):
                    liva_desc, liva_err, periodos_sel = await _download_libros_iva(
                        portal_page, cuit_obj, periodos, download_dir,
                    )
                    results["periodos_descargados"].extend(liva_desc)
                    results["periodos_error"].extend(liva_err)
                    if periodos_sel:
                        results["periodos_seleccionables"] = periodos_sel

                if operacion_norm in ("descargar", "descargar_ddjj"):
                    try:
                        ddjj_desc, ddjj_err = await _download_ddjj(
                            portal_page, cuit_obj, periodos, download_dir,
                        )
                        results["periodos_descargados"].extend(ddjj_desc)
                        results["periodos_error"].extend(ddjj_err)
                    except Exception as ddjj_e:
                        err_msg = f"Error en descarga de DDJJ: {ddjj_e}"
                        logging.error(err_msg)
                        error_logs.append(err_msg)

            success = len(results["periodos_descargados"]) > 0 and len(results["periodos_error"]) == 0
            if results["periodos_descargados"] and results["periodos_error"]:
                success = True

            return results, error_logs, success

    except Exception as e:
        logging.error("Error en bot_libros_portal_iva: %s", e)
        error_logs.append(str(e))
        return results, error_logs, False
    finally:
        if context:
            try:
                asyncio.ensure_future(context.close())
            except Exception:
                pass
        if browser:
            try:
                asyncio.ensure_future(browser.close())
            except Exception:
                pass
