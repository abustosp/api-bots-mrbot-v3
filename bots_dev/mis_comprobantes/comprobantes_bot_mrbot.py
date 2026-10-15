from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError
import pandas as pd
import numpy as np
import os
import zipfile
from shutil import rmtree
import base64
import json
import re
from shared.arca_login import login_arca, open_arca_service
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser
import asyncio
import tempfile
from typing import Optional


mce_xlsx=[
    'Fecha',
    'Tipo',
    'Punto de Venta',
    'Número Desde',
    'Número Hasta',
    'Cód. Autorización',
    'Tipo Doc. Receptor',
    'Nro. Doc. Receptor',
    'Denominación Receptor',
    'Tipo Cambio',
    'Moneda',
    'Neto Grav. IVA 0%',
    'IVA 2,5%',
    'Neto Grav. IVA 2,5%',
    'IVA 5%',
    'Neto Grav. IVA 5%',
    'IVA 10,5%',
    'Neto Grav. IVA 10,5%',
    'IVA 21%',
    'Neto Grav. IVA 21%',
    'IVA 27%',
    'Neto Grav. IVA 27%',
    'Neto Gravado Total',
    'Neto No Gravado',
    'Op. Exentas',
    'Otros Tributos',
    'Total IVA',
    'Imp. Total'
]

mce_zip = [
    'Fecha de Emisión',
    'Tipo de Comprobante',
    'Punto de Venta',
    'Número Desde',
    'Número Hasta',
    'Cód. Autorización',
    'Tipo Doc. Receptor',
    'Nro. Doc. Receptor',
    'Denominación Receptor',
    'Tipo Cambio',
    'Moneda',
    'Imp. Neto Gravado IVA 0%',
    'IVA 2,5%',
    'Imp. Neto Gravado IVA 2,5%',
    'IVA 5%',
    'Imp. Neto Gravado IVA 5%',
    'IVA 10,5%',
    'Imp. Neto Gravado IVA 10,5%',
    'IVA 21%',
    'Imp. Neto Gravado IVA 21%',
    'IVA 27%',
    'Imp. Neto Gravado IVA 27%',
    'Imp. Neto Gravado Total',
    'Imp. Neto No Gravado',
    'Imp. Op. Exentas',
    'Otros Tributos',
    'Total IVA',
    'Imp. Total'
]

mcr_xlsx=[
    'Fecha',
    'Tipo',
    'Punto de Venta',
    'Número Desde',
    'Número Hasta',
    'Cód. Autorización',
    'Tipo Doc. Emisor',
    'Nro. Doc. Emisor',
    'Denominación Emisor',
    'Tipo Doc. Receptor',
    'Nro. Doc. Receptor',
    'Tipo Cambio',
    'Moneda',
    'Neto Grav. IVA 0%',
    'IVA 2,5%',
    'Neto Grav. IVA 2,5%',
    'IVA 5%',
    'Neto Grav. IVA 5%',
    'IVA 10,5%',
    'Neto Grav. IVA 10,5%',
    'IVA 21%',
    'Neto Grav. IVA 21%',
    'IVA 27%',
    'Neto Grav. IVA 27%',
    'Neto Gravado Total',
    'Neto No Gravado',
    'Op. Exentas',
    'Otros Tributos',
    'Total IVA',
    'Imp. Total'
]

mcr_zip = [
    'Fecha de Emisión',
    'Tipo de Comprobante',
    'Punto de Venta',
    'Número Desde',
    'Número Hasta',
    'Cód. Autorización',
    'Tipo Doc. Emisor',
    'Nro. Doc. Emisor',
    'Denominación Emisor',
    'Tipo Doc. Receptor',
    'Nro. Doc. Receptor',
    'Tipo Cambio',
    'Moneda',
    'Imp. Neto Gravado IVA 0%',
    'IVA 2,5%',
    'Imp. Neto Gravado IVA 2,5%',
    'IVA 5%',
    'Imp. Neto Gravado IVA 5%',
    'IVA 10,5%',
    'Imp. Neto Gravado IVA 10,5%',
    'IVA 21%',
    'Imp. Neto Gravado IVA 21%',
    'IVA 27%',
    'Imp. Neto Gravado IVA 27%',
    'Imp. Neto Gravado Total',
    'Imp. Neto No Gravado',
    'Imp. Op. Exentas',
    'Otros Tributos',
    'Total IVA',
    'Imp. Total'
]


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

fill_0_mce = [
    'Imp. Neto Gravado IVA 0%',
    'IVA 2,5%',
    'Imp. Neto Gravado IVA 2,5%',
    'IVA 5%',
    'Imp. Neto Gravado IVA 5%',
    'IVA 10,5%',
    'Imp. Neto Gravado IVA 10,5%',
    'IVA 21%',
    'Imp. Neto Gravado IVA 21%',
    'IVA 27%',
    'Imp. Neto Gravado IVA 27%',
    'Imp. Neto Gravado Total',
    'Imp. Neto No Gravado',
    'Imp. Op. Exentas',
    'Otros Tributos',
    'Total IVA',
    'Imp. Total'
]

fill_0_mcr = [
    'Imp. Neto Gravado IVA 0%',
    'IVA 2,5%',
    'Imp. Neto Gravado IVA 2,5%',
    'IVA 5%',
    'Imp. Neto Gravado IVA 5%',
    'IVA 10,5%',
    'Imp. Neto Gravado IVA 10,5%',
    'IVA 21%',
    'Imp. Neto Gravado IVA 21%',
    'IVA 27%',
    'Imp. Neto Gravado IVA 27%',
    'Imp. Neto Gravado Total',
    'Imp. Neto No Gravado',
    'Imp. Op. Exentas',
    'Otros Tributos',
    'Total IVA',
    'Imp. Total'
    ]

mce_xlsx_zip_map = dict(zip(mce_zip, mce_xlsx))
mcr_xlsx_zip_map = dict(zip(mcr_zip, mcr_xlsx))


