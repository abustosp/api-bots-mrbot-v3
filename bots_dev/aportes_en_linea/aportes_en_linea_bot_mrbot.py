import os
import re
import base64
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from playwright.async_api import async_playwright, Page
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser


# ----------------------------------------------------------------------

# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Bot principal
# ----------------------------------------------------------------------
async def bot_aportes_en_linea(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str = None,
    archivo_historico_b64: bool = False,

    proxy_request: bool = False,
) -> Tuple[Dict[str, str], List[str], bool]:
    """
    Ejecuta el bot de Aportes en Línea de AFIP.
    
    Args:
        cuit_representante (str): CUIT del representante para iniciar sesión.
        clave_representante (str): Clave fiscal del representante.
        cuit_representado (str): CUIT del representado. Si no se especifica, se usa el del representante.
        archivo_historico_b64 (bool): Si True, devuelve el archivo histórico en base64.

        proxy_request (bool): Si True, usa configuración de proxy.
    
    Returns:
        tuple[dict[str, str], list[str], bool]:

            - error_logs: lista de strings con errores/avisos
            - success: bool indicando si el flujo fue exitoso
    """
    results: Dict[str, str] = {}
    error_logs: List[str] = []
    success = True
    
    # Si no se especifica representado, usar el representante
    if not cuit_representado:
        cuit_representado = cuit_representante
    
    # Validar que al menos se solicitó una salida

    if not any(request_salidas):
        error_logs.append("No se seleccionó ningún tipo de salida (base64).")
        return results, error_logs, False
    
    proxy = get_proxy_config() if proxy_request else None
    
    try:
        async with limit_browser(), async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            
            # ------------------------------------------------------------------
            # Login AFIP/ARCA
            # ------------------------------------------------------------------
            try:
                await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
                await page.get_by_role("spinbutton").fill(str(cuit_representante))
                await page.get_by_role("button", name="Siguiente").click()
                await page.wait_for_load_state("networkidle")
            except Exception as e:
                error_logs.append(f"No se pudo acceder a la página de login: {e}")
                await _safe_close(context, browser)
                return results, error_logs, False
            
            # Detectar captcha
            try:
                await page.get_by_role("img", name="Captcha").click(timeout=1000)
                error_logs.append("Captcha detectado")
            except Exception:
                pass
            
            # Completar clave
            try:
                await page.get_by_role("textbox", name="TU CLAVE").fill(str(clave_representante))
                await page.get_by_role("button", name=re.compile(r"Ingresar", re.I)).click()
                await page.wait_for_load_state("networkidle")
            except Exception as e:
                error_logs.append(f"No se pudo completar el login: {e}")
                await _safe_close(context, browser)
                return results, error_logs, False
            
            # Verificar cambio de clave fiscal
            try:
                if await page.get_by_role("heading", name=re.compile(r"Cambiar Clave Fiscal", re.I)).is_visible():
                    error_logs.append("Se requiere cambiar la clave fiscal")
                    await _safe_close(context, browser)
                    return results, error_logs, False
            except Exception:
                pass
            
            # Ver todos los servicios
            try:
                await page.get_by_role("link", name=re.compile(r"Ver todos", re.I)).click(timeout=5000)
            except Exception as e:
                error_logs.append(f"No se pudo desplegar 'Ver todos': {e}")
            
            # ------------------------------------------------------------------
            # Abrir servicio Aportes en Línea (popup)
            # ------------------------------------------------------------------
            page1: Optional[Page] = None
            try:
                async with page.expect_popup() as page1_info:
                    await page.get_by_role("button", name=re.compile(r"APORTES EN LÍNEA", re.I)).click()
                page1 = await page1_info.value
            except Exception as e:
                error_logs.append("No se encuentra habilitado el servicio de Aportes en Línea")
                await _safe_close(context, browser)
                return results, error_logs, False
            
            # ------------------------------------------------------------------
            # Ingresar al servicio
            # ------------------------------------------------------------------
            try:
                await page1.get_by_role("button", name="INGRESAR").click()
            except Exception as e:
                error_logs.append(f"No se pudo ingresar al servicio: {e}")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return results, error_logs, False
            
            # ------------------------------------------------------------------
            # Descargar archivo histórico
            # ------------------------------------------------------------------
            try:
                async with page1.expect_download() as download_info:
                    # El popup que se abre al hacer click en "Archivo Histórico"
                    # lo capturamos pero lo cerramos después
                    async with page1.expect_popup() as page3_info:
                        await page1.get_by_role("button", name="Archivo Histórico").click()
                    page3 = await page3_info.value
                    await page3.close()
                
                download = await download_info.value
                
                # Obtener bytes del archivo descargado
                tmp_path = None
                try:
                    path = await download.path()
                    if path is None:
                        tmp = open(str(DOWNLOAD_DIR / "tmp.bin"), "wb")
                        tmp_path = tmp.name
                        tmp.close()
                        await download.save_as(tmp_path)
                        path = tmp_path
                    
                    with open(path, "rb") as f:
                        file_bytes = f.read()
                    
                    # Base64
                    if archivo_historico_b64:
                        results["archivo_historico_b64"] = base64.b64encode(file_bytes).decode("utf-8")

                finally:
                    # Limpiar archivo temporal
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception:
                            pass

            except Exception as e:
                error_logs.append(f"Error al descargar archivo histórico: {e}")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return results, error_logs, False
            
            # Cierre ordenado
            await _safe_close(context, browser, page1=page1, base_page=page)
    
    except Exception as e:
        error_logs.append(f"No se pudo iniciar sesión en ARCA. Verifique los datos ingresados: {e}")
        success = False
    
    return results, error_logs, success


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
async def _safe_close(context, browser, *, page1: Optional[Page] = None, base_page: Optional[Page] = None):
    """Cierra de forma segura todos los recursos de Playwright."""
    try:
        if page1 is not None:
            await page1.close()
    except Exception:
        pass
    try:
        if base_page is not None:
            await base_page.close()
    except Exception:
        pass
    try:
        await context.close()
    except Exception:
        pass
    try:
        await browser.close()
    except Exception:
        pass
