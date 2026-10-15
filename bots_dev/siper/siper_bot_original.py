import asyncio
import os
import re
import tempfile
import base64
from datetime import datetime

from playwright.async_api import async_playwright
from shared.arca_login import login_arca
from shared.proxy import get_proxy_config

# Stub for subir_archivo_a_minio (not needed for local testing)
async def subir_archivo_a_minio(**kwargs):
    return "minio_disabled"

# ----------------------------------------------------------------------
# Configuración de MinIO (similar a SCT_bot.py, pero para SIPER)
# ----------------------------------------------------------------------

minio = os.getenv("MINIO", "False").lower() == "true"
minio_url_interna =  os.getenv("MINIO_URL_INTERNA")
minio_url_externa = os.getenv("MINIO_URL_EXTERNA")
minio_ssl = os.getenv("MINIO_SSL", "False").lower() == "true"
minio_interno = os.getenv("MINIO_INTERNO", "False").lower() == "true"

minio_bucket_siper = os.getenv("MINIO_BUCKET_SIPER", "siper")
minio_access_key_siper = os.getenv("MINIO_MRBOT_ACCESS_KEY")
minio_secret_key_siper = os.getenv("MINIO_MRBOT_SECRET_KEY")


# ----------------------------------------------------------------------
# Bot principal: estructura similar a bot_sct
# ----------------------------------------------------------------------