async def filtrar_csv_por_rango(
    archivo_de_mc: str,
    desde: str,
    hasta: str,
    archivo_salida: str,
    cuit: str
    ) -> pd.DataFrame:
    """
    función para filtrar el Dataframe en base a fechas para arreglar bug en ARCA donde se traen comprobantes que exceden el período consultado

    Args:
        archivo_de_mc (str): path del archivo de mis comprobantes descargado
        desde (str): fecha de inicio del rango a filtrar
        hasta (str): fecha de fin del rango a filtrar
        archivo_salida (str): path del archivo de salida
        cuit (str): CUIT del cliente

    Returns:
        pd.DataFrame: DataFrame filtrado por el rango de fechas
    """
    
    df = pd.read_csv(archivo_de_mc, sep=";", decimal=",")
    
    tipo_MC = archivo_de_mc.split('/')[-1].split('-')[1].strip()
    
    if tipo_MC == "MCR":
        columnas = mcr_xlsx
        for col in fill_0_mcr:
            df[col] = df[col].fillna(0)
    else:
        columnas = mce_xlsx
        for col in fill_0_mce:
            df[col] = df[col].fillna(0)
    
    df.columns = columnas
    df['Fecha'] = pd.to_datetime(df['Fecha'], format='%Y-%m-%d')
    desde = pd.to_datetime(desde, format='%d/%m/%Y')
    hasta = pd.to_datetime(hasta, format='%d/%m/%Y')

    df = df[(df['Fecha'] >= desde) & (df['Fecha'] <= hasta)]
    df['Fecha'] = df['Fecha'].dt.strftime('%Y-%m-%d')
    df.to_csv(archivo_salida, index=False, sep=";")

    df['MC'] = tipo_MC
    df['CUIT Cliente'] = cuit
    if tipo_MC == "MCE":
        df['MC AUX'] = cuit + "-" + df['Tipo'].astype(str) + "-" + df['Punto de Venta'].astype(str) + "-" + df['Número Desde'].astype(str) + "-" + df['Nro. Doc. Receptor'].astype(str) + "-" + cuit
    else:
        df['MC AUX'] = cuit + "-" + df['Tipo'].astype(str) + "-" + df['Punto de Venta'].astype(str) + "-" + df['Número Desde'].astype(str) + "-" + df['Nro. Doc. Receptor'].astype(str) + "-" + df['Nro. Doc. Emisor'].astype(str)
    df['Factura AUX'] = cuit + "-" + df["Tipo"].astype(str) + "-" + df["Punto de Venta"].astype(str) + "-" + df["Número Desde"].astype(str)
    df.loc[df["MC"] == "MCR", "Factura AUX"] = np.nan

    return df


