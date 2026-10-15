import os
import re
import tempfile
import base64
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple, Optional

from playwright.async_api import async_playwright, Page
from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(exist_ok=True)
TEMP_DIR = Path("./temp")
TEMP_DIR.mkdir(exist_ok=True)

def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "aportes_en_linea") -> None:
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

async def bot_aportes_en_linea(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str = None,
    archivo_historico_b64: bool = False,
    proxy_request: bool = False,
) -> Tuple[Dict[str, str], List[str], bool]:
    """
    Ejecuta el bot de Aportes en Línea de AFIP (Logger).
    """
    results: Dict[str, str] = {}
    error_logs: List[str] = []
    success = True
    
    debug = DebugCapture(prefix="aportes_en_linea")
    debug.log("Iniciando bot Aportes en Linea Logger")
    
    if not cuit_representado:
        cuit_representado = cuit_representante
    
    proxy = get_proxy_config() if proxy_request else None
    
    try:
        async with async_playwright() as pw:
            har_path = debug.har_dir / "aportes.har"
            browser = await pw.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(accept_downloads=True, record_har_path=har_path, record_har_content="embed")
            page = await context.new_page()
            await debug.capture(page, "inicio")
            
            # Login
            login_result = await login_arca(
                page,
                str(cuit_representante),
                str(clave_representante),
                error_logs=error_logs,
            )
            await debug.capture(page, "login_completo")
            
            if not login_result.success:
                debug.log(f"Login fallo: {error_logs}")
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
                debug.log("Servicio no disponible")
                await _safe_close(context, browser)
                return results, error_logs, False
            
            await debug.capture(page1, "servicio_abierto")

            # Ingresar al servicio
            try:
                await page1.get_by_role("button", name="INGRESAR").click()
                await debug.capture(page1, "ingresar_click")
            except Exception:
                pass
            
            # Descargar archivo histórico
            try:
                async with page1.expect_download() as download_info:
                    async with page1.expect_popup() as page3_info:
                        await page1.get_by_role("button", name="Archivo Histórico").click()
                    page3 = await page3_info.value
                    await debug.capture(page3, "popup_archivo_historico")
                    await page3.close()
                
                download = await download_info.value
                
                fecha_hoy = datetime.now().strftime("%Y%m%d")
                nombre_archivo = f"AEL - {cuit_representado} - {fecha_hoy}.xls"
                final_path = debug.descargas_dir / nombre_archivo
                
                await download.save_as(final_path)
                debug.log(f"Archivo descargado: {final_path}")
                results["archivo_path"] = str(final_path)

                if archivo_historico_b64:
                    results["archivo_historico_b64"] = base64.b64encode(final_path.read_bytes()).decode("utf-8")
                    
            except Exception as e:
                debug.log(f"Error descarga: {e}")
                error_logs.append(f"Error al descargar archivo histórico: {e}")
                await debug.capture(page1, "error_descarga")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return results, error_logs, False
            
            await _safe_close(context, browser, page1=page1, base_page=page)
    
    except Exception as e:
        debug.log(f"Exception general: {e}")
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
