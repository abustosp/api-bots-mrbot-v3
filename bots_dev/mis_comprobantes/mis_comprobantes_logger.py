import pandas as pd
import numpy as np
import os
import zipfile
import base64
import json
import re
import asyncio
from typing import Optional, List, Tuple
from pathlib import Path
from shutil import rmtree
from datetime import datetime
import logging

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(exist_ok=True)
TEMP_DIR = Path("./temp")
TEMP_DIR.mkdir(exist_ok=True)

mce_xlsx = [
    'Fecha', 'Tipo', 'Punto de Venta', 'Número Desde', 'Número Hasta', 'Cód. Autorización', 
    'Tipo Doc. Receptor', 'Nro. Doc. Receptor', 'Denominación Receptor', 'Tipo Cambio', 'Moneda', 
    'Neto Grav. IVA 0%', 'IVA 2,5%', 'Neto Grav. IVA 2,5%', 'IVA 5%', 'Neto Grav. IVA 5%', 
    'IVA 10,5%', 'Neto Grav. IVA 10,5%', 'IVA 21%', 'Neto Grav. IVA 21%', 'IVA 27%', 
    'Neto Grav. IVA 27%', 'Neto Gravado Total', 'Neto No Gravado', 'Op. Exentas', 'Otros Tributos', 
    'Total IVA', 'Imp. Total'
]

mce_zip = [
    'Fecha de Emisión', 'Tipo de Comprobante', 'Punto de Venta', 'Número Desde', 'Número Hasta', 
    'Cód. Autorización', 'Tipo Doc. Receptor', 'Nro. Doc. Receptor', 'Denominación Receptor', 
    'Tipo Cambio', 'Moneda', 'Imp. Neto Gravado IVA 0%', 'IVA 2,5%', 'Imp. Neto Gravado IVA 2,5%', 
    'IVA 5%', 'Imp. Neto Gravado IVA 5%', 'IVA 10,5%', 'Imp. Neto Gravado IVA 10,5%', 
    'IVA 21%', 'Imp. Neto Gravado IVA 21%', 'IVA 27%', 'Imp. Neto Gravado IVA 27%', 
    'Imp. Neto Gravado Total', 'Imp. Neto No Gravado', 'Imp. Op. Exentas', 'Otros Tributos', 
    'Total IVA', 'Imp. Total'
]

mcr_xlsx = [
    'Fecha', 'Tipo', 'Punto de Venta', 'Número Desde', 'Número Hasta', 'Cód. Autorización', 
    'Tipo Doc. Emisor', 'Nro. Doc. Emisor', 'Denominación Emisor', 'Tipo Doc. Receptor', 
    'Nro. Doc. Receptor', 'Tipo Cambio', 'Moneda', 'Neto Grav. IVA 0%', 'IVA 2,5%', 
    'Neto Grav. IVA 2,5%', 'IVA 5%', 'Neto Grav. IVA 5%', 'IVA 10,5%', 'Neto Grav. IVA 10,5%', 
    'IVA 21%', 'Neto Grav. IVA 21%', 'IVA 27%', 'Neto Grav. IVA 27%', 'Neto Gravado Total', 
    'Neto No Gravado', 'Op. Exentas', 'Otros Tributos', 'Total IVA', 'Imp. Total'
]

mcr_zip = [
    'Fecha de Emisión', 'Tipo de Comprobante', 'Punto de Venta', 'Número Desde', 'Número Hasta', 
    'Cód. Autorización', 'Tipo Doc. Emisor', 'Nro. Doc. Emisor', 'Denominación Emisor', 
    'Tipo Doc. Receptor', 'Nro. Doc. Receptor', 'Tipo Cambio', 'Moneda', 'Imp. Neto Gravado IVA 0%', 
    'IVA 2,5%', 'Imp. Neto Gravado IVA 2,5%', 'IVA 5%', 'Imp. Neto Gravado IVA 5%', 
    'IVA 10,5%', 'Imp. Neto Gravado IVA 10,5%', 'IVA 21%', 'Imp. Neto Gravado IVA 21%', 
    'IVA 27%', 'Imp. Neto Gravado IVA 27%', 'Imp. Neto Gravado Total', 'Imp. Neto No Gravado', 
    'Imp. Op. Exentas', 'Otros Tributos', 'Total IVA', 'Imp. Total'
]

fill_0_mce = [
    'Imp. Neto Gravado IVA 0%', 'IVA 2,5%', 'Imp. Neto Gravado IVA 2,5%', 'IVA 5%', 
    'Imp. Neto Gravado IVA 5%', 'IVA 10,5%', 'Imp. Neto Gravado IVA 10,5%', 'IVA 21%', 
    'Imp. Neto Gravado IVA 21%', 'IVA 27%', 'Imp. Neto Gravado IVA 27%', 'Imp. Neto Gravado Total', 
    'Imp. Neto No Gravado', 'Imp. Op. Exentas', 'Otros Tributos', 'Total IVA', 'Imp. Total'
]

