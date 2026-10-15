import asyncio
import logging
import os
import tempfile
import base64
import re
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Optional, List, Any
from pathlib import Path
from datetime import datetime

import pandas as pd
from playwright.async_api import async_playwright, Download

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

BOT_NAME = "vep_archivo"
LOGGER = logging.getLogger(f"{BOT_NAME}_logger")

# --- Debug Capture Class ---

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

# --- Helpers ---

COLUMNAS_REQUERIDAS = [
    "ID_Lote_mrbot",
    "Fecha VEP_mrbot",
    "CUIT Pagador_mrbot",
    "Fecha_Expiracion_mrbot",
    "CUIT_Contibuyente_mrbot",
    "Impuesto_mrbot",
    "Concepto_mrbot",
    "Subconcepto_mrbot",
    "Anticipo_mrbot",
    "Periodo_mrbot",
    "Importe_mrbot",
    "Formulario_mrbot",
    "Cod Pago_mrbot",
]
MAX_REGISTROS_POR_LOTE = 600

@dataclass(frozen=True)
class ArchivoVEP:
    nombre: str
    contenido: str
    cuit: str
    id_lote: str
    nro_lote: int
    total_lotes: int

def generate_registy(
    nro_form: str,
    cod_tipo_pago: str,
    contribuyente_cuit: str,
    concepto: str,
    sub_concepto: str,
    periodo_fiscal: str,
    obligacion_impuesto: str,
    importe: float,
) -> str:
    output_line = (
        f'02<VEP nroFormulario="{nro_form}" codTipoPago="{cod_tipo_pago}" '
        f'contribuyenteCUIT="{contribuyente_cuit}" concepto="{concepto}" '
        f'subConcepto="{sub_concepto}" periodoFiscal="{periodo_fiscal}" '
        f'importe="{importe}"> <Obligacion impuesto="{obligacion_impuesto}" '
        f'importe="{importe}"/></VEP>'
    )
    return output_line

def generate_header(
    cuit: str,
    cantidad_de_registros: int,
    version: str = "0100",
    nro_formulario: str = "20001",
    impuesto: str = "003",
    concepto: str = "003",
    tipo_registro: str = "01",
) -> str:
    header = (
        tipo_registro
        + cuit.zfill(11)
        + nro_formulario.zfill(5)
        + version.zfill(5)
        + impuesto.zfill(3)
        + concepto.zfill(3)
        + str(cantidad_de_registros).zfill(4)
    )
    return header

def normalizar_numero(value) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, str):
        value = value.strip()
    try:
        return str(int(float(value)))
    except (ValueError, TypeError):
        return str(value).strip()

def normalizar_entero(value, field_name: str) -> str:
    normalizado = normalizar_numero(value)
    if normalizado == "" or not normalizado.isdigit():
        raise ValueError(f"El campo {field_name} debe ser un entero.")
    return normalizado

def leer_excel_desde_bytes(excel_bytes: bytes) -> pd.DataFrame:
    try:
        df = pd.read_excel(BytesIO(excel_bytes))
    except Exception as exc:
        raise ValueError("No se pudo leer el archivo Excel.") from exc

    columnas_faltantes = [c for c in COLUMNAS_REQUERIDAS if c not in df.columns]
    if columnas_faltantes:
        raise ValueError("Faltan columnas requeridas: " + ", ".join(columnas_faltantes))

    return df[COLUMNAS_REQUERIDAS].copy()