async def descargar_mis_comprobantes(
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
    ) -> tuple[pd.DataFrame, pd.DataFrame, str, str, list, bool, str, str, str, str]:
    """
    función para ejecutar el bot de descarga de Mis Comprobantes desde ARCA/AFIP

    Args:
        desde (str): Fecha de inicio para la consulta.
        hasta (str): Fecha de fin para la consulta.
        cuit_inicio_sesion (str): CUIT del usuario para iniciar sesión.
        representado_nombre (str): Nombre del representado.
        representado_cuit (str): CUIT del representado.
        contrasena (str): constraseña del usuario para iniciar sesión.
        eliminar_descargas (bool, optional): opción para elimiar las descargas temporales. Defaults to False.
        descarga_emitidos (bool, optional): opción para descargar los emitidos. Defaults to False.
        descarga_recibidos (bool, optional): opción para descargas los recibidos. Defaults to False.

        descarga_b64 (bool, optional): opción para devolver los archivos en base64. Defaults to False.
        descarga_json (bool, optional): opción para devolver los archivos en json. Defaults to False.
        proxy_request (bool, optional): opción para usar proxy. Defaults to False.

    Returns:
        tuple[pd.DataFrame, pd.DataFrame, str, str, list, bool]: tupla con los dataframes de recibidos y emitidos, base64 de recibidos y emitidos, logs de errores y éxito de la operación.
    """
    
    desde = pd.to_datetime(desde, format='%d/%m/%Y')
    hasta = pd.to_datetime(hasta, format='%d/%m/%Y')
    desde_str = desde.strftime('%d/%m/%Y')
    hasta_str = hasta.strftime('%d/%m/%Y')
    
    cuits = [representado_cuit, cuit_inicio_sesion]
    
    # por cada cuit en cuits, limpiarlo (eliminando espacios, guiones, puntos y /)
    for i in range(len(cuits)):
        cuits[i] = str(cuits[i]).replace(" ", "").replace("-", "").replace(".", "").replace("/", "")    

    nro = str(representado_cuit)[-1]
    mc_e = f'{nro} - MCE - {str(desde_str).replace("/", "")} - {str(hasta_str).replace("/", "")} - {representado_cuit} - {representado_nombre}'
    mc_r = f'{nro} - MCR - {str(desde_str).replace("/", "")} - {str(hasta_str).replace("/", "")} - {representado_cuit} - {representado_nombre}'

    df_e = pd.DataFrame()
    df_r = pd.DataFrame()
    df_e_base64 = str("")
    df_r_base64 = str("")
    
    error_logs = []
    success = True
    


    proxy= get_proxy_config() if proxy_request==True else None
    

    request_descargas = [descarga_emitidos, descarga_recibidos]
    
    if not any(request_salidas) and not any(request_descargas):
        success = False
        error_logs.append("No se seleccionó ningún tipo de salida de los resultados (json).")
        error_logs.append("No se seleccionó ningún tipo de comprobante para descargar (emitidos o recibidos).")

    if not any(request_salidas):
        success = False
        error_logs.append("No se seleccionó ningún tipo de salida de los resultados (json).")

    if not any(request_descargas):
        success = False
        error_logs.append("No se seleccionó ningún tipo de comprobante para descargar (emitidos o recibidos).")

    else:
        temp_dir = None
        if eliminar_descargas:
            temp_dir = tempfile.TemporaryDirectory(prefix="miscomprobantes_")
            path_descarga = temp_dir.name
        else:
            path_descarga = os.path.join(os.getcwd(), f"descargas/mis_comprobantes/{representado_nombre}")
            os.makedirs(path_descarga, exist_ok=True)

        def _cleanup_and_return(
            df_r_value,
            df_e_value,
            df_r_b64,
            df_e_b64,
            logs,
            ok,


        ):
            if temp_dir is not None:
                temp_dir.cleanup()
            return (
                df_r_value,
                df_e_value,
                df_r_b64,
                df_e_b64,
                logs,
                ok,


            )

        async with limit_browser(), async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True,
                                                       proxy=proxy)
            context = await browser.new_context()
            page = await context.new_page()

            page1 = await _login_y_abrir_servicio_mis_comprobantes(
                page,
                str(cuit_inicio_sesion),
                contrasena,
                error_logs,
            )
            if page1 is None:
                await context.close()
                await browser.close()
                return _cleanup_and_return(
                    df_r,
                    df_e,
                    df_r_base64,
                    df_e_base64,
                    error_logs,
                    False,


                )
            if page1 is not page and not page.is_closed():
                await page.close()

            try:
                try:

                    try:
                        await page1.wait_for_load_state("networkidle")
                        representado_cuit_2 = str(representado_cuit)
                        representado_cuit_2 = representado_cuit_2[0:2] + "-" + representado_cuit_2[2:10] + "-" + representado_cuit_2[10:11]
                        await page1.locator(f'small.pull-right:has-text("{representado_cuit_2}")').click(timeout=3000)
                    except:
                        representado_cuit_text = await page1.locator("span.nombre-propio.nombre-activo.pull-righ").text_content()
                        representado_cuit_value = representado_cuit_text.split("[")[1].split("]")[0]
                        representado_cuit_value_clean = representado_cuit_value.replace("-", "")
                        if representado_cuit_value_clean == str(representado_cuit):
                            pass
                        else:
                            error_logs.append(f"No se pudo seleccionar el representado. Se devuelven los comprobantes del Contribuyente {representado_cuit_value}")
                    
                    
                    if descarga_recibidos:

                        await page1.wait_for_load_state("networkidle")
                        await page1.get_by_text("Comprobantes Recibidos", exact=True).click()
                        await page1.wait_for_load_state("networkidle")
                        await page1.get_by_label("Fecha del Comprobante *").click()
                        # Limpiar los campos de fecha
                        # Limpiar el campo desde
                        campo_desde = page1.locator("input[name=\"daterangepicker_start\"]")
                        await campo_desde.click()
                        await campo_desde.fill("")
                        await campo_desde.fill(desde_str)
                        
                        # Limpiar el campo hasta
                        campo_hasta = page1.locator("input[name=\"daterangepicker_end\"]")
                        await campo_hasta.click()
                        await campo_hasta.fill("")
                        await campo_hasta.fill(hasta_str)
    
                        # await page1.get_by_role("textbox", name="Fecha del Comprobante *").fill(f"{desde_str} - {hasta_str}")
                        
                        if puntos_venta_recibidos and len(puntos_venta_recibidos) > 0:
                                await page1.get_by_role("textbox", name="Punto de Venta").fill(",".join(puntos_venta_recibidos))
                        
                        await page1.get_by_role("button", name="Aplicar").click()
                        await page1.get_by_role("button", name="Buscar").click()
                        await page1.wait_for_load_state("networkidle")

                        # Descarga CSV
                        try:
                            async with page1.expect_download() as download_info:
                                await page1.get_by_role("button", name="CSV").click(timeout=timeout_mc*1000)
                            download = await download_info.value
                            await download.save_as(path=f"{path_descarga}/{mc_r}.zip")
                        except PlaywrightTimeoutError:
                            error_logs.append(f"no se pudo seleccionar el archivo en {timeout_mc} segundos, intentar con otro valor en la solicitud")
                            success = False
                            return _cleanup_and_return(
                                df_r,
                                df_e,
                                df_r_base64,
                                df_e_base64,
                                error_logs,
                                success,


                            )
                        except Exception as e:
                            error_logs.append(f"Error durante la descarga de Mis Comprobantes Recibidos.: {e}")
                            success = False
                            return _cleanup_and_return(
                                df_r,
                                df_e,
                                df_r_base64,
                                df_e_base64,
                                error_logs,
                                success,


                            )
                        

                        if carga_json:
                            try:
                                # Extraer el zip en un csv con el mismo nombre
                                with zipfile.ZipFile(f"{path_descarga}/{mc_r}.zip", 'r') as zip_ref:
                                    archivo_extraido = zip_ref.namelist()[0]
                                    zip_ref.extract(archivo_extraido, path_descarga)
                                    archivo_csv = f"{path_descarga}/{mc_r}.csv"
                                    os.rename(f"{path_descarga}/{archivo_extraido}", archivo_csv)
                                    df_r = await filtrar_csv_por_rango(archivo_csv, desde_str, hasta_str, archivo_csv, representado_cuit)
                                    # eliminar archivos
                                    os.remove(archivo_csv)
                                    # os.remove(f"{path_descarga}/{mc_r}.zip")
                            except Exception as e:
                                error_logs.append(f"Error al convertir a json el archivo de Mis Comprobantes Recibidos.: {e}")
                        
                        # convertir el zip a base64
                        if carga_b64:
                            try:    
                                # Convertir el zip a base64 directamente
                                with open(f"{path_descarga}/{mc_r}.zip", "rb") as zip_file:
                                    df_r_base64 = base64.b64encode(zip_file.read()).decode('utf-8')
                            except Exception as e:
                                error_logs.append(f"Error al convertir a base64 el archivo de Mis Comprobantes Recibidos.: {e}")
                            
                            os.remove(f"{path_descarga}/{mc_r}.zip")

                        await page1.get_by_text("Menú Principal", exact=True).click()
                        
                        
                    if descarga_emitidos:
                        
                        await page1.wait_for_load_state("networkidle")
                        await page1.get_by_text("Comprobantes Emitidos", exact=True).click()
                        await page1.wait_for_load_state("networkidle")
                        await page1.get_by_label("Fecha del Comprobante *").click()
                        # Limpiar campos de fecha
                        
                        # Limpiar el campo desde
                        campo_desde = page1.locator("input[name=\"daterangepicker_start\"]")
                        await campo_desde.click()
                        await campo_desde.fill("")
                        await campo_desde.fill(desde_str)
                        
                        # Limpiar el campo hasta
                        campo_hasta = page1.locator("input[name=\"daterangepicker_end\"]")
                        await campo_hasta.click()
                        await campo_hasta.fill("")
                        await campo_hasta.fill(hasta_str)
                        
                        # await page1.get_by_role("textbox", name="Fecha del Comprobante *").fill(f"{desde_str} - {hasta_str}")
                        
                        if puntos_venta_emitidos and len(puntos_venta_emitidos) > 0:
                                await page1.get_by_role("textbox", name="Punto de Venta").fill(",".join(puntos_venta_emitidos))
                                
                        await page1.get_by_role("button", name="Aplicar").click()
                        await page1.get_by_role("button", name="Buscar").click()
                        await page1.wait_for_load_state("networkidle")

                        try:
                            async with page1.expect_download() as download_info:
                                await page1.get_by_role("button", name="CSV").click(timeout=timeout_mc*1000)
                            download = await download_info.value
                            await download.save_as(path=f"{path_descarga}/{mc_e}.zip")
                        except PlaywrightTimeoutError:
                            error_logs.append(f"no se pudo seleccionar el archivo en {timeout_mc} segundos, intentar con otro valor en la solicitud")
                            success = False
                            return _cleanup_and_return(
                                df_r,
                                df_e,
                                df_r_base64,
                                df_e_base64,
                                error_logs,
                                success,


                            )
                        except Exception as e:
                            error_logs.append(f"Error durante la descarga de Mis Comprobantes Emitidos.: {e}")
                            success = False
                            return _cleanup_and_return(
                                df_r,
                                df_e,
                                df_r_base64,
                                df_e_base64,
                                error_logs,
                                success,


                            )
                        

                        if carga_json:
                            try:
                                # Extraer el zip en un csv con el mismo nombre
                                with zipfile.ZipFile(f"{path_descarga}/{mc_e}.zip", 'r') as zip_ref:
                                    # convertir el zip a base64
                                    archivo_extraido = zip_ref.namelist()[0]
                                    zip_ref.extract(archivo_extraido, path_descarga)
                                    archivo_csv = f"{path_descarga}/{mc_e}.csv"
                                    os.rename(f"{path_descarga}/{archivo_extraido}", archivo_csv)
                                    df_e = await filtrar_csv_por_rango(archivo_csv, desde_str, hasta_str, archivo_csv, representado_cuit)
                                    # eliminar archivos
                                    # os.remove(archivo_csv)
                                    # os.remove(f"{path_descarga}/{mc_e}.zip")
                            except Exception as e:
                                error_logs.append(f"Error al tratar de convertir el archivo a Dataframe: {str(e)}")

                        # convertir el zip a base64
                        if carga_b64:
                            try:
                                # Convertir el zip a base64 directamente
                                with open(f"{path_descarga}/{mc_e}.zip", "rb") as zip_file:
                                    df_e_base64 = base64.b64encode(zip_file.read()).decode('utf-8')
                            except Exception as e:
                                error_logs.append(f"Error al convertir a base64 el archivo de Mis Comprobantes Emitidos.: {str(e)}")


                    if eliminar_descargas and temp_dir is None:
                        try:
                            rmtree(path_descarga)
                        except:
                            pass
                        
                except:
                    error_logs.append("No se encuentra habilitado el servicio de Mis Comprobantes")

            except Exception as e:
                error_logs.append(f"No se pudo iniciar sesión en ARCA. Verifique los datos ingresados: {str(e)}")
                await context.close()
                await browser.close()
                success = False
                return _cleanup_and_return(
                    df_r,
                    df_e,
                    df_r_base64,
                    df_e_base64,
                    error_logs,
                    success,


                )

            await context.close()
            await browser.close()
            
            return _cleanup_and_return(
                df_r,
                df_e,
                df_r_base64,
                df_e_base64,
                error_logs,
                success,


            )
        