fill_0_mcr = [
    'Imp. Neto Gravado IVA 0%', 'IVA 2,5%', 'Imp. Neto Gravado IVA 2,5%', 'IVA 5%', 
    'Imp. Neto Gravado IVA 5%', 'IVA 10,5%', 'Imp. Neto Gravado IVA 10,5%', 'IVA 21%', 
    'Imp. Neto Gravado IVA 21%', 'IVA 27%', 'Imp. Neto Gravado IVA 27%', 'Imp. Neto Gravado Total', 
    'Imp. Neto No Gravado', 'Imp. Op. Exentas', 'Otros Tributos', 'Total IVA', 'Imp. Total'
]

def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "mis_comprobantes") -> None:
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

async def _login_y_abrir_servicio_mis_comprobantes(page, cuit_inicio_sesion: str, contrasena: str, error_logs: list[str]):
    login_result = await login_arca(
        page,
        str(cuit_inicio_sesion),
        contrasena,
        error_logs=error_logs,
    )
    if not login_result.success:
        return None

    return await open_arca_service(
        page,
        service_name="MIS COMPROBANTES",
        service_matchers=(
            re.compile(r"MIS\s+COMPROBANTES", re.I),
            "MIS COMPROBANTES Consulta de Comprobantes Electrónicos Emitidos y Recibidos",
        ),
        error_logs=error_logs,
        ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
        service_unavailable_message="No se encuentra habilitado el servicio de Mis Comprobantes",
    )


async def filtrar_csv_por_rango(
    archivo_de_mc: str,
    desde: str,
    hasta: str,
    archivo_salida: str,
    cuit: str
    ) -> pd.DataFrame:
    
    df = pd.read_csv(archivo_de_mc, sep=";", decimal=",")
    
    filename = Path(archivo_de_mc).name
    if "MCR" in filename:
        columnas = mcr_xlsx
        for col in fill_0_mcr:
             if col in df.columns: df[col] = df[col].fillna(0)
    else:
        columnas = mce_xlsx
        for col in fill_0_mce:
            if col in df.columns: df[col] = df[col].fillna(0)
    
    if len(df.columns) == len(columnas):
        df.columns = columnas
        
    if 'Fecha' in df.columns:
        df['Fecha'] = pd.to_datetime(df['Fecha'], format='%Y-%m-%d')
        desde_dt = pd.to_datetime(desde, format='%d/%m/%Y')
        hasta_dt = pd.to_datetime(hasta, format='%d/%m/%Y')

        df = df[(df['Fecha'] >= desde_dt) & (df['Fecha'] <= hasta_dt)]
        df['Fecha'] = df['Fecha'].dt.strftime('%Y-%m-%d')
    
    df.to_csv(archivo_salida, index=False, sep=";")
    return df