def generar_archivos_vep(
    df: pd.DataFrame,
    fecha_hoy: str,
) -> list[ArchivoVEP]:
    df = df.copy()
    df["_id_lote"] = df["ID_Lote_mrbot"].apply(normalizar_numero)
    df = df[df["_id_lote"] != ""]

    archivos: list[ArchivoVEP] = []

    for id_lote_actual, df_lote in df.groupby("_id_lote", sort=False):
        if df_lote.empty:
            continue

        primer_registro = df_lote.iloc[0]
        cuit = normalizar_numero(primer_registro["CUIT Pagador_mrbot"]).zfill(11)

        nro_formulario = "20001"
        total_lotes = (len(df_lote) + MAX_REGISTROS_POR_LOTE - 1) // MAX_REGISTROS_POR_LOTE

        for indice_lote in range(total_lotes):
            inicio = indice_lote * MAX_REGISTROS_POR_LOTE
            fin = inicio + MAX_REGISTROS_POR_LOTE
            df_lote_parcial = df_lote.iloc[inicio:fin]
            cantidad_de_registros = len(df_lote_parcial)

            header = generate_header(
                cuit=cuit,
                cantidad_de_registros=cantidad_de_registros + 1,
            )

            registros = []
            for _, row in df_lote_parcial.iterrows():
                cod_tipo_pago = normalizar_entero(row["Cod Pago_mrbot"], "codTipoPago")
                concepto = normalizar_entero(row["Concepto_mrbot"], "concepto")
                sub_concepto = normalizar_entero(row["Subconcepto_mrbot"], "subConcepto")
                obligacion_impuesto = normalizar_entero(
                    row["Impuesto_mrbot"],
                    "Obligacion impuesto",
                )
                registro = generate_registy(
                    nro_form=str(row["Formulario_mrbot"]),
                    cod_tipo_pago=cod_tipo_pago,
                    contribuyente_cuit=str(row["CUIT_Contibuyente_mrbot"]),
                    concepto=concepto,
                    sub_concepto=sub_concepto,
                    periodo_fiscal=str(row["Periodo_mrbot"]),
                    obligacion_impuesto=obligacion_impuesto,
                    importe=float(row["Importe_mrbot"]),
                )
                registros.append(registro)

            output_content = header + "\n" + "\n".join(registros)

            nro_lote = str(indice_lote + 1).zfill(3)
            total_lotes_str = str(total_lotes).zfill(3)
            nombre_archivo = (
                f"F{nro_formulario}.cuit.{cuit}.fecha.{fecha_hoy}.nro.{nro_lote}de{total_lotes_str}.txt"
            )

            archivos.append(
                ArchivoVEP(
                    nombre=nombre_archivo,
                    contenido=output_content,
                    cuit=cuit,
                    id_lote=id_lote_actual,
                    nro_lote=indice_lote + 1,
                    total_lotes=total_lotes,
                )
            )

    return archivos

def crear_zip(archivos: list[ArchivoVEP]) -> BytesIO:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zipf:
        for archivo in archivos:
            arcname = f"{archivo.cuit}/lote_{archivo.id_lote}/{archivo.nombre}"
            zipf.writestr(arcname, archivo.contenido)
    buffer.seek(0)
    return buffer

