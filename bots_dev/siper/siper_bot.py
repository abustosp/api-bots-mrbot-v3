import asyncio
import base64
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright
from shared.arca_login import login_arca
from shared.proxy import get_proxy_config

async def bot_siper(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str = None,
    detalle_b64: bool = True,
    categorias_b64: bool = True,
    proxy_request: bool = False,
) -> tuple[dict[str, str], list[str], bool]:
    """
    Ejecuta el bot de SIPER (Common Version), toma 2 capturas y extrae datos:
      1) Detalle SIPER
      2) Historia de las categorías
    """
    results: dict[str, str] = {}
    error_logs: list[str] = []
    success = True
    
    # Helper similar to logger but without debug
    async def _capturar_section_container(
        page,
        nombre_archivo: str,
        devolver_b64: bool,
        clave_b64: str,
        clave_path: str,
        output_dir: Path,
    ):
        container = page.locator("#sectionContainer")
        try:
            await container.wait_for(state="visible", timeout=15000)
        except Exception as e:
            if await container.count() == 0:
                container = page.locator("body")
            else:
                error_logs.append(f"No se encontró #sectionContainer para {clave_b64}: {e}")
                return

        output_dir.mkdir(parents=True, exist_ok=True)
        file_path = output_dir / nombre_archivo
        
        try:
            await container.screenshot(path=str(file_path))
            results[clave_path] = str(file_path)

            if devolver_b64:
                with open(file_path, "rb") as f:
                    results[clave_b64] = base64.b64encode(f.read()).decode("utf-8")

        except Exception as e:
            error_logs.append(f"Error al capturar {clave_b64}: {e}")

    try:
        async with async_playwright() as pw:
            proxy = get_proxy_config() if proxy_request else None

            browser = await pw.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context()
            page = await context.new_page()

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
            if not login_result.success:
                await context.close()
                await browser.close()
                return results, error_logs, False

            # Seleccionar representado
            cuit_a_consultar = cuit_representado if cuit_representado else cuit_representante
            cuit_sin_guiones = cuit_a_consultar.replace("-", "")
            
            try:
                await page.goto("https://seti.afip.gob.ar/padron-puc-consulta-internet/ResponsiveIndexInternetAction.do")
                await page.wait_for_load_state("networkidle", timeout=10000)

                sel_persona = page.locator("div.selPersona")
                if await sel_persona.count() > 0:
                    anchor_direct = page.locator(f"a[href*='idPersona={cuit_sin_guiones}']").first
                    try:
                        if await anchor_direct.count() > 0:
                            await anchor_direct.scroll_into_view_if_needed()
                            await anchor_direct.click()
                            await page.wait_for_load_state("networkidle", timeout=10000)
                        else:
                            # Fallback
                            panel_cuit = page.locator("div.panel-body").filter(has_text=re.compile(r"CUIT \d{2}-\d{8}-\d"))
                            if await panel_cuit.count() > 0:
                                enlace = panel_cuit.first.locator("xpath=ancestor::a[1]")
                                if await enlace.count() > 0:
                                    await enlace.scroll_into_view_if_needed()
                                    await enlace.click()
                                    await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
            except Exception as e:
                error_logs.append(f"Error al seleccionar representado: {e}")

            # Navegación directa
            try:
                await page.goto(f"https://seti.afip.gob.ar/padron-puc-consulta-internet/ResponsiveIndexInternetAction.do?idPersona={cuit_sin_guiones}")
                await page.wait_for_load_state("networkidle", timeout=10000)
                await page.goto("https://seti.afip.gob.ar/padron-puc-consulta-internet/gov.afip.padron.puc.conductafiscal.gwt.ConductaFiscal/ConductaFiscalAction.do")
                await page.wait_for_load_state("networkidle", timeout=10000)
                frame_loc = page
            except Exception as e:
                error_logs.append(f"No se pudo acceder al detalle SIPER directo: {e}")
                await context.close()
                await browser.close()
                return results, error_logs, False

            time_now = datetime.now().strftime("%Y%m%d_%H%M%S")
            base_dir = Path("./descargas/siper")
            target_cuit = cuit_representado or cuit_representante
            output_dir = base_dir / target_cuit / time_now
            output_dir.mkdir(parents=True, exist_ok=True)

            # EXTRAER DATOS (Ported from Logger)
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
            except Exception as e:
                error_logs.append(f"Error al extraer datos SIPER: {e}")

            # Captura Detalle
            nombre_detalle = f"siper_detalle_{cuit_representante}_{time_now}.png"
            await _capturar_section_container(
                page=page,
                nombre_archivo=nombre_detalle,
                devolver_b64=detalle_b64,
                clave_b64="detalle_b64",
                clave_path="detalle_path",
                output_dir=output_dir,
            )

            # Historia Categorias
            try:
                texto_historia_variantes = ["Historia de las Categorías", "(Historia de las Categorías)", "(Historia de las Categorías", "Historia de las Categorías)"]
                found_history = False
                for t in texto_historia_variantes:
                    loc = frame_loc.get_by_text(t)
                    if await loc.count() > 0 and await loc.first.is_visible():
                        await loc.first.click()
                        found_history = True
                        break
                
                if not found_history:
                     link = frame_loc.get_by_role("link", name=re.compile("Historia de las Categorías", re.I))
                     if await link.count() > 0:
                         await link.first.click()
                         found_history = True

                if found_history:
                    await page.wait_for_load_state("networkidle", timeout=5000)
                    nombre_categorias = f"siper_categorias_{cuit_representante}_{time_now}.png"
                    await _capturar_section_container(
                        page=page,
                        nombre_archivo=nombre_categorias,
                        devolver_b64=categorias_b64,
                        clave_b64="categorias_b64",
                        clave_path="categorias_path",
                        output_dir=output_dir,
                    )
                else:
                    error_logs.append("No se encontró el enlace 'Historia de las Categorías'")

            except Exception as e:
                error_logs.append(f"No se pudo acceder a 'Historia de las Categorías': {e}")

            await context.close()
            await browser.close()

            try:
                json_path = output_dir / f"siper_scrape_{target_cuit}_{time_now}.json"
                with json_path.open("w", encoding="utf-8") as f:
                    json.dump(results, f, ensure_ascii=False, indent=2)
                results["scrape_json_path"] = str(json_path)
            except Exception as e:
                error_logs.append(f"Error al guardar JSON de SIPER: {e}")

    except Exception as e:
        error_logs.append(f"Error global en bot_siper: {e}")
        success = False

    return results, error_logs, success


async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    cuit = os.getenv("SIPER_CUIT_REPRESENTANTE", "")
    clave = os.getenv("SIPER_CLAVE_REPRESENTANTE", "")
    cuit_rep = os.getenv("SIPER_CUIT_REPRESENTADO", "")
    
    if cuit and clave:
        res = await bot_siper(cuit, clave, cuit_rep)
        print("Success:", res[2])
        print("Archivos:", res[0])

if __name__ == "__main__":
    asyncio.run(main())