async def bot_mis_comprobantes_logger(
    desde: str,
    hasta: str,
    cuit_inicio_sesion: str,
    representado_nombre: str,
    representado_cuit: str,
    contrasena: str,
    eliminar_descargas: bool = False,
    descarga_emitidos: bool = False,
    descarga_recibidos: bool = False,
    puntos_venta_emitidos: Optional[list] = None,
    puntos_venta_recibidos: Optional[list] = None,
    carga_b64: bool = False,
    carga_json: bool = False,
    timeout_mc: int = 30,
    proxy_request: bool = False
    ) -> Tuple[pd.DataFrame, pd.DataFrame, str, str, List[str], bool]:
    
    debug = DebugCapture(prefix="mis_comprobantes")
    debug.log("Iniciando bot Mis Comprobantes Logger")

    try:
        desde_dt = pd.to_datetime(desde, format='%d/%m/%Y')
        hasta_dt = pd.to_datetime(hasta, format='%d/%m/%Y')
    except Exception as e:
        debug.log(f"Error formato fecha: {e}")
        return pd.DataFrame(), pd.DataFrame(), "", "", [f"Error formato fecha: {e}"], False

    desde_str = desde_dt.strftime('%d/%m/%Y')
    hasta_str = hasta_dt.strftime('%d/%m/%Y')
    
    nro = str(representado_cuit)[-1]
    mc_e = f'{nro} - MCE - {desde_str.replace("/", "")} - {hasta_str.replace("/", "")} - {representado_cuit} - {representado_nombre}'
    mc_r = f'{nro} - MCR - {desde_str.replace("/", "")} - {hasta_str.replace("/", "")} - {representado_cuit} - {representado_nombre}'

    df_e = pd.DataFrame()
    df_r = pd.DataFrame()
    df_e_base64 = str("")
    df_r_base64 = str("")
    
    error_logs = []
    success = True
    
    proxy = get_proxy_config() if proxy_request else None
    
    # Usar directorio de descargas del logger
    path_descarga = debug.descargas_dir
    debug.log(f"Directorio descargas: {path_descarga}")

    async with async_playwright() as playwright:
        har_path = debug.har_dir / "mis_comprobantes.har"
        
        browser = await playwright.chromium.launch(headless=True, proxy=proxy)
        context = await browser.new_context(record_har_path=har_path, record_har_content="embed")
        page = await context.new_page()
        await debug.capture(page, "inicio")

        page1 = await _login_y_abrir_servicio_mis_comprobantes(
            page,
            str(cuit_inicio_sesion),
            contrasena,
            error_logs,
        )
        if page1 is None:
            debug.log(f"Login fallo: {error_logs}")
            await debug.capture(page, "login_error")
            await context.close()
            await browser.close()
            return df_r, df_e, df_r_base64, df_e_base64, error_logs, False
        
        await debug.capture(page, "login_exitoso_main_page")
        if page1 is not page:
            await debug.capture(page1, "servicio_mis_comprobantes_abierto")

        if page1 is not page and not page.is_closed():
            await page.close()

        try:
            await page1.wait_for_load_state("networkidle")
            
            # Seleccionar representado
            try:
                representado_cuit_2 = str(representado_cuit)
                if len(representado_cuit_2) == 11:
                    representado_cuit_2 = f"{representado_cuit_2[0:2]}-{representado_cuit_2[2:10]}-{representado_cuit_2[10:11]}"
                
                await page1.locator(f'small.pull-right:has-text("{representado_cuit_2}")').click(timeout=3000)
                await debug.capture(page1, "representado_seleccionado")
            except:
                try:
                    representado_cuit_text = await page1.locator("span.nombre-propio.nombre-activo.pull-righ").text_content()
                    if str(representado_cuit) not in representado_cuit_text.replace("-",""):
                         error_logs.append(f"No se pudo seleccionar el representado correcto {representado_cuit}")
                         debug.log("Error seleccionando representado")
                except:
                    pass
            
            # --- RECIBIDOS ---
            if descarga_recibidos:
                try:
                    await page1.wait_for_load_state("networkidle")
                    await page1.get_by_text("Comprobantes Recibidos", exact=True).click()
                    await page1.wait_for_load_state("networkidle")
                    await debug.capture(page1, "menu_recibidos")
                    
                    await page1.get_by_label("Fecha del Comprobante *").click()
                    campo_desde = page1.locator("input[name=\"daterangepicker_start\"]")
                    await campo_desde.click(); await campo_desde.fill(""); await campo_desde.fill(desde_str)
                    campo_hasta = page1.locator("input[name=\"daterangepicker_end\"]")
                    await campo_hasta.click(); await campo_hasta.fill(""); await campo_hasta.fill(hasta_str)
                    
                    if puntos_venta_recibidos and len(puntos_venta_recibidos) > 0:
                        await page1.get_by_role("textbox", name="Punto de Venta").fill(",".join(puntos_venta_recibidos))
                    
                    await page1.get_by_role("button", name="Aplicar").click()
                    await debug.capture(page1, "fechas_aplicadas_recibidos")
                    await page1.get_by_role("button", name="Buscar").click()
                    await page1.wait_for_load_state("networkidle")
                    await debug.capture(page1, "busqueda_recibidos")

                    async with page1.expect_download() as download_info:
                        await page1.get_by_role("button", name="CSV").click(timeout=timeout_mc*1000)
                    download = await download_info.value
                    
                    zip_name = f"{mc_r}.zip"
                    zip_path = path_descarga / zip_name
                    await download.save_as(path=zip_path)
                    debug.log(f"Zip recibidos descargado: {zip_path}")
                    
                    if True: # Always process
                        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                            archivo_extraido_name = zip_ref.namelist()[0]
                            zip_ref.extract(archivo_extraido_name, path_descarga)
                            archivo_csv = path_descarga / f"{mc_r}.csv"
                            
                            (path_descarga / archivo_extraido_name).rename(archivo_csv)
                            debug.log(f"CSV extraido: {archivo_csv}")
                            
                            df_r = await filtrar_csv_por_rango(str(archivo_csv), desde_str, hasta_str, str(archivo_csv), representado_cuit)
                            if eliminar_descargas:
                                archivo_csv.unlink(missing_ok=True)

                    if carga_b64:
                        df_r_base64 = base64.b64encode(zip_path.read_bytes()).decode('utf-8')
                            
                    if eliminar_descargas:
                         zip_path.unlink(missing_ok=True)

                    await page1.get_by_text("Menú Principal", exact=True).click()

                except Exception as e:
                    debug.log(f"Error recibidos: {e}")
                    error_logs.append(f"Error procesando recibidos: {e}")
                    await debug.capture(page1, "error_recibidos")
                    success = False
            
            # --- EMITIDOS ---
            if descarga_emitidos:
                try:
                    await page1.wait_for_load_state("networkidle")
                    await page1.get_by_text("Comprobantes Emitidos", exact=True).click()
                    await page1.wait_for_load_state("networkidle")
                    await debug.capture(page1, "menu_emitidos")
                    
                    await page1.get_by_label("Fecha del Comprobante *").click()
                    campo_desde = page1.locator("input[name=\"daterangepicker_start\"]")
                    await campo_desde.click(); await campo_desde.fill(""); await campo_desde.fill(desde_str)
                    campo_hasta = page1.locator("input[name=\"daterangepicker_end\"]")
                    await campo_hasta.click(); await campo_hasta.fill(""); await campo_hasta.fill(hasta_str)
                    
                    if puntos_venta_emitidos and len(puntos_venta_emitidos) > 0:
                        await page1.get_by_role("textbox", name="Punto de Venta").fill(",".join(puntos_venta_emitidos))
                            
                    await page1.get_by_role("button", name="Aplicar").click()
                    await debug.capture(page1, "fechas_aplicadas_emitidos")
                    await page1.get_by_role("button", name="Buscar").click()
                    await page1.wait_for_load_state("networkidle")
                    await debug.capture(page1, "busqueda_emitidos")

                    async with page1.expect_download() as download_info:
                        await page1.get_by_role("button", name="CSV").click(timeout=timeout_mc*1000)
                    download = await download_info.value
                    
                    zip_name = f"{mc_e}.zip"
                    zip_path = path_descarga / zip_name
                    await download.save_as(path=zip_path)
                    debug.log(f"Zip emitidos descargado: {zip_path}")
                    
                    if True:
                        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                            archivo_extraido_name = zip_ref.namelist()[0]
                            zip_ref.extract(archivo_extraido_name, path_descarga)
                            archivo_csv = path_descarga / f"{mc_e}.csv"
                            
                            (path_descarga / archivo_extraido_name).rename(archivo_csv)
                            debug.log(f"CSV extraido: {archivo_csv}")
                            
                            df_e = await filtrar_csv_por_rango(str(archivo_csv), desde_str, hasta_str, str(archivo_csv), representado_cuit)
                            if eliminar_descargas:
                                archivo_csv.unlink(missing_ok=True)

                    if carga_b64:
                        df_e_base64 = base64.b64encode(zip_path.read_bytes()).decode('utf-8')
                            
                    if eliminar_descargas:
                         zip_path.unlink(missing_ok=True)

                    await page1.get_by_text("Menú Principal", exact=True).click()

                except Exception as e:
                    debug.log(f"Error emitidos: {e}")
                    error_logs.append(f"Error procesando emitidos: {e}")
                    await debug.capture(page1, "error_emitidos")
                    success = False

        except Exception as e:
            debug.log(f"Error general: {e}")
            error_logs.append(f"Error general en Mis Comprobantes: {e}")
            success = False
        finally:
            await context.close()
            await browser.close()
            
    return df_r, df_e, df_r_base64, df_e_base64, error_logs, success