async def generar_vep_desde_archivo_logger(
    archivo_txt: str,
    cuit_inicio_sesion: str,
    medio_pago: str,
    contrasena: str,
    headless: bool = True,
    b64_PDF_data: bool = False,
    proxy_request: bool = False,
    ) -> tuple[dict[str, str], list[str], bool]:
    """
    Genera un VEP desde un archivo .txt y descarga el detalle, con Logging detallado.
    """

    results: dict[str, str] = {}
    error_logs: list[str] = []
    success = True
    
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot VEP Archivo Logger")

    # Use debug directories
    output_dir = debug.descargas_dir
    har_path = debug.har_dir / f"{BOT_NAME}.har"
    
    medio_pagos_habilitados = [
        "link",
        "pago_mis_cuentas",
        "internet_banking",
        "xn_group",
        "qr",
    ]
    
    if medio_pago not in medio_pagos_habilitados:
        error_logs.append(f"Medio de pago '{medio_pago}' no está habilitado.")
        return results, error_logs, False

    if not os.path.exists(archivo_txt):
        error_logs.append(f"No se encontró el archivo: {archivo_txt}")
        return results, error_logs, False

    proxy = get_proxy_config() if proxy_request else None

    async def _set_input_file(page, archivo: str) -> None:
        selectors = [
            '#VepSeleccionarArchivo input[type="file"]',
            'input[type="file"]',
            'input[type="file"][name]',
            'input[type="file"][id]',
            'input[id*="file"]',
            'input[name*="file"]',
        ]
        try:
            await page.wait_for_selector(
                '#VepSeleccionarArchivo input[type="file"]',
                timeout=5000,
                state="attached",
            )
        except Exception:
            pass
        for selector in selectors:
            locator = page.locator(selector)
            if await locator.count() > 0:
                await locator.first.set_input_files(archivo)
                return

        label = page.locator('label:has-text("Buscá en tus archivos")').first
        try:
            if await label.count() > 0:
                input_id = await label.get_attribute("for")
                if input_id:
                    file_input = page.locator(f"#{input_id}")
                    if await file_input.count() > 0:
                        await file_input.set_input_files(archivo)
                        return
        except Exception:
            pass

        raise RuntimeError("No se encontró input[type=file] para cargar el archivo.")

    def _truncate_text(value: str, limit: int = 300) -> str:
        value = value or ""
        if len(value) <= limit:
            return value
        return value[:limit] + "...(trunc)"

    async def _is_error_screen(page) -> bool:
        try:
            if "#/error" in (page.url or ""):
                return True
        except Exception:
            pass
        try:
            if await page.locator("#HttpError").count() > 0:
                return True
        except Exception:
            pass
        return False

    async def _extract_error_details(page) -> dict[str, str]:
        details: dict[str, str] = {}
        try:
            detalle = page.locator("#HttpError .detalle").first
            if await detalle.count() > 0:
                details["detalle"] = (await detalle.inner_text()).strip()
        except Exception:
            pass
        try:
            card_body = page.locator("#HttpErrorMensaje .card-body").first
            if await card_body.count() > 0:
                details["card_text"] = (await card_body.inner_text()).strip()
        except Exception:
            pass
        return details

    async def _volver_a_inicio_vep(page) -> None:
        try:
            await page.get_by_text("close", exact=True).click(timeout=2000)
        except Exception:
            pass
        try:
            await page.get_by_role("button", name="Close").click(timeout=2000)
        except Exception:
            pass
        try:
            await page.get_by_role("button", name="Cancelar").click(timeout=2000)
        except Exception:
            pass
        try:
            await page.get_by_text("Pagos", exact=True).click(timeout=5000)
        except Exception:
            pass

    async def _download_to_bytes(download: Download) -> tuple[bytes, str]:
        path = await download.path()
        filename = download.suggested_filename or "vep_detalle.pdf"
        if path is None:
            tmp = tempfile.NamedTemporaryFile(delete=False)
            tmp_path = tmp.name
            tmp.close()
            await download.save_as(tmp_path)
            path = tmp_path
        try:
            with open(path, "rb") as f:
                return f.read(), filename
        finally:
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

    def _decode_qr_src(qr_src: str) -> Optional[bytes]:
        if not qr_src:
            return None
        qr_src = qr_src.strip()
        if qr_src.startswith("data:"):
            try:
                header, payload = qr_src.split(",", 1)
            except ValueError:
                return None
            if "base64" not in header:
                return None
            payload = payload.strip()
            try:
                return base64.b64decode(payload)
            except Exception:
                return None
        try:
            return base64.b64decode(qr_src)
        except Exception:
            return None

    def _qr_filename(pdf_name: str) -> str:
        pdf_name = pdf_name or "vep_detalle.pdf"
        base_name = os.path.basename(pdf_name)
        stem, _ = os.path.splitext(base_name)
        return f"{stem}_QR.png"

    async def _extract_qr_src(page) -> Optional[str]:
        try:
            qr_modal = page.locator('div.modal:has-text("Pagar con QR")')
            if await qr_modal.count() == 0:
                return None
            qr_img = qr_modal.locator("img").first
            if await qr_img.count() == 0:
                return None
            return await qr_img.get_attribute("src")
        except Exception:
            return None

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=headless,
                                                       proxy=proxy)
            context = await browser.new_context(
                accept_downloads=True,
                record_har_path=har_path,
                record_har_content="embed"
            )
            page = await context.new_page()

            await debug.capture(page, "inicio")

            login_result = await login_arca(
                page,
                str(cuit_inicio_sesion),
                contrasena,
                error_logs=error_logs,
            )
            await debug.capture(page, "post_login")
            
            if not login_result.success:
                debug.log(f"Login failed: {error_logs}")
                await context.close()
                await browser.close()
                return results, error_logs, False

            page1 = await open_arca_service(
                page,
                service_name="PRESENTACIÓN DE DDJJ Y PAGOS",
                service_matchers=(
                    "PRESENTACIÓN DE DDJJ Y PAGOS",
                    re.compile(r"PRESENTACI[ÓO]N\s+DE\s+DDJJ\s+Y\s+PAGOS", re.I),
                ),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo desplegar 'Ver todos' en ARCA",
                service_unavailable_message="No se pudo abrir el servicio Presentación de DDJJ y Pagos",
            )
            if page1 is None:
                debug.log("Service not found or unavailable")
                await context.close()
                await browser.close()
                return results, error_logs, False
            if page1 is not page and not page.is_closed():
                await page.close()
            await page1.wait_for_load_state("networkidle")
            
            await debug.capture(page1, "servicio_abierto")

            try:
                await page1.get_by_role("button", name="Aceptar").click(timeout=5000)
            except Exception:
                pass

            try:
                await page1.get_by_role("button", name="Close").click(timeout=5000)
            except Exception:
                pass
            await debug.capture(page1, "post_aceptar")

            try:
                await page1.get_by_text("Pagos", exact=True).click(timeout=5000)
            except Exception:
                pass
            await debug.capture(page1, "click_pagos")

            try:
                await page1.get_by_text("close", exact=True).click(timeout=5000)
            except Exception:
                pass
            await debug.capture(page1, "pagos_loaded")

            try:
                max_reintentos = max(0, int(os.getenv("VEP_RETRY_GENERAR", "2")))
            except ValueError:
                max_reintentos = 2

            for intento in range(1, max_reintentos + 2):
                try:
                    await page1.get_by_role("button", name="Generar VEP").click(timeout=5000)
                except Exception:
                    if intento <= max_reintentos:
                        await _volver_a_inicio_vep(page1)
                        continue
                    raise

                try:
                    await page1.get_by_text("description Desde archivo").click(timeout=5000)
                except Exception:
                    if intento <= max_reintentos:
                        await _volver_a_inicio_vep(page1)
                        continue
                    raise

                try:
                    await page1.get_by_role("button", name="Close").click(timeout=5000)
                except Exception:
                    pass
                await debug.capture(page1, "vep_desde_archivo_seleccionado")

                await _set_input_file(page1, archivo_txt)
                await debug.capture(page1, "archivo_cargado")

                try:
                    await page1.get_by_role("button", name="Generar VEP").click(timeout=15000)
                except Exception:
                    if intento <= max_reintentos:
                        await _volver_a_inicio_vep(page1)
                        continue
                    raise

                await asyncio.sleep(1)
                if await _is_error_screen(page1):
                    details = await _extract_error_details(page1)
                    detalle = details.get("detalle") or details.get("card_text")
                    if detalle:
                        error_logs.append(
                            "Pantalla de error posterior a generar VEP: "
                            f"{_truncate_text(detalle)}"
                        )
                    if intento <= max_reintentos:
                        await _volver_a_inicio_vep(page1)
                        continue
                    raise RuntimeError(
                        "Pantalla de error detectada luego de generar VEP."
                    )
                break

            try:
                await page1.get_by_text("close", exact=True).click(timeout=5000)
            except Exception:
                pass
            await debug.capture(page1, "vep_generado")

            medio_pago_img_ids = {
                "qr": "0",
                "link": "1001",
                "pago_mis_cuentas": "1002",
                "internet_banking": "1003",
                "xn_group": "1005",
            }

            try:
                img_id = medio_pago_img_ids[medio_pago]
                await page1.locator(f'img[id="{img_id}"]').click(timeout=5000)
                await asyncio.sleep(0.5)
                try:
                    await page1.get_by_role("button", name="Aceptar").click(timeout=5000)
                except Exception:
                    pass
                await debug.capture(page1, "medio_pago_seleccionado")
            except Exception as e:
                errMsg = f"Error al seleccionar el medio de pago: {e}"
                error_logs.append(errMsg)
                debug.log(errMsg)
                await context.close()
                await browser.close()
                return results, error_logs, False

            download_path = None
            download_name = None
            pdf_data = None
            qr_path = None

            try:
                async with page1.expect_download() as download_info:
                    await page1.get_by_role("button", name="Descargar VEP").click()
                download = await download_info.value
                download_name = download.suggested_filename or "vep_detalle.pdf"
                download_path = output_dir / download_name
                await download.save_as(path=str(download_path))
                with open(download_path, "rb") as f:
                    pdf_data = f.read()
                debug.log(f"Downloaded PDF: {download_path}")
            except Exception as e:
                error_logs.append(f"Error al descargar el detalle del VEP: {e}")
                debug.log(f"Error downloading PDF: {e}")

            if pdf_data is None:
                try:
                    async with page1.expect_popup() as page2_info:
                        await page1.get_by_title("Ver detalle").click()
                    page2 = await page2_info.value
                    async with page2.expect_download() as download1_info:
                        await page2.get_by_role("button", name="picture_as_pdf").click()
                    download1 = await download1_info.value
                    pdf_data, download_name = await _download_to_bytes(download1)
                    download_path = output_dir / download_name
                    with open(download_path, "wb") as f:
                        f.write(pdf_data)
                    debug.log(f"Downloaded PDF (method 2): {download_path}")
                    await page2.close()
                except Exception as e:
                    error_logs.append(f"Error al descargar el PDF del VEP: {e}")
                    debug.log(f"Error downloading PDF (method 2): {e}")
                    success = False

            if pdf_data:
                results["NOMBRE_ARCHIVO"] = download_name or "vep_detalle.pdf"
                if download_path:
                    results["RUTA_ARCHIVO"] = str(download_path)
                if b64_PDF_data:
                    results["B64"] = base64.b64encode(pdf_data).decode("utf-8")

            if medio_pago == "qr":
                qr_src = await _extract_qr_src(page1)
                qr_bytes = _decode_qr_src(qr_src) if qr_src else None
                if qr_bytes:
                    qr_name = _qr_filename(download_name or "vep_detalle.pdf")
                    qr_path = output_dir / qr_name
                    try:
                        with open(qr_path, "wb") as f:
                            f.write(qr_bytes)
                        results["NOMBRE_QR"] = qr_name
                        results["RUTA_QR"] = str(qr_path)
                        debug.log(f"Saved QR: {qr_path}")
                        if b64_PDF_data:
                            results["B64_QR"] = base64.b64encode(qr_bytes).decode(
                                "utf-8"
                            )
                    except Exception as e:
                        error_logs.append(f"Error al guardar QR: {e}")
                else:
                    error_logs.append("No se pudo obtener el QR del VEP.")
                    debug.log("QR not found")

            await context.close()
            await browser.close()
            
    except Exception as e:
        error_logs.append(f"Error global en vep_archivo_bot: {e}")
        debug.log(f"Critical error: {e}")
        success = False

    return results, error_logs, success

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    cuit = os.getenv("VEP_CUIT", "")
    clave = os.getenv("VEP_CLAVE", "")
    medio = os.getenv("VEP_MEDIO_PAGO", "internet_banking")
    file_path = os.getenv("VEP_FILE_PATH", "vep_test.txt")
    headless = os.getenv("VEP_HEADLESS", "True").lower() in ("true", "1", "yes")
    
    if cuit and clave and os.path.exists(file_path):
        res = await generar_vep_desde_archivo_logger(file_path, cuit, medio, clave, headless=headless)
        print(res)
    else:
        print("Skipping VEP execution: Missing credentials or file.")

if __name__ == "__main__":
    asyncio.run(main())