async def solicitar_consulta_mis_comprobantes(
    desde: str,
    hasta: str,
    cuit_inicio_sesion: str,
    representado_nombre: str,
    representado_cuit: str,
    contrasena: str,
    emitidos: bool = False,
    recibidos: bool = False,
    punto_venta_emitidos: Optional[list] = None,
    punto_venta_recibidos: Optional[list] = None,
    proxy_request: bool = False
    ) -> tuple[dict, str, list, bool]:
    """
    Automatiza la solicitud de consultas de comprobantes electrónicos en el sistema Mis Comprobantes de ARCA/AFIP.
    
    Esta función utiliza Playwright para simular la interacción de un usuario real con el portal web de AFIP,
    realizando el login, navegando por las secciones de comprobantes emitidos y/o recibidos, completando los
    formularios de búsqueda con el rango de fechas especificado, y capturando los IDs de consulta generados
    por el sistema junto con las cookies de sesión necesarias para operaciones posteriores.
    
    Flujo de ejecución:
    1. Inicia sesión en el portal de AFIP con las credenciales proporcionadas
    2. Navega al servicio "MIS COMPROBANTES"
    3. Selecciona el representado (si aplica)
    4. Para cada tipo de comprobante solicitado (emitidos/recibidos):
       - Navega a la sección correspondiente
       - Completa el formulario con el rango de fechas
       - Ejecuta la búsqueda
       - Captura el response AJAX con el idConsulta generado
       - Vuelve al menú principal
    5. Extrae y retorna las cookies de sesión para uso posterior
    
    La función NO descarga archivos, solo solicita las consultas y devuelve los identificadores necesarios
    para recuperar los resultados posteriormente mediante otras operaciones (descarga, consulta de estado, etc.).

    Args:
        desde (str): Fecha de inicio del rango de consulta en formato 'dd/mm/yyyy' (ej: '01/01/2025')
        hasta (str): Fecha de fin del rango de consulta en formato 'dd/mm/yyyy' (ej: '31/01/2025')
        cuit_inicio_sesion (str): CUIT del usuario que iniciará sesión en AFIP (puede incluir guiones o puntos)
        representado_nombre (str): Nombre o razón social del representado (usado para logs/identificación)
        representado_cuit (str): CUIT del contribuyente a consultar (puede ser el mismo que cuit_inicio_sesion)
        contrasena (str): Clave fiscal del usuario para autenticación en el portal de AFIP
        emitidos (bool, optional): Si es True, solicita consulta de comprobantes emitidos. Por defecto False.
        recibidos (bool, optional): Si es True, solicita consulta de comprobantes recibidos. Por defecto False.
        punto_venta_emitidos (Optional[list], optional): Lista de puntos de venta para filtrar comprobantes emitidos. Por defecto None.
        punto_venta_recibidos (Optional[list], optional): Lista de puntos de venta para filtrar comprobantes recibidos. Por defecto None.
        proxy_request (bool, optional): Si es True, utiliza configuración de proxy. Por defecto False.

    Returns:
        tuple[dict, str, list, bool]: Una tupla con 4 elementos:
            - dict: Resultados de las consultas con la estructura:
                {
                    "recibidos": {
                        "estado": "ok",
                        "idConsulta": "150229278",
                        "estado_consulta": "PE"  # PE=Pendiente, OK=Completado
                    },
                    "emitidos": {
                        "estado": "ok",
                        "idConsulta": "150229282",
                        "estado_consulta": "PE"
                    }
                }
            - str: Cookies de sesión en formato header (ej: "SRV=xyz; TS01239d0c=abc...")
            - list: Lista de mensajes de error/warning ocurridos durante la ejecución
            - bool: True si la operación fue exitosa, False si hubo errores críticos
    
    Raises:
        No lanza excepciones directamente, todos los errores son capturados y retornados en error_logs.
    
    Example:
        >>> resultados, cookies, logs, success = await solicitar_consulta_mis_comprobantes(
        ...     desde="01/01/2025",
        ...     hasta="31/01/2025",
        ...     cuit_inicio_sesion="20123456789",
        ...     representado_nombre="MI EMPRESA SRL",
        ...     representado_cuit="20123456789",
        ...     contrasena="MiClaveFiscal123",
        ...     emitidos=True,
        ...     recibidos=True
        ... )
        >>> if success:
        ...     print(f"ID consulta emitidos: {resultados['emitidos']['idConsulta']}")
        ...     print(f"Cookies: {cookies[:50]}...")
    
    Notes:
        - La función requiere que playwright esté instalado y configurado
        - Los idConsulta devueltos tienen una validez temporal en el sistema de AFIP
        - Las cookies de sesión son necesarias para realizar descargas o consultas posteriores
        - Si aparece un CAPTCHA, se registra en los logs pero la función intenta continuar
        - La función simula comportamiento humano sin delays artificiales
    """
    
    desde = pd.to_datetime(desde, format='%d/%m/%Y')
    hasta = pd.to_datetime(hasta, format='%d/%m/%Y')
    desde_str = desde.strftime('%d/%m/%Y')
    hasta_str = hasta.strftime('%d/%m/%Y')
    
    cuits = [representado_cuit, cuit_inicio_sesion]
    
    # por cada cuit en cuits, limpiarlo (eliminando espacios, guiones, puntos y /)
    for i in range(len(cuits)):
        cuits[i] = str(cuits[i]).replace(" ", "").replace("-", "").replace(".", "").replace("/", "")    
    
    error_logs = []
    success = True
    resultados: dict = {}
    cookies_header = str("")

    proxy = get_proxy_config() if proxy_request==True else None

    request_descargas = [emitidos, recibidos]

    if not any(request_descargas):
        success = False
        error_logs.append("No se seleccionó ningún tipo de comprobante para consultar (emitidos o recibidos).")
        return resultados, cookies_header, error_logs, success

    async def _ejecutar_consulta(page_obj, tipo: str):
        """Ejecuta la consulta interactuando con la UI y captura el request AJAX"""
        
        def _extraer_id_consulta(payload):
            if not isinstance(payload, dict):
                return None
            datos = payload.get("datos")
            if isinstance(datos, dict):
                if datos.get("idConsulta"):
                    return datos.get("idConsulta")
                if datos.get("id"):
                    return datos.get("id")
            for key in ("idConsulta", "id_consulta", "idConsultaAsync", "id"):
                if payload.get(key):
                    return payload.get(key)
            return None
        
        try:
            await page_obj.wait_for_load_state("networkidle")
            await page_obj.get_by_label("Fecha del Comprobante *").click()
            await page_obj.wait_for_load_state("networkidle")
            await page_obj.locator('//*[@id="fechaEmision"]').click()
            await page_obj.wait_for_load_state("networkidle")
            
            # Limpiar y llenar fecha desde
            campo_desde = page_obj.locator("input[name=\"daterangepicker_start\"]")
            await campo_desde.click()
            await campo_desde.fill("")
            await campo_desde.fill(desde_str)
            
            # Limpiar y llenar fecha hasta
            campo_hasta = page_obj.locator("input[name=\"daterangepicker_end\"]")
            await campo_hasta.click()
            await campo_hasta.fill("")
            await campo_hasta.fill(hasta_str)
            await page_obj.get_by_role("button", name="Aplicar").click()
            
            # Llenar puntos de venta según corresponda
            if tipo == "emitidos" and punto_venta_emitidos is not None:
                if len(punto_venta_emitidos) > 0:
                    puntos_venta_str = ",".join([str(pv) for pv in punto_venta_emitidos])
                    await page_obj.get_by_role("textbox", name="Punto de Venta").fill(puntos_venta_str)
            elif tipo == "recibidos" and punto_venta_recibidos is not None:
                if len(punto_venta_recibidos) > 0:
                    puntos_venta_str = ",".join([str(pv) for pv in punto_venta_recibidos])
                    await page_obj.get_by_role("textbox", name="Punto de Venta").fill(puntos_venta_str)
            
            # Usar wait_for_response para capturar el AJAX
            async with page_obj.expect_response(lambda response: "ajax.do?f=generarConsulta" in response.url and f"&t={tipo}" in response.url) as response_info:
                await page_obj.get_by_role("button", name="Buscar").click()
            
            ajax_response = await response_info.value
            await page_obj.wait_for_load_state("networkidle")
            
            status = ajax_response.status
            
            try:
                texto = await ajax_response.text()
                json_resp = json.loads(texto)
                respuesta = {"status": status, "json": json_resp, "text": None}
            except Exception as e:
                texto = await ajax_response.text()
                respuesta = {"status": status, "json": None, "text": texto}
            
        except Exception as e:
            error_logs.append(f"Error al ejecutar la consulta tipo {tipo}: {str(e)}")
            return None

        if respuesta.get("status") != 200:
            error_logs.append(f"Consulta tipo {tipo} devolvió código {respuesta.get('status')}")
            return None

        json_resp = respuesta.get("json")
        if json_resp is None:
            texto = respuesta.get("text", "")
            error_logs.append(f"Respuesta no JSON en consulta tipo {tipo}: {texto[:200]}")
            return None

        if not isinstance(json_resp, dict):
            error_logs.append(f"Respuesta JSON no es un diccionario para tipo {tipo}")
            return None
        
        estado = json_resp.get("estado")
        datos = json_resp.get("datos", {})
        id_consulta = None
        estado_consulta = None
        
        if isinstance(datos, dict):
            id_consulta = datos.get("idConsulta")
            estado_consulta = datos.get("estado")
        
        if not id_consulta:
            id_consulta = _extraer_id_consulta(json_resp)
        
        if not id_consulta:
            error_logs.append(f"No se pudo obtener idConsulta de la respuesta en consulta tipo {tipo}.")
            return None

        resultado = {
            "estado": estado,
            "idConsulta": str(id_consulta),
            "estado_consulta": estado_consulta,
        }
        
        # La estimación se puede hacer opcionalmente en otro momento
        # No hacemos el goto para no perder el estado de la página

        return resultado
    
    async with limit_browser(), async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True,
                                                   proxy=proxy)
        context = await browser.new_context()
        page = await context.new_page()

        page1 = await _login_y_abrir_servicio_mis_comprobantes(
            page,
            str(cuit_inicio_sesion),
            contrasena,
            error_logs,
        )
        if page1 is None:
            await context.close()
            await browser.close()
            success = False
            return resultados, cookies_header, error_logs, success
        if page1 is not page and not page.is_closed():
            await page.close()

        try:
            try:

                cookies = await context.cookies("https://fes.afip.gob.ar/")
                cookies_header = "; ".join([f"{cookie['name']}={cookie['value']}" for cookie in cookies])

                try:
                    await page1.wait_for_load_state("networkidle")
                    representado_cuit_2 = str(representado_cuit)
                    representado_cuit_2 = representado_cuit_2[0:2] + "-" + representado_cuit_2[2:10] + "-" + representado_cuit_2[10:11]
                    await page1.locator(f'small.pull-right:has-text("{representado_cuit_2}")').click(timeout=3000)
                except Exception as e_rep:
                    try:
                        representado_cuit_text = await page1.locator("span.nombre-propio.nombre-activo.pull-righ").text_content()
                        representado_cuit_value = representado_cuit_text.split("[")[1].split("]")[0]
                        representado_cuit_value_clean = representado_cuit_value.replace("-", "")
                        if representado_cuit_value_clean == str(representado_cuit):
                            pass
                        else:
                            error_logs.append(f"No se pudo seleccionar el representado. Se realizó la consulta del Contribuyente {representado_cuit_value}")
                    except Exception as e_rep2:
                        error_logs.append(f"Error al verificar representado: {str(e_rep2)}")
                
                # Ejecutar consultas
                if recibidos:
                    await page1.wait_for_load_state("networkidle")
                    await page1.get_by_text("Comprobantes Recibidos", exact=True).click()
                    consulta_recibidos = await _ejecutar_consulta(page1, "R")
                    if consulta_recibidos:
                        resultados["recibidos"] = consulta_recibidos
                    await page1.wait_for_load_state("networkidle")
                    await page1.get_by_text("Menú Principal", exact=True).click()
                    
                if emitidos:
                    await page1.wait_for_load_state("networkidle")
                    await page1.get_by_text("Comprobantes Emitidos", exact=True).click()
                    consulta_emitidos = await _ejecutar_consulta(page1, "E")
                    if consulta_emitidos:
                        resultados["emitidos"] = consulta_emitidos
                    await page1.wait_for_load_state("networkidle")
                    await page1.get_by_text("Menú Principal", exact=True).click()

            except Exception as e:
                error_logs.append(f"Error al acceder al servicio de Mis Comprobantes: {str(e)}")

        except Exception as e:
            error_logs.append(f"No se pudo iniciar sesión en ARCA. Verifique los datos ingresados: {str(e)}")
            await context.close()
            await browser.close()
            success = False
            return resultados, cookies_header, error_logs, success

        await context.close()
        await browser.close()
        
        return resultados, cookies_header, error_logs, success
    