async def main():
    try:
        from dotenv import load_dotenv; load_dotenv()
    except: pass
    
    def _env_flag(name: str) -> bool:
        return os.getenv(name, "False").lower() in ("true", "1", "yes")

    res = await bot_mis_comprobantes_logger(
        desde=os.getenv("MIS_COMPROBANTES_DESDE", "01/01/2024"),
        hasta=os.getenv("MIS_COMPROBANTES_HASTA", "31/01/2024"),
        cuit_inicio_sesion=os.getenv("MIS_COMPROBANTES_CUIT_INICIO_SESION", ""),
        representado_nombre=os.getenv("MIS_COMPROBANTES_REPRESENTADO_NOMBRE", "TEST"),
        representado_cuit=os.getenv("MIS_COMPROBANTES_REPRESENTADO_CUIT", ""),
        contrasena=os.getenv("MIS_COMPROBANTES_CONTRASENA", ""),
        eliminar_descargas=False,
        descarga_emitidos=True,
        descarga_recibidos=True,
        puntos_venta_emitidos=None, 
        puntos_venta_recibidos=None,
        carga_b64=True,
        carga_json=True,
        proxy_request=_env_flag("MIS_COMPROBANTES_PROXY_REQUEST")
    )
    print("Logs:", res[4])
    print("Success:", res[5])
    if res[5]:
        print("Emitidos shape:", res[1].shape if not res[1].empty else "Empty")

if __name__ == "__main__":
    asyncio.run(main())
