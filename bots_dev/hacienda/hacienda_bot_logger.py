"""
Hacienda Bot Logger Version
Imports original hacienda_bot functions and wraps them with DebugCapture for logging.
"""
import asyncio
import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Any

from playwright.async_api import async_playwright, Page

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

BOT_NAME = "hacienda"

# Import original hacienda functions
try:
    from hacienda_bot_original import (
        descargar_hacienda,
        bot_hacienda,
        similarity_score,
        normalize_name_for_match,
        COMPROBANTES_SERVICE_PATTERN,
        HACIENDA_SERVICE_PATTERN,
    )
    HAS_ORIGINAL = True
except ImportError:
    HAS_ORIGINAL = False

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


async def click_and_get_target_page_logger(host_page: Page, click_target, debug: DebugCapture, timeout_ms: int = 12000) -> Page:
    """Click and wait for popup, returning the target page."""
    popup_page = None
    try:
        popup_waiter = asyncio.create_task(host_page.wait_for_event("popup", timeout=timeout_ms))
        await click_target.click()
        try:
            popup_page = await popup_waiter
        except Exception:
            popup_page = None
        finally:
            if not popup_waiter.done():
                popup_waiter.cancel()
    except Exception as e:
        debug.log(f"Error in click_and_get_target_page: {e}")
    
    target_page = popup_page or host_page
    try:
        await target_page.wait_for_load_state("domcontentloaded", timeout=10000)
    except Exception:
        pass
    try:
        await target_page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    return target_page


async def seleccionar_denominacion_logger(page: Page, denominacion: str, debug: DebugCapture) -> bool:
    """Selecciona la denominación en popups de AFIP siguiendo patrón del bot original."""
    if not denominacion:
        debug.log("No se especificó denominación, omitiendo selección")
        return True
    
    target_text = denominacion.strip()
    debug.log(f"Buscando denominación: {target_text}")
    
    # Esperar a que la página cargue
    try:
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass
    
    async def click_candidate(candidate) -> bool:
        """Intenta hacer click en un candidato con múltiples estrategias."""
        try:
            await candidate.scroll_into_view_if_needed()
        except Exception:
            pass
        
        for kwargs in ({"timeout": 5000}, {"timeout": 5000, "force": True}):
            try:
                await candidate.click(**kwargs)
                await page.wait_for_load_state("networkidle", timeout=5000)
                return True
            except Exception:
                continue
        
        # Fallback JS click
        try:
            handle = await candidate.element_handle()
            if handle:
                await handle.evaluate("el => el.click()")
                await page.wait_for_load_state("networkidle", timeout=5000)
                return True
        except Exception:
            pass
        return False
    
    async def click_first_match(locator) -> bool:
        """Hace click en el primer elemento visible que coincida."""
        count = await locator.count()
        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if await candidate.is_visible():
                    if await click_candidate(candidate):
                        debug.log(f"Click exitoso en índice {idx}")
                        return True
            except Exception:
                continue
        return False
    
    # 1) Exacto case-sensitive (button y link)
    debug.log("Intentando match exacto case-sensitive")
    exact_locators = [
        page.get_by_role("button", name=target_text, exact=True),
        page.get_by_role("link", name=target_text, exact=True),
    ]
    for locator in exact_locators:
        if await click_first_match(locator):
            debug.log(f"Seleccionada denominación con match exacto")
            return True
    
    # 2) Exacto case-insensitive
    debug.log("Intentando match case-insensitive")
    insensitive_pattern = re.compile(rf"^{re.escape(target_text)}$", re.I)
    ci_locators = [
        page.get_by_role("button", name=insensitive_pattern),
        page.get_by_role("link", name=insensitive_pattern),
    ]
    for locator in ci_locators:
        if await click_first_match(locator):
            debug.log(f"Seleccionada denominación con match case-insensitive")
            return True
    
    # 3) Fuzzy matching como fallback
    debug.log("Intentando match fuzzy")
    best_score = 0.0
    best_candidate = None
    best_text = ""
    target_normalized = target_text.upper().strip()
    
    # Buscar en buttons y links
    for role in ("button", "link"):
        elements = page.get_by_role(role)
        count = await elements.count()
        
        for i in range(count):
            try:
                elem = elements.nth(i)
                text = (await elem.inner_text() or "").strip()
                
                if not text or len(text) < 3:
                    continue
                
                text_normalized = text.upper().strip()
                
                # Calcular score con preferencia por match exacto y penalización por longitud
                if target_normalized == text_normalized:
                    score = 2.0  # Exact normalized
                elif target_normalized in text_normalized:
                    # Penalizar textos más largos que contienen el target
                    len_ratio = len(target_normalized) / len(text_normalized)
                    score = 0.5 + (0.5 * len_ratio)
                elif text_normalized in target_normalized:
                    score = 0.7
                else:
                    # Jaccard similarity
                    words_target = set(target_normalized.split())
                    words_text = set(text_normalized.split())
                    if words_target and words_text:
                        intersection = len(words_target & words_text)
                        union = len(words_target | words_text)
                        score = intersection / union if union > 0 else 0
                    else:
                        score = 0
                
                # Penalizar por diferencia de longitud
                len_penalty = abs(len(text_normalized) - len(target_normalized)) * 0.002
                final_score = score - len_penalty
                
                debug.log(f"Candidato '{text}': score={final_score:.3f}")
                
                if final_score > best_score:
                    best_score = final_score
                    best_candidate = elem
                    best_text = text
                    debug.log(f"Nuevo mejor: '{text}' score={final_score:.3f}")
            except Exception:
                continue
    
    if best_candidate and best_score > 0.4:
        if await click_candidate(best_candidate):
            debug.log(f"Seleccionada denominación fuzzy '{best_text}' score={best_score:.3f}")
            return True
    
    debug.log("No se encontró denominación coincidente")
    return False