async def descarga_csv_consulta(
    fecha_desde: str,
    fecha_hasta: str,
    cuit_inicio_sesion: str,
    representado_nombre: str,
    representado_cuit: str,
    contrasena: str,
    eliminar_descargas: bool = False,
    descarga_emitidos: bool = False,
    descarga_recibidos: bool = False,

    carga_b64: bool = False,
    carga_json: bool = False,
    cookies_header: str = None,
    proxy_request: bool = False,
    verbose: bool = False
) -> tuple[pd.DataFrame, pd.DataFrame, str, str, list, bool, str, str, str, str, dict]:
    """
    Descarga el CSV de consultas ya realizadas en Mis Comprobantes de ARCA/AFIP.
    
    Esta función accede al historial de consultas realizadas previamente en el sistema,
    localiza las consultas correspondientes al rango de fechas especificado y descarga los
    archivos CSV con los resultados. Puede descargar emitidos, recibidos o ambos.
    
    Flujo de ejecución:
    1. Inicia sesión en el portal de AFIP (si no se proveen cookies)
    2. Navega al servicio "MIS COMPROBANTES"
    3. Para cada tipo solicitado (emitidos/recibidos):
       - Accede a la sección correspondiente
       - Navega al Historial de consultas
       - Localiza la consulta por rango de fechas
       - Descarga el archivo CSV
       - Guarda el archivo en la ubicación especificada
       - Vuelve al menú principal

    Args:
        fecha_desde (str): Fecha de inicio en formato 'dd/mm/yyyy' (ej: '01/01/2024')
        fecha_hasta (str): Fecha de fin en formato 'dd/mm/yyyy' (ej: '31/01/2024')
        cuit_inicio_sesion (str): CUIT del usuario que iniciará sesión en AFIP
        representado_nombre (str): Nombre o razón social del representado
        representado_cuit (str): CUIT del contribuyente consultado
        contrasena (str): Clave fiscal del usuario
        eliminar_descargas (bool, optional): opción para eliminar las descargas temporales. Por defecto False.
        descarga_emitidos (bool, optional): Si es True, descarga comprobantes emitidos. Por defecto False.
        descarga_recibidos (bool, optional): Si es True, descarga comprobantes recibidos. Por defecto False.

        carga_b64 (bool, optional): Si es True, devuelve archivos en base64. Por defecto False.
        carga_json (bool, optional): Si es True, devuelve archivos en JSON. Por defecto False.
        cookies_header (str, optional): Cookies de sesión existentes. Si se proveen, no se hace login.
        proxy_request (bool, optional): Si es True, utiliza configuración de proxy. Por defecto False.
        verbose (bool, optional): Si es True, muestra logs de debug detallados. Por defecto False.

    Returns:
        tuple[pd.DataFrame, pd.DataFrame, str, str, list, bool, str, str, str, str, dict]: Una tupla con 11 elementos:
            - pd.DataFrame: DataFrame con comprobantes recibidos
            - pd.DataFrame: DataFrame con comprobantes emitidos
            - str: Archivo recibidos en base64
            - str: Archivo emitidos en base64
            - list: Lista de mensajes de error/warning
            - bool: True si la operación fue exitosa, False si hubo errores críticos
            - dict: Diccionario con información de todas las consultas disponibles en el historial:
                {
                    "emitidos": [
                        {
                            "fecha_desde": "01/01/2024",
                            "fecha_hasta": "15/01/2024",
                            "exportable": True,
                            "estado": "Finalizada"
                        },
                        ...
                    ],
                    "recibidos": [...]
                }

    Raises:
        No lanza excepciones directamente, todos los errores son capturados y retornados en error_logs.

    Example:

        ...     fecha_desde="01/01/2024",
        ...     fecha_hasta="15/01/2024",
        ...     cuit_inicio_sesion="20123456789",
        ...     representado_nombre="MI EMPRESA SRL",
        ...     representado_cuit="20123456789",
        ...     contrasena="MiClaveFiscal123",
        ...     descarga_emitidos=True,
        ...     descarga_recibidos=True,
        ...     carga_json=True,

        ... )
        >>> if success:


        ...     print(f"Consultas en historial: {historial}")

    Notes:
        - Las consultas deben existir previamente en el historial
        - El formato de las fechas en el historial debe coincidir exactamente
        - Requiere que playwright esté instalado y configurado
        - Si se proveen cookies, se evita el login completo
    """
    error_logs = []
    consultas_historial = {}
    
    # Variables de retorno similares a descargar_mis_comprobantes
    df_e = pd.DataFrame()
    df_r = pd.DataFrame()
    df_e_base64 = str("")
    df_r_base64 = str("")


    success = True
    
    # Validar que al menos uno esté seleccionado
    if not descarga_emitidos and not descarga_recibidos:
        error_logs.append("Debe seleccionar al menos emitidos o recibidos")

    # Validar salidas y descargas

    request_descargas = [descarga_emitidos, descarga_recibidos]
    
    if not any(request_salidas) and not any(request_descargas):
        error_logs.append("Debe seleccionar al menos una opción de salida y descarga")

    if not any(request_salidas):
        error_logs.append("Debe seleccionar al menos una opción de salida")

    if not any(request_descargas):
        error_logs.append("Debe seleccionar al menos emitidos o recibidos para descargar")

    # Convertir fechas al formato esperado por el historial (dd/mm/yyyy)
    try:
        desde = pd.to_datetime(fecha_desde, format='%d/%m/%Y')
        hasta = pd.to_datetime(fecha_hasta, format='%d/%m/%Y')
        fecha_desde_historial = desde.strftime('%d/%m/%Y')
        fecha_hasta_historial = hasta.strftime('%d/%m/%Y')
        desde_str = desde.strftime('%d/%m/%Y')
        hasta_str = hasta.strftime('%d/%m/%Y')
    except Exception as e:
        error_logs.append(f"Error al convertir fechas: {str(e)}")

    # Limpiar CUITs
    cuits = [representado_cuit, cuit_inicio_sesion]
    for i in range(len(cuits)):
        cuits[i] = str(cuits[i]).replace(" ", "").replace("-", "").replace(".", "").replace("/", "")
    representado_cuit = cuits[0]
    cuit_inicio_sesion = cuits[1]
    
    # Crear nombres de archivo siguiendo el patrón de descargar_mis_comprobantes
    nro = str(representado_cuit)[-1]
    mc_e = f'{nro} - MCE - {str(desde_str).replace("/", "")} - {str(hasta_str).replace("/", "")} - {representado_cuit} - {representado_nombre}'
    mc_r = f'{nro} - MCR - {str(desde_str).replace("/", "")} - {str(hasta_str).replace("/", "")} - {representado_cuit} - {representado_nombre}'
    
    temp_dir = None
    if eliminar_descargas:
        temp_dir = tempfile.TemporaryDirectory(prefix="miscomprobantes_")
        path_descarga = temp_dir.name
    else:
        path_descarga = os.path.join(os.getcwd(), f"descargas/mis_comprobantes/{representado_nombre}")

    if not path_descarga.endswith("/"):
        path_descarga += "/"

    os.makedirs(path_descarga, exist_ok=True)

    def _cleanup_and_return_historial(
        df_r_value,
        df_e_value,
        df_r_b64,
        df_e_b64,
        logs,
        ok,


        historial,
    ):
        if temp_dir is not None:
            temp_dir.cleanup()
        return (
            df_r_value,
            df_e_value,
            df_r_b64,
            df_e_b64,
            logs,
            ok,


            historial,
        )
    
    proxy = get_proxy_config() if proxy_request else None
    
    async def _descargar_tipo(page_obj, tipo: str, nombre_archivo: str):
        """Descarga CSV para un tipo específico de comprobante, procesa y sube a storage"""
        consultas_tipo = []
        df_resultado = pd.DataFrame()
        df_base64 = str("")

        
        try:
            # Navegar a la sección correspondiente
            await page_obj.wait_for_load_state("networkidle")
            if tipo == "emitidos":
                await page_obj.get_by_text("Comprobantes Emitidos", exact=True).click()
            else:
                await page_obj.get_by_text("Comprobantes Recibidos", exact=True).click()
            
            await page_obj.wait_for_load_state("networkidle")
            
            # Ir a Historial
            await page_obj.get_by_role("link", name="Historial").click()
            await page_obj.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
            
            # Construir el texto a buscar
            texto_busqueda = f"Fecha: {fecha_desde_historial} a {fecha_hasta_historial}"
            if verbose:
                print(f"[DEBUG] Buscando {tipo}: {texto_busqueda}")
            
            # Buscar filas con diferentes selectores
            selectores_tabla = ["table tbody tr", "tbody tr", "tr:has-text('Fecha:')", ".table tbody tr"]
            
            filas = None
            count = 0
            
            for selector in selectores_tabla:
                filas = page_obj.locator(selector)
                count = await filas.count()
                if verbose:
                    print(f"[DEBUG] {tipo} - Selector '{selector}': {count} filas")
                if count > 0:
                    break
            
            if count == 0:
                error_logs.append(f"No se encontraron consultas en el historial de {tipo}")
                return None, consultas_tipo
            
            # Recopilar información de TODAS las consultas en el historial
            for i in range(count):
                fila = filas.nth(i)
                texto_fila = await fila.text_content()
                
                # Extraer información de la fila
                info_consulta = {}
                
                # Buscar patrón de fecha "Fecha: dd/mm/yyyy a dd/mm/yyyy"
                match_fecha = re.search(r'Fecha:\s*(\d{2}/\d{2}/\d{4})\s*a\s*(\d{2}/\d{2}/\d{4})', texto_fila)
                if match_fecha:
                    info_consulta["fecha_desde"] = match_fecha.group(1)
                    info_consulta["fecha_hasta"] = match_fecha.group(2)
                
                # Buscar estado (puede ser "Finalizada", "En proceso", "Error", etc.)
                if "Finalizada" in texto_fila or "finalizada" in texto_fila.lower():
                    info_consulta["estado"] = "Finalizada"
                elif "En proceso" in texto_fila or "en proceso" in texto_fila.lower():
                    info_consulta["estado"] = "En proceso"
                elif "Pendiente" in texto_fila or "pendiente" in texto_fila.lower():
                    info_consulta["estado"] = "Pendiente"
                elif "Error" in texto_fila or "error" in texto_fila.lower():
                    info_consulta["estado"] = "Error"
                else:
                    info_consulta["estado"] = "Desconocido"
                
                # Verificar si tiene botón de exportar disponible
                botones = fila.locator("button")
                count_botones = await botones.count()
                info_consulta["exportable"] = count_botones > 0
                
                # Agregar información completa de la fila para referencia
                info_consulta["texto_completo"] = texto_fila.strip()
                
                if verbose:
                    print(f"[DEBUG] {tipo} - Fila {i}: {info_consulta}")
                
                consultas_tipo.append(info_consulta)
            
            # Buscar la fila con la consulta específica que queremos descargar
            fila_encontrada = None
            for i in range(count):
                fila = filas.nth(i)
                texto_fila = await fila.text_content()
                if texto_busqueda in texto_fila:
                    fila_encontrada = fila
                    if verbose:
                        print(f"[DEBUG] {tipo} - ✓ Consulta encontrada en fila {i}")
                    break
            
            if fila_encontrada is None:
                error_logs.append(f"No se encontró consulta de {tipo} para el rango {fecha_desde} - {fecha_hasta}")

            # Click en el botón de acciones
            botones = fila_encontrada.locator("button")
            count_botones = await botones.count()
            
            if count_botones > 1:
                await botones.nth(1).click()
            else:
                await botones.first.click()
            
            await page_obj.wait_for_load_state("networkidle")
            await asyncio.sleep(0.5)
            
            if verbose:
                print(f"[DEBUG] {tipo} - Iniciando descarga de CSV...")
            
            # Descargar CSV
            async with page_obj.expect_download() as download_info:
                await page_obj.get_by_role("link", name="CSV").click()
            
            download = await download_info.value
            nombre_descarga = download.suggested_filename
            
            # Determinar la extensión del archivo descargado
            if nombre_descarga.endswith('.zip'):
                archivo_final = f"{path_descarga}{nombre_archivo}.zip"
            elif nombre_descarga.endswith('.csv'):
                archivo_final = f"{path_descarga}{nombre_archivo}.csv"
            else:
                # Si no tiene extensión reconocida, usar la original
                archivo_final = f"{path_descarga}{nombre_archivo}_{nombre_descarga}"
            
            await download.save_as(archivo_final)
            
            if verbose:
                print(f"[DEBUG] {tipo} - Archivo guardado: {archivo_final}")
            
            # Procesar el archivo descargado
            try:
                # Si es ZIP, extraer el CSV
                if archivo_final.endswith('.zip'):
                    with zipfile.ZipFile(archivo_final, 'r') as zip_ref:
                        archivo_extraido = zip_ref.namelist()[0]
                        zip_ref.extract(archivo_extraido, path_descarga)
                        archivo_csv = f"{path_descarga}{nombre_archivo}.csv"
                        os.rename(f"{path_descarga}{archivo_extraido}", archivo_csv)
                else:
                    # Si ya es CSV, solo renombrar
                    archivo_csv = archivo_final
                
                # Filtrar CSV por rango de fechas
                if carga_json:
                    df_resultado = await filtrar_csv_por_rango(
                        archivo_csv, 
                        desde_str, 
                        hasta_str, 
                        archivo_csv, 
                        representado_cuit
                    )
                

                # Convertir a base64 si está habilitado
                if carga_b64:
                    try:
                        # Usar el archivo ZIP para base64
                        archivo_zip_b64 = f"{path_descarga}{nombre_archivo}.zip"
                        if not archivo_final.endswith('.zip'):
                            with zipfile.ZipFile(archivo_zip_b64, 'w', zipfile.ZIP_DEFLATED) as zipf:
                                zipf.write(archivo_csv, os.path.basename(archivo_csv))
                        else:
                            archivo_zip_b64 = archivo_final
                        
                        with open(archivo_zip_b64, "rb") as zip_file:
                            df_base64 = base64.b64encode(zip_file.read()).decode('utf-8')
                        if verbose:
                            print(f"[DEBUG] {tipo} - Convertido a base64")
                    except Exception as e:
                        error_logs.append(f"Error al convertir {tipo} a base64: {str(e)}")
                
            except Exception as e:
                error_logs.append(f"Error al procesar archivo de {tipo}: {str(e)}")
            
            # Volver al menú principal
            await page_obj.wait_for_load_state("networkidle")
            await page_obj.get_by_text("Menú Principal", exact=True).click()
            await page_obj.wait_for_load_state("networkidle")
            
            if verbose:
                print(f"[DEBUG] {tipo} - ✓ Descarga completada")
            

        except Exception as e:
            error_logs.append(f"Error al descargar CSV de {tipo}: {str(e)}")

    async with limit_browser(), async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True, proxy=proxy)
        context = await browser.new_context()
        page = await context.new_page()
        
        try:
            # Login (si no hay cookies)
            if not cookies_header:
                page1 = await _login_y_abrir_servicio_mis_comprobantes(
                    page,
                    str(cuit_inicio_sesion),
                    contrasena,
                    error_logs,
                )
                if page1 is None:
                    await context.close()
                    await browser.close()
                    return _cleanup_and_return_historial(
                        df_r,
                        df_e,
                        df_r_base64,
                        df_e_base64,
                        error_logs,
                        False,


                        consultas_historial,
                    )
                if page1 is not page and not page.is_closed():
                    await page.close()
            else:
                # Si hay cookies, ir directamente a Mis Comprobantes
                await page.goto("https://fes.afip.gob.ar/mis-comprobantes/", timeout=15000)
                page1 = page
            
            # Seleccionar representado si es necesario
            try:
                await page1.wait_for_load_state("networkidle")
                representado_cuit_2 = str(representado_cuit)
                representado_cuit_2 = representado_cuit_2[0:2] + "-" + representado_cuit_2[2:10] + "-" + representado_cuit_2[10:11]
                await page1.locator(f'small.pull-right:has-text("{representado_cuit_2}")').click(timeout=3000)
            except Exception as e_rep:
                try:
                    representado_cuit_text = await page1.locator("span.nombre-propio.nombre-activo.pull-righ").text_content()
                    representado_cuit_value = representado_cuit_text.split("[")[1].split("]")[0]
                    representado_cuit_value_clean = representado_cuit_value.replace("-", "")
                    if representado_cuit_value_clean != str(representado_cuit):
                        error_logs.append(f"Advertencia: Se descargará del contribuyente {representado_cuit_value}")
                except Exception as e_rep2:
                    error_logs.append(f"Error al verificar representado: {str(e_rep2)}")
            
            # Descargar recibidos si está solicitado
            if descarga_recibidos:
                consultas_historial["recibidos"] = consultas_rec

            # Descargar emitidos si está solicitado
            if descarga_emitidos:
                consultas_historial["emitidos"] = consultas_emi
        
        except Exception as e:
            error_logs.append(f"Error general en descarga: {str(e)}")
            success = False
        
        finally:
            await context.close()
            await browser.close()
    
    # Limpiar archivos temporales si se solicita
    if eliminar_descargas and temp_dir is None:
        try:
            rmtree(path_descarga)
        except:
            pass
    
    return _cleanup_and_return_historial(
        df_r,
        df_e,
        df_r_base64,
        df_e_base64,
        error_logs,
        success,


        consultas_historial,
    )