async def bot_siper(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str = None,
    detalle_b64: bool = True,
    categorias_b64: bool = True,
    detalle_minio: bool = True,
    categorias_minio: bool = True,
    proxy_request: bool = False,
) -> tuple[dict[str, str], list[str], bool]:
    """
    Ejecuta el bot de SIPER, toma 2 capturas:
      1) Detalle SIPER
      2) Historia de las categorías

    Args:
        cuit_representante (str): CUIT que se usa para loguearse en AFIP.
        clave_representante (str): Clave fiscal del contribuyente.
        cuit_representado (str): CUIT del representado (solo numérico). Si no se especifica, se usa el CUIT del representante.
        detalle_b64 (bool): Si True, devuelve la captura de detalle SIPER en base64 PNG.
        categorias_b64 (bool): Si True, devuelve la captura de historia de categorías en base64 PNG.
        detalle_minio (bool): Si True, sube la captura de detalle SIPER a MinIO y devuelve URL.
        categorias_minio (bool): Si True, sube la captura de historia de categorías a MinIO y devuelve URL.
        proxy_request (bool): Si en el futuro querés inyectar proxy (de momento no se usa).

    Returns:
        tuple[dict[str, str], list[str], bool]:
            - results: dict con claves:
                - "detalle_b64"
                - "categorias_b64"
                - "detalle_minio_url"
                - "categorias_minio_url"
            - error_logs: lista de strings con errores/avisos
            - success: bool indicando si el flujo general fue exitoso
    """

    results: dict[str, str] = {}
    error_logs: list[str] = []
    success = True

    # Helper interno para tomar screenshot de #sectionContainer
    async def _capturar_section_container(
        page, nombre_archivo_minio: str,
        devolver_b64: bool,
        subir_minio_flag: bool,
        clave_b64: str,
        clave_minio: str
        ):
        """
        Toma captura de #sectionContainer, opcionalmente la devuelve en b64
        y/o la sube a MinIO, guardando los resultados en el dict results.
        """
        container = page.locator("#sectionContainer")
        try:
            await container.wait_for(state="visible", timeout=15000)
        except Exception as e:
            if await container.count() == 0:
                container = page.locator("body")
            else:
                error_logs.append(f"No se encontró #sectionContainer para {clave_b64}/{clave_minio}: {e}")
                return

        tmp_path = None
        try:
            # Crear archivo temporal
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
            tmp_path = tmp.name
            tmp.close()

            # Hacer captura
            await container.screenshot(path=tmp_path)

            # Leer bytes
            with open(tmp_path, "rb") as f:
                img_bytes = f.read()

            # Devolver en base64 si corresponde
            if devolver_b64:
                results[clave_b64] = base64.b64encode(img_bytes).decode("utf-8")

            # Subir a MinIO si corresponde
            if subir_minio_flag:
                try:
                    url = await subir_archivo_a_minio(
                        minio_url_interna=minio_url_interna,
                        minio_url_externa=minio_url_externa,
                        minio_ssl=minio_ssl,
                        minio_interno=minio_interno,
                        minio_access_key=minio_access_key_siper,
                        minio_secret_key=minio_secret_key_siper,
                        minio_bucket=minio_bucket_siper,
                        archivo_a_subir=tmp_path,
                        nombre_archivo_en_bucket=nombre_archivo_minio)
                    results[clave_minio] = url
                except Exception as e:
                    error_logs.append(f"Error al subir {nombre_archivo_minio} a MinIO: {e}")

        except Exception as e:
            error_logs.append(f"Error al capturar {clave_b64}/{clave_minio}: {e}")
        finally:
            # Borrar archivo temporal
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass

    try:
        async with async_playwright() as pw:
            # Si en el futuro querés usar proxy, acá se arma el dict
            proxy = get_proxy_config() if proxy_request==True else None

            browser = await pw.chromium.launch(headless=True,
                                               proxy=proxy)
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

            # ------------------------------------------------------------------
            # Seleccionar representado si corresponde
            # ------------------------------------------------------------------
            cuit_a_consultar = cuit_representado if cuit_representado else cuit_representante
            # Remover guiones del CUIT para que coincida con el parámetro idPersona en el href
            cuit_sin_guiones = cuit_a_consultar.replace("-", "")
            
            try:
                # Intentar navegar a la URL que podría mostrar el menú de selección
                await page.goto("https://seti.afip.gob.ar/padron-puc-consulta-internet/ResponsiveIndexInternetAction.do")
                await page.wait_for_load_state("networkidle", timeout=10000)

                # Detectar si existe el menú de selección de personas
                sel_persona = page.locator("div.selPersona")
                if await sel_persona.count() > 0:
                    cuit_formateado = f"{cuit_sin_guiones[:2]}-{cuit_sin_guiones[2:10]}-{cuit_sin_guiones[10:]}"

                    # 1) Intento directo por href idPersona
                    anchor_direct = page.locator(f"a[href*='idPersona={cuit_sin_guiones}']").first
                    try:
                        if await anchor_direct.count() > 0:
                            await anchor_direct.scroll_into_view_if_needed()
                            await anchor_direct.click()
                            await page.wait_for_load_state("networkidle", timeout=10000)
                        else:
                            raise Exception("Anchor idPersona no encontrado")
                    except Exception as e_direct:
                        error_logs.append(f"Fallback selección representado (href): {e_direct}")
                        # 2) Fallback panel-body con texto CUIT
                        # Selector genérico usando regex para cualquier CUIT
                        panel_cuit = page.locator("div.panel-body").filter(has_text=re.compile(r"CUIT \d{2}-\d{8}-\d"))
                        if await panel_cuit.count() > 0:
                            enlace = panel_cuit.first.locator("xpath=ancestor::a[1]")
                            if await enlace.count() > 0:
                                try:
                                    await enlace.scroll_into_view_if_needed()
                                    await enlace.click()
                                    await page.wait_for_load_state("networkidle", timeout=10000)
                                except Exception as e_click:
                                    error_logs.append(f"Error click enlace representado: {e_click}")
                            else:
                                try:
                                    await panel_cuit.first.scroll_into_view_if_needed()
                                    await panel_cuit.first.click()
                                    await page.wait_for_load_state("networkidle", timeout=10000)
                                except Exception as e_panel_click:
                                    error_logs.append(f"Error click panel representado: {e_panel_click}")
                        else:
                            error_logs.append(f"No se encontró panel con texto CUIT {cuit_formateado}")
            except Exception as e:
                error_logs.append(f"Error al intentar seleccionar representado: {e}")

            # ------------------------------------------------------------------
            # Navegación directa a detalle SIPER (sin click intermedio)
            # ------------------------------------------------------------------
            try:
                # Redirección explícita con idPersona (CUIT representado sin guiones)
                await page.goto(f"https://seti.afip.gob.ar/padron-puc-consulta-internet/ResponsiveIndexInternetAction.do?idPersona={cuit_sin_guiones}")
                await page.wait_for_load_state("networkidle")
                # Luego ir al detalle SIPER
                await page.goto("https://seti.afip.gob.ar/padron-puc-consulta-internet/gov.afip.padron.puc.conductafiscal.gwt.ConductaFiscal/ConductaFiscalAction.do")
                await page.wait_for_load_state("networkidle")
                frame_loc = page  # Para reutilizar variable en extracción posterior
            except Exception as e:
                error_logs.append(f"No se pudo acceder al detalle SIPER directo: {e}")
                await context.close()
                await browser.close()
                # success = False
                return results, error_logs, False

            # Timestamp para nombres de archivo
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
                        # quitar tags y normalizar espacios
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

            # ------------------------------------------------------------------
            # 1) Captura de detalle SIPER
            # ------------------------------------------------------------------
            try:
                # Localizar enlace "Historia de las Categorías" con mayor robustez
                texto_historia_variantes = ["Historia de las Categorías", "(Historia de las Categorías)", "(Historia de las Categorías", "Historia de las Categorías)"]
                enlace_historia_loc = None
                for t in texto_historia_variantes:
                    loc = frame_loc.get_by_text(t)
                    try:
                        await loc.wait_for(state="visible", timeout=1200)
                        enlace_historia_loc = loc
                        break
                    except Exception:
                        continue
                if not enlace_historia_loc:
                    # Fallback: cualquier link que contenga el texto
                    try:
                        enlace_historia_loc = frame_loc.get_by_role("link", name=re.compile("Historia de las Categorías", re.I))
                        await enlace_historia_loc.wait_for(state="visible", timeout=1500)
                    except Exception:
                        raise Exception("No se encontró el enlace 'Historia de las Categorías'")
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception as e:
                error_logs.append(f"No se pudo acceder a 'Historia de las Categorías': {e}")
            
            nombre_detalle_minio = f"siper_detalle_{cuit_representante}_{time_now}.png"
            await _capturar_section_container(
                page=page,
                nombre_archivo_minio=nombre_detalle_minio,
                devolver_b64=detalle_b64,
                subir_minio_flag=detalle_minio,
                clave_b64="detalle_b64",
                clave_minio="detalle_minio_url",
            )

            # ------------------------------------------------------------------
            # 2) Click en "Historia de las Categorías" y captura
            # ------------------------------------------------------------------
            try:
                enlace_historia = frame_loc.get_by_text("(Historia de las Categorías")
                await enlace_historia.click()
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception as e:
                error_logs.append(f"No se pudo acceder a 'Historia de las Categorías': {e}")

            nombre_categorias_minio = f"siper_categorias_{cuit_representante}_{time_now}.png"
            await _capturar_section_container(
                page=page,
                nombre_archivo_minio=nombre_categorias_minio,
                devolver_b64=categorias_b64,
                subir_minio_flag=categorias_minio,
                clave_b64="categorias_b64",
                clave_minio="categorias_minio_url",
            )

            # Cierre ordenado
            await context.close()
            await browser.close()

    except Exception as e:
        error_logs.append(f"Error global en bot_siper: {e}")
        success = False

    return results, error_logs, success

# Main function for standalone testing
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
    
    print(f"Ejecutando bot SIPER para CUIT: {cuit}")
    results, errors, success = await bot_siper(cuit, clave, cuit_representado)
    print(f"Success: {success}")
    print(f"Results: {results}")
    if errors:
        print(f"Errors: {errors}")

if __name__ == "__main__":
    asyncio.run(main())