async def _menu_is_open(page: Page) -> bool:
    """Check if sidebar menu is open."""
    sidebar = page.locator("div.sidebar, nav.sidebar, aside.sidebar, #sidebar")
    if await sidebar.count() > 0:
        for i in range(await sidebar.count()):
            try:
                if await sidebar.nth(i).is_visible():
                    return True
            except Exception:
                pass
    
    nav_collapse = page.locator(".navbar-collapse.in, .navbar-collapse.show")
    if await nav_collapse.count() > 0:
        return True
    
    offcanvas = page.locator(".offcanvas.show, .offcanvas-start.show")
    if await offcanvas.count() > 0:
        return True
    
    return False


async def open_menu_if_needed(page: Page, debug: DebugCapture) -> None:
    """Click hamburger menu to open sidebar navigation."""
    menu_candidates = [
        page.locator("#buttonSidebar button.hamburger[data-toggle='offcanvas']"),
        page.locator("button.hamburger[data-toggle='offcanvas']"),
        page.locator("button[data-toggle='offcanvas']"),
        page.get_by_role("button", name=re.compile(r"Men[úu]|Menu", re.I)),
        page.locator("button.navbar-toggle"),
        page.locator("button:has(i.fa-bars)"),
        page.locator("button:has-text('☰')"),
        page.locator("#buttonSidebar button"),
        page.locator("button.hamburger"),
    ]
    
    if await _menu_is_open(page):
        debug.log("Menú ya está abierto")
        return
    
    for locator in menu_candidates:
        count = await locator.count()
        if count == 0:
            continue
        
        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if not await candidate.is_visible():
                    continue
            except Exception:
                continue
            
            try:
                await candidate.scroll_into_view_if_needed()
            except Exception:
                pass
            
            clicked = False
            try:
                await candidate.click(timeout=3000)
                clicked = True
                debug.log(f"Clicked menu button variant {menu_candidates.index(locator)}")
            except Exception:
                try:
                    await candidate.click(timeout=3000, force=True)
                    clicked = True
                except Exception:
                    pass
            
            if not clicked:
                try:
                    handle = await candidate.element_handle()
                    if handle is not None:
                        await handle.evaluate("el => el.click()")
                        clicked = True
                except Exception:
                    pass
            
            if not clicked:
                continue
            
            await asyncio.sleep(0.35)
            if await _menu_is_open(page):
                debug.log("Menú abierto exitosamente")
                return
    
    # Fallback: try JS click
    try:
        toggled = await page.evaluate("""
            () => {
                const btn = 
                    document.querySelector('#buttonSidebar button.hamburger[data-toggle="offcanvas"]') ||
                    document.querySelector('button.hamburger[data-toggle="offcanvas"]') ||
                    document.querySelector('button[data-toggle="offcanvas"]') ||
                    document.querySelector('#buttonSidebar button');
                if (btn) {
                    btn.click();
                    return true;
                }
                return false;
            }
        """)
        if toggled:
            debug.log("Menú abierto via JS")
            await asyncio.sleep(0.35)
    except Exception as e:
        debug.log(f"JS menu toggle failed: {e}")


