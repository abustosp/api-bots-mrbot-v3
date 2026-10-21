"""
SIPER Bot Logger Version
Based on original siper_bot.py with DebugCapture for logging and debugging.
"""
import asyncio
import base64
import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright
from shared.arca_login import login_arca
from shared.proxy import get_proxy_config

BOT_NAME = "siper"

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


async def bot_siper_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str = None,
    detalle_b64: bool = True,
    categorias_b64: bool = True,
    proxy_request: bool = False,
) -> tuple[dict[str, str], list[str], bool]:
    """
    Logger version of SIPER bot with DebugCapture for diagnostics.
    """
    results: dict[str, str] = {}
    error_logs: list[str] = []
    success = True
    
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot SIPER Logger")

    async def _capturar_section_container(
        page, nombre_archivo: str,
        devolver_b64: bool,
        clave_b64: str,
    ):
        """Toma captura de #sectionContainer y opcionalmente devuelve en b64."""
        container = page.locator("#sectionContainer")
        try:
            await container.wait_for(state="visible", timeout=15000)
        except Exception as e:
            if await container.count() == 0:
                container = page.locator("body")
            else:
                error_logs.append(f"No se encontró #sectionContainer para {clave_b64}: {e}")
                return

        try:
            # Guardar screenshot local
            screenshot_path = debug.descargas_dir / f"{nombre_archivo}.png"
            await container.screenshot(path=str(screenshot_path))
            debug.log(f"Screenshot guardado: {screenshot_path}")
            results[f"{clave_b64}_path"] = str(screenshot_path)

            # Devolver en base64 si corresponde
            if devolver_b64:
                with open(screenshot_path, "rb") as f:
                    results[clave_b64] = base64.b64encode(f.read()).decode("utf-8")

        except Exception as e:
            error_logs.append(f"Error al capturar {clave_b64}: {e}")

    try:
        async with async_playwright() as pw:
            proxy = get_proxy_config() if proxy_request else None
            
            har_path = debug.har_dir / "siper.har"
            browser = await pw.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(record_har_path=str(har_path), record_har_content="embed")
            page = await context.new_page()
            
            await debug.capture(page, "inicio")

            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
                login_url=(
                    "https://auth.afip.gob.ar/contribuyente_/login.xhtml"
                    "?action=SYSTEM&system=padron-puc-consulta-internet"
                ),
            )
            await debug.capture(page, "despues_login")
            
            if not login_result.success:
                debug.log(f"Login falló: {error_logs}")
                await context.close()
                await browser.close()
                return results, error_logs, False

            # Seleccionar representado si corresponde
            cuit_a_consultar = cuit_representado if cuit_representado else cuit_representante
            cuit_sin_guiones = cuit_a_consultar.replace("-", "")
            
            try:
                await page.goto("https://seti.afip.gob.ar/padron-puc-consulta-internet/ResponsiveIndexInternetAction.do")
                await page.wait_for_load_state("networkidle", timeout=10000)
                await debug.capture(page, "pagina_seleccion")

                sel_persona = page.locator("div.selPersona")
                if await sel_persona.count() > 0:
                    debug.log(f"Seleccionando representado: {cuit_sin_guiones}")
                    anchor_direct = page.locator(f"a[href*='idPersona={cuit_sin_guiones}']").first
                    try:
                        if await anchor_direct.count() > 0:
                            await anchor_direct.scroll_into_view_if_needed()
                            await anchor_direct.click()
                            await page.wait_for_load_state("networkidle", timeout=10000)
                        else:
                            debug.log("Anchor idPersona no encontrado, usando fallback")
                            panel_cuit = page.locator("div.panel-body").filter(has_text=re.compile(r"CUIT \d{2}-\d{8}-\d"))
                            if await panel_cuit.count() > 0:
                                enlace = panel_cuit.first.locator("xpath=ancestor::a[1]")
                                if await enlace.count() > 0:
                                    await enlace.scroll_into_view_if_needed()
                                    await enlace.click()
                                    await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception as e:
                        error_logs.append(f"Error selección representado: {e}")
            except Exception as e:
                error_logs.append(f"Error al seleccionar representado: {e}")
                await debug.capture(page, "error_seleccion")

            # Navegación directa a detalle SIPER
            try:
                debug.log("Navegando a detalle SIPER")
                await page.goto(f"https://seti.afip.gob.ar/padron-puc-consulta-internet/ResponsiveIndexInternetAction.do?idPersona={cuit_sin_guiones}")
                await page.wait_for_load_state("networkidle")
                await page.goto("https://seti.afip.gob.ar/padron-puc-consulta-internet/gov.afip.padron.puc.conductafiscal.gwt.ConductaFiscal/ConductaFiscalAction.do")
                await page.wait_for_load_state("networkidle")
                await debug.capture(page, "detalle_siper")
            except Exception as e:
                error_logs.append(f"No se pudo acceder al detalle SIPER: {e}")
                await debug.capture(page, "error_detalle_siper")
                await context.close()
                await browser.close()
                return results, error_logs, False

            time_now = datetime.now().strftime("%Y%m%d_%H%M%S")

            # Extraer datos estructurados del detalle SIPER
            try:
                await page.wait_for_selector("td.conductaFiscalName:has-text('Contribuyente')", timeout=15000)
                base_table = page.locator("td.conductaFiscalName:has-text('Contribuyente')").first.locator("xpath=ancestor::table[1]")
                html_section = await base_table.inner_html()
                
                def _get(label, pattern=None, join_spans=False):
                    nonlocal html_section
                    if pattern is None:
                        pattern = rf'class="conductaFiscalName">\s*{label}\s*</td>.*?class="conductaFiscalValue">\s*<div[^>]*>(.*?)</div>'
                    m = re.search(pattern, html_section, re.DOTALL|re.IGNORECASE)
                    if not m:
                        return ""
                    val = m.group(1)
                    if join_spans:
                        val = re.sub(r'<[^>]+>', ' ', val)
                    val = re.sub(r'\s+', ' ', val).strip()
                    return val
                
                contribuyente = _get("Contribuyente")
                categoria_raw = _get("Categoría SIPER", pattern=r'class="conductaFiscalName">\s*Categoría SIPER\s*</td>.*?<div class="conductaFiscalLink">(.*?)</div>', join_spans=True)
                motivo = _get("Motivo")
                fecha_ult = _get("Fecha última Categoría SIPER")
                estado_disconf = _get("Estado trámite de disconformidad")
                
                if contribuyente: results["contribuyente"] = contribuyente
                if categoria_raw: results["categoria_siper"] = categoria_raw
                if motivo: results["motivo"] = motivo
                if fecha_ult: results["fecha_ultima_categoria_siper"] = fecha_ult
                if estado_disconf: results["estado_tramite_disconformidad"] = estado_disconf
                
                debug.log(f"Datos extraídos: contribuyente={contribuyente}, categoria={categoria_raw}")
            except Exception as e:
                error_logs.append(f"Error al extraer datos SIPER: {e}")
                await debug.capture(page, "error_extraccion")

            # Captura de detalle SIPER
            nombre_detalle = f"siper_detalle_{cuit_representante}_{time_now}"
            await _capturar_section_container(
                page=page,
                nombre_archivo=nombre_detalle,
                devolver_b64=detalle_b64,
                clave_b64="detalle_b64",
            )

            # Click en "Historia de las Categorías" y captura
            try:
                debug.log("Buscando enlace Historia de las Categorías")
                texto_historia_variantes = ["Historia de las Categorías", "(Historia de las Categorías)", "(Historia de las Categorías"]
                enlace_historia_loc = None
                for t in texto_historia_variantes:
                    loc = page.get_by_text(t)
                    try:
                        await loc.wait_for(state="visible", timeout=2000)
                        enlace_historia_loc = loc
                        break
                    except Exception:
                        continue
                        
                if enlace_historia_loc:
                    await enlace_historia_loc.click()
                    await page.wait_for_load_state("networkidle", timeout=5000)
                    await debug.capture(page, "historia_categorias")
                    
                    nombre_categorias = f"siper_categorias_{cuit_representante}_{time_now}"
                    await _capturar_section_container(
                        page=page,
                        nombre_archivo=nombre_categorias,
                        devolver_b64=categorias_b64,
                        clave_b64="categorias_b64",
                    )
                else:
                    error_logs.append("No se encontró el enlace 'Historia de las Categorías'")
                    
            except Exception as e:
                error_logs.append(f"No se pudo acceder a 'Historia de las Categorías': {e}")
                await debug.capture(page, "error_historia")

            # Cierre ordenado
            await context.close()
            await browser.close()
            try:
                json_path = debug.descargas_dir / f"siper_scrape_{cuit_sin_guiones}_{time_now}.json"
                with json_path.open("w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                results["scrape_json_path"] = str(json_path)
                debug.log(f"JSON guardado: {json_path}")
            except Exception as e:
                error_logs.append(f"Error al guardar JSON de SIPER: {e}")

            debug.log(f"Bot completado. Results: {list(results.keys())}")

    except Exception as e:
        error_logs.append(f"Error global en bot_siper: {e}")
        debug.log(f"Error global: {e}")
        success = False

    return results, error_logs, success


async def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    
    cuit = os.getenv("SIPER_CUIT_REPRESENTANTE", "")
    clave = os.getenv("SIPER_CLAVE", "")
    cuit_representado = os.getenv("SIPER_CUIT_REPRESENTADO", cuit)
    
    if not cuit or not clave:
        print("ERROR: Faltan SIPER_CUIT_REPRESENTANTE y/o SIPER_CLAVE en .env")
        return
    
    print(f"Ejecutando bot SIPER Logger para CUIT: {cuit}")
    results, errors, success = await bot_siper_logger(cuit, clave, cuit_representado)
    print(f"Success: {success}")
    print(f"Results: {list(results.keys())}")
    if errors:
        print(f"Errors: {errors}")

if __name__ == "__main__":
    asyncio.run(main())
