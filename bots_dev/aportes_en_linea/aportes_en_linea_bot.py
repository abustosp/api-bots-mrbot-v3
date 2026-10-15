import os
import re
import tempfile
import base64
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from playwright.async_api import async_playwright, Page
from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(exist_ok=True)

async def bot_aportes_en_linea(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str = None,
    # Opciones de salida
    archivo_historico_b64: bool = False,
    proxy_request: bool = False,
) -> Tuple[Dict[str, str], List[str], bool]:
    """
    Ejecuta el bot de Aportes en Línea de AFIP.
    """
    results: Dict[str, str] = {}
    error_logs: List[str] = []
    success = True
    
    if not cuit_representado:
        cuit_representado = cuit_representante
    
    proxy = get_proxy_config() if proxy_request else None
    
    # Directorio de salida
    output_dir = DOWNLOAD_DIR / "aportes_en_linea" / f"{cuit_representado}"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()
            
            # Login
            login_result = await login_arca(
                page,
                str(cuit_representante),
                str(clave_representante),
                error_logs=error_logs,
            )
            if not login_result.success:
                await _safe_close(context, browser)
                return results, error_logs, False
            
            # Abrir servicio
            page1 = await open_arca_service(
                page,
                service_name="APORTES EN LÍNEA",
                service_matchers=(
                    re.compile(r"APORTES EN LÍNEA", re.I),
                ),
                error_logs=error_logs,
                service_unavailable_message="No se encuentra habilitado el servicio de Aportes en Línea",
            )
            
            if page1 is None:
                await _safe_close(context, browser)
                return results, error_logs, False

            # Ingresar al servicio
            try:
                # A veces hay un botón de Ingresar intermedio
                await page1.get_by_role("button", name="INGRESAR").click()
            except Exception:
                pass
            
            # Seleccionar representado si es necesario (Aportes en linea suele tener seleccion interna o usa el de login)
            # En el código original no habia logica explicita de cambiarlos, 
            # pero si el usuario pide otro representado, este servicio suele tener un "Seleccionar Contribuyente"
            # Si el servicio no lo permite facil, se asume que es el del login.
            # El codigo original no lo hacia, asi que mantengo paridad.

            # Descargar archivo histórico
            try:
                async with page1.expect_download() as download_info:
                    # El popup que se abre al hacer click en "Archivo Histórico"
                    async with page1.expect_popup() as page3_info:
                        await page1.get_by_role("button", name="Archivo Histórico").click()
                    page3 = await page3_info.value
                    await page3.close()
                
                download = await download_info.value
                
                fecha_hoy = datetime.now().strftime("%Y%m%d")
                nombre_archivo = f"AEL - {cuit_representado} - {fecha_hoy}.xls"
                final_path = output_dir / nombre_archivo
                
                await download.save_as(final_path)
                results["archivo_path"] = str(final_path)

                if archivo_historico_b64:
                    results["archivo_historico_b64"] = base64.b64encode(final_path.read_bytes()).decode("utf-8")
                    
            except Exception as e:
                error_logs.append(f"Error al descargar archivo histórico: {e}")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return results, error_logs, False
            
            await _safe_close(context, browser, page1=page1, base_page=page)
    
    except Exception as e:
        error_logs.append(f"Error general: {e}")
        success = False
    
    return results, error_logs, success

async def _safe_close(context, browser, *, page1: Optional[Page] = None, base_page: Optional[Page] = None):
    try:
        if page1 is not None and page1 is not base_page:
            await page1.close()
    except Exception: pass
    try:
        if base_page is not None:
            await base_page.close()
    except Exception: pass
    try:
        await context.close()
    except Exception: pass
    try:
        await browser.close()
    except Exception: pass

async def main():
    # Test local
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    
    cuit = os.getenv("APORTES_CUIT_LOGIN", "")
    clave = os.getenv("APORTES_CLAVE", "")
    
    if cuit and clave:
        res = await bot_aportes_en_linea(cuit, clave, cuit_representado=cuit)
        print(res)

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