async def bot_hacienda_logger(
    cuit_representante: str,
    clave: str,
    denominacion: str,
    desde: str,
    hasta: str,
    cuit_representado: str = None,
    proxy_request: bool = False,
    **kwargs
) -> Tuple[Dict[str, Any], List[str], bool]:
    """Logger version of Hacienda bot with DebugCapture."""
    error_logs: List[str] = []
    results: Dict[str, Any] = {}
    
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log(f"Iniciando bot Hacienda Logger")
    debug.log(f"CUIT: {cuit_representante}, Denominación: {denominacion}")
    debug.log(f"Período: {desde} - {hasta}")
    
    download_root = debug.descargas_dir
    proxy = get_proxy_config() if proxy_request else None
    
    try:
        async with async_playwright() as pw:
            har_path = debug.har_dir / "hacienda.har"
            browser = await pw.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(
                accept_downloads=True, 
                record_har_path=str(har_path), 
                record_har_content="embed"
            )
            page = await context.new_page()
            await debug.capture(page, "inicio")
            
            # Login
            login_result = await login_arca(page, cuit_representante, clave, error_logs=error_logs)
            await debug.capture(page, "login_completo")
            
            if not login_result.success:
                debug.log(f"Login falló: {error_logs}")
                await context.close()
                await browser.close()
                return results, error_logs, False
            
            # Abrir Comprobantes en Línea
            debug.log("Abriendo Comprobantes en Línea")
            comp_page = await open_arca_service(
                page, 
                service_name="COMPROBANTES EN LINEA", 
                service_matchers=(re.compile(r"COMPROBANTES\s+EN\s+L[ÍI]NEA", re.I),)
            )
            if not comp_page:
                debug.log("No se pudo abrir Comprobantes en Línea")
                error_logs.append("No se pudo abrir Comprobantes en Línea")
                await context.close()
                await browser.close()
                return results, error_logs, False
            
            await debug.capture(comp_page, "comprobantes_abierto")
            
            # Select denomination - required to proceed to main menu
            # The page shows "Seleccione la Empresa a representar" with buttons
            debug.log("Seleccionando denominación en página de empresas")
            await seleccionar_denominacion_logger(comp_page, denominacion, debug)
            await comp_page.wait_for_load_state("networkidle", timeout=5000)
            await debug.capture(comp_page, "despues_seleccion_empresa")
            
            
            # Open Hacienda using the "open twice" pattern from original bot
            # The Hacienda button is actually a link with ID btn_fwd_lsp
            debug.log("Buscando servicio Hacienda")
            hacienda_btn = comp_page.locator("#btn_fwd_lsp")
            if await hacienda_btn.count() == 0:
                debug.log("ID btn_fwd_lsp no encontrado, buscando por texto")
                hacienda_btn = comp_page.get_by_role("link", name=re.compile(r"Hacienda.*Carne", re.I))
            if await hacienda_btn.count() == 0:
                hacienda_btn = comp_page.get_by_text("Hacienda y Carne")
                
            if await hacienda_btn.count() == 0:
                debug.log("FATAL: No se encontró botón Hacienda")
                await debug.capture(comp_page, "error_boton_hacienda")
                error_logs.append("No se encontró botón Hacienda")
                await context.close()
                await browser.close()
                return results, error_logs, False
            
            # FIRST OPEN: Open and close the popup to "prep" the service
            debug.log("Primera apertura de Hacienda (prep)")
            first_page = await click_and_get_target_page_logger(comp_page, hacienda_btn.first, debug)
            await debug.capture(first_page, "hacienda_primera_apertura")
            if first_page is not comp_page:
                try:
                    await first_page.close()
                except Exception:
                    pass
            
            # SECOND OPEN: This is the actual service page
            debug.log("Segunda apertura de Hacienda (real)")
            hacienda_page = await click_and_get_target_page_logger(comp_page, hacienda_btn.first, debug)
            await debug.capture(hacienda_page, "hacienda_segunda_apertura")
            
            if hacienda_page.url == comp_page.url:
                debug.log("ADVERTENCIA: URL de hacienda es igual a comprobantes")
            
            await seleccionar_denominacion_logger(hacienda_page, denominacion, debug)
            
            # Now we use the original logic for processing queries and downloads
            # We import the patterns and functions from original
            from hacienda_bot_original import (
                procesar_consulta, 
                QUERY_EMISOR_PATTERN, 
                QUERY_RECEPTOR_PATTERN,
                save_consolidated_excel
            )
            
            debug.log("Iniciando procesamiento de consultas (Emisor y Receptor)")
            
            emisor_dir = download_root / "por_emisor"
            receptor_dir = download_root / "por_receptor"
            emisor_dir.mkdir(parents=True, exist_ok=True)
            receptor_dir.mkdir(parents=True, exist_ok=True)
            
            debug.log("Procesando consulta: Por Emisor")
            emisor_files, emisor_frames = await procesar_consulta(
                hacienda_page,
                "por_emisor",
                QUERY_EMISOR_PATTERN,
                cuit_representante,
                desde,
                hasta,
                emisor_dir
            )
            debug.log(f"Descargados {len(emisor_files)} archivos de Emisor")
            await debug.capture(hacienda_page, "despues_emisor")
            
            debug.log("Procesando consulta: Por Receptor")
            receptor_files, receptor_frames = await procesar_consulta(
                hacienda_page,
                "por_receptor",
                QUERY_RECEPTOR_PATTERN,
                cuit_representante,
                desde,
                hasta,
                receptor_dir
            )
            debug.log(f"Descargados {len(receptor_files)} archivos de Receptor")
            await debug.capture(hacienda_page, "despues_receptor")
            
            debug.log("Generando Excel consolidado")
            excel_path = save_consolidated_excel(
                table_frames_by_query={
                    "por_emisor": emisor_frames,
                    "por_receptor": receptor_frames,
                },
                output_dir=download_root,
                cuit_representante=cuit_representante,
                denominacion=denominacion,
                desde=desde,
                hasta=hasta,
            )
            
            results = {
                "status": "success",
                "descargas_dir": str(download_root),
                "excel_consolidado": str(excel_path),
                "emisor_count": len(emisor_files),
                "receptor_count": len(receptor_files),
            }
            
            debug.log(f"Bot completado exitosamente. Excel: {excel_path}")
            
            await context.close()
            await browser.close()
            return results, error_logs, True
            
    except Exception as e:
        debug.log(f"Error general: {e}")
        error_logs.append(str(e))
        return results, error_logs, False


async def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    
    cuit = os.getenv("HACIENDA_CUIT_REPRESENTANTE", "")
    clave = os.getenv("HACIENDA_CLAVE", "")
    denominacion = os.getenv("HACIENDA_DENOMINACION", "SILVA EDGARDO DAVID")
    desde = os.getenv("HACIENDA_DESDE", "01/01/2020")
    hasta = os.getenv("HACIENDA_HASTA", "31/12/2020")
    
    if not cuit or not clave:
        print("ERROR: Faltan HACIENDA_CUIT_REPRESENTANTE y/o HACIENDA_CLAVE en .env")
        return
    
    print(f"Ejecutando bot Hacienda Logger para CUIT: {cuit}")
    results, errors, success = await bot_hacienda_logger(cuit, clave, denominacion, desde, hasta)
    print(f"Success: {success}")
    print(f"Results: {results}")
    if errors:
        print(f"Errors: {errors}")


if __name__ == "__main__":
    asyncio.run(main())
