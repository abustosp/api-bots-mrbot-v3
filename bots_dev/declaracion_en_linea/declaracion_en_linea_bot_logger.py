import asyncio
import json
import os
import re
import shutil
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin

from playwright.async_api import Playwright, async_playwright, TimeoutError as PlaywrightTimeoutError

from shared.arca_login import login_arca, open_arca_service
from shared.public_errors import safe_public_message
from shared.proxy import get_proxy_config

# Import helpers from common version
from declaracion_en_linea.declaracion_en_linea_bot import (
    extract_tables_from_html,
    extract_ddjj_header_fields,
    extract_ddjj_sections,
    es_vep_pagado,
    export_vep_pdf_from_row,
    extract_vep_info_from_html,
    find_vep_row,
    wait_for_vep_rows,
    fetch_vep_info_for_period,
    merge_vep_info,
    normalize_periodo_range,
    is_periodo_in_range,
    build_absolute_url,
    extract_periodo_from_row,
    collect_ddjj_rows,
    build_ddjj_url,
    build_vep_url,
    save_ddjj_results_json,
    get_ddjj_html,
    select_representado_cuit,
    _build_archivo_nombre,
    DOWNLOAD_DIR
)

TEMP_DIR = Path("./temp")
TEMP_DIR.mkdir(exist_ok=True)

def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "declaracion_en_linea") -> None:
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

async def wait_for_ready(page, step_name: str, timeout_ms: int = 30000) -> None:
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        await page.wait_for_load_state("domcontentloaded")

async def goto_with_retry(page, url: str, step_name: str, attempts: int = 3, timeout_ms: int = 60000) -> None:
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        try:
            await page.goto(url, timeout=timeout_ms)
            await wait_for_ready(page, step_name, timeout_ms=timeout_ms)
            return
        except PlaywrightTimeoutError as exc:
            last_error = exc
    raise PlaywrightTimeoutError(f"No se pudo navegar a {url}") from last_error

async def wait_for_ddjj_content(page, timeout_ms: int = 15000) -> None:
    try:
        await page.locator("#ucF931_lblCUIT, .F931CeldaResumenHeader").first.wait_for(
            state="attached",
            timeout=timeout_ms,
        )
    except PlaywrightTimeoutError:
        pass

async def download_ddjj_excel_from_row_logger(
    page,
    row_info: Dict[str, Any],
    periodo: str,
    cuit_representado: str,
    representado_nombre: str,
    download_dir: Path,
    debug: DebugCapture,
) -> Optional[Path]:
    periodo_safe = periodo if periodo else "sin_periodo"
    select_locator = row_info["row"].locator("select[id*='opPapeles']")
    if await select_locator.count() == 0:
        return None

    popup_page = None
    confirm_page = None

    async def build_nomina_url() -> Optional[str]:
        # Logica identica a common
        onchange = await select_locator.first.get_attribute("onchange")
        periodo_nomina = None
        sec_nomina = None
        if onchange:
            match = re.search(r"AbrirPopupNomina\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", onchange)
            if match:
                periodo_nomina = match.group("periodo")
                sec_nomina = match.group("sec")

        f931_div = row_info["row"].locator("div[id*='dvVerF931']")
        if (not periodo_nomina or not sec_nomina) and await f931_div.count() > 0:
            onclick = await f931_div.first.get_attribute("onclick")
            match = re.search(r"AbrirPopupF931\('(?P<periodo>\d{6})','(?P<sec>\d+)'\)", onclick or "")
            if match:
                periodo_nomina = periodo_nomina or match.group("periodo")
                sec_nomina = sec_nomina or match.group("sec")

        periodo_nomina = periodo_nomina or row_info.get("periodo")
        sec_nomina = sec_nomina or "000"
        if not periodo_nomina:
            return None

        base_url = (
            page.url if page and page.url else "https://serviciossegsoc.afip.gob.ar/djproforma/app/consultar/"
        )
        return urljoin(base_url, f"../altadj/ver_nomina.aspx?Periodo={periodo_nomina}&SecDJVig={sec_nomina}")

    try:
        await select_locator.first.scroll_into_view_if_needed()
        existing_pages = set(page.context.pages)
        try:
            async with page.expect_popup(timeout=5000) as popup_info:
                await select_locator.first.select_option("2")
            popup_page = await popup_info.value
            await debug.capture(popup_page, f"popup_nomina_excel_{periodo_safe}")
        except PlaywrightTimeoutError:
            new_pages = [candidate for candidate in page.context.pages if candidate not in existing_pages]
            if new_pages:
                popup_page = new_pages[-1]
                await debug.capture(popup_page, f"new_page_nomina_excel_{periodo_safe}")
            else:
                nomina_url = await build_nomina_url()
                if not nomina_url:
                    return None
                popup_page = await page.context.new_page()
                await goto_with_retry(popup_page, nomina_url, f"ddjj_nomina_{periodo_safe}")
                await debug.capture(popup_page, f"goto_nomina_excel_{periodo_safe}")

        if popup_page is None:
            return None

        await wait_for_ready(popup_page, f"ddjj_excel_popup_{periodo_safe}")

        descargar_locator = popup_page.locator("#ctl00_ContentPlaceHolder1_btnBajarCuiles")
        await descargar_locator.wait_for(state="visible", timeout=5000)
        async with popup_page.expect_download() as download_info:
            confirm_task = asyncio.create_task(popup_page.wait_for_event("popup", timeout=5000))
            await descargar_locator.click()
            try:
                confirm_page = await confirm_task
                await debug.capture(confirm_page, f"confirm_nomina_excel_{periodo_safe}")
            except (PlaywrightTimeoutError, asyncio.TimeoutError):
                confirm_page = None
        download = await download_info.value

        suggested_name = download.suggested_filename or "ddjj_cuiles.xls"
        suffix = Path(suggested_name).suffix or ".xls"
        download_path = download_dir / _build_archivo_nombre(
            cuit_representado=cuit_representado,
            tipo="DDJJ Cuiles",
            periodo=periodo_safe,
            representado_nombre=representado_nombre,
            extension=suffix,
        )
        await download.save_as(str(download_path))
        debug.log(f"Descargado XML Nomina: {download_path}")
        return download_path
    except Exception as exc:
        debug.log(f"Error descarga XML Nomina: {exc}")
        return None
    finally:
        if confirm_page:
            await confirm_page.close()
        if popup_page:
            await popup_page.close()

async def download_vep_pdf_from_page_logger(
    page,
    periodo: str,
    cuit_representado: str,
    representado_nombre: str,
    download_dir: Path,
    debug: DebugCapture,
    nro_vep: Optional[str] = None,
) -> Optional[Path]:
    periodo_safe = periodo if periodo else "sin_periodo"
    try:
        row_count = await wait_for_vep_rows(page)
        debug.log(f"Filas de VEPs renderizadas: {row_count}")

        target_row = await find_vep_row(page, periodo, nro_vep)
        if target_row is None:
            debug.log(f"Sin VEP en el listado para {periodo_safe}")
            return None

        export_link = await export_vep_pdf_from_row(page, target_row)
        if export_link is None:
            debug.log(f"No se pudo abrir el menú de exportación del VEP {periodo_safe}")
            return None

        download_path = download_dir / _build_archivo_nombre(
            cuit_representado=cuit_representado,
            tipo="VEP",
            periodo=periodo_safe,
            representado_nombre=representado_nombre,
            extension=".pdf",
        )
        async with page.expect_download(timeout=30000) as download_info:
            await export_link.click(timeout=15000)
        download = await download_info.value
        await download.save_as(str(download_path))
        debug.log(f"Descargado VEP PDF: {download_path}")
        return download_path
    except Exception as exc:
        debug.log(f"Error descarga VEP PDF: {exc}")
        return None

async def save_vep_pdf_logger(
    page,
    periodo: str,
    cuit_representado: str,
    representado_nombre: str,
    download_dir: Path,
    debug: DebugCapture,
) -> Optional[Path]:
    periodo_safe = periodo if periodo else "sin_periodo"
    pdf_path = download_dir / _build_archivo_nombre(
        cuit_representado=cuit_representado,
        tipo="VEP",
        periodo=periodo_safe,
        representado_nombre=representado_nombre,
        extension=".pdf",
    )
    try:
        await page.emulate_media(media="screen")
        await page.pdf(path=str(pdf_path), format="A4", print_background=True)
        debug.log(f"Guardado VEP PDF (print): {pdf_path}")
        return pdf_path
    except Exception as e:
        debug.log(f"Error guardado VEP PDF (print): {e}")
        pass
    return None

async def save_ddjj_pdf_logger(
    page,
    periodo: str,
    cuit_representado: str,
    representado_nombre: str,
    download_dir: Path,
    debug: DebugCapture,
) -> Optional[Path]:
    periodo_safe = periodo if periodo else "sin_periodo"
    pdf_path = download_dir / _build_archivo_nombre(
        cuit_representado=cuit_representado,
        tipo="DDJJ",
        periodo=periodo_safe,
        representado_nombre=representado_nombre,
        extension=".pdf",
    )
    try:
        await page.emulate_media(media="screen")
        await page.pdf(path=str(pdf_path), format="A4", print_background=True)
        debug.log(f"Guardado DDJJ PDF (print): {pdf_path}")
        return pdf_path
    except Exception as e:
        debug.log(f"Error guardado DDJJ PDF (print): {e}")
        pass
    return None

async def bot_declaracion_en_linea(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: Optional[str] = None,
    representado_nombre: Optional[str] = None,
    periodo_desde: Optional[str] = None,
    periodo_hasta: Optional[str] = None,
    proxy_request: bool = False,
    headless: bool = True,
) -> tuple[dict[str, object], list[str], bool]:
    results: dict[str, object] = {}
    error_logs: list[str] = []
    
    debug = DebugCapture(prefix="declaracion_en_linea")
    debug.log("Iniciando bot Declaracion en Linea Logger")
    
    if not cuit_representante or not clave_representante:
        error_logs.append("Faltan parametros requeridos.")
        return results, error_logs, False
    if not periodo_desde or not periodo_hasta:
        error_logs.append("Periodo desde y hasta requeridos.")
        return results, error_logs, False

    representado_value = (cuit_representado or cuit_representante or "").strip()
    if not representado_value:
        representado_value = cuit_representante
    
    # Directorio de descarga
    output_dir = debug.descargas_dir
    range_start, range_end = normalize_periodo_range(periodo_desde or "", periodo_hasta or "")
    if not range_start or not range_end:
        error_logs.append("Periodo invalido.")
        return results, error_logs, False

    proxy = get_proxy_config() if proxy_request else None

    async with async_playwright() as playwright:
        har_path = debug.har_dir / "declaracion_en_linea.har"
        browser = await playwright.chromium.launch(headless=headless, proxy=proxy)
        context = await browser.new_context(accept_downloads=True, record_har_path=har_path, record_har_content="embed")
        page = await context.new_page()
        await debug.capture(page, "inicio")
        
        try:
            # Login
            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=error_logs,
            )
            await debug.capture(page, "login_completo")
            
            if not login_result.success:
                debug.log(f"Login fallo: {error_logs}")
                return results, error_logs, False
            
            # Abrir servicio
            page1 = await open_arca_service(
                page,
                service_name="DECLARACIÓN EN LÍNEA",
                service_matchers=(
                    re.compile(r"DECLARACI[ÓO]N\s+EN\s+L[ÍI]NEA", re.I),
                    re.compile(r"DECLARACI[ÓO]N\s+EN\s+L[ÍI]NEA\s+Confecci", re.I),
                ),
                error_logs=error_logs,
                service_unavailable_message="No se encuentra habilitado el servicio de DECLARACIÓN EN LÍNEA",
            )
            if page1 is None:
                debug.log("Servicio no disponible")
                return results, error_logs, False
            await debug.capture(page1, "servicio_abierto")

            # Seleccion de representado (si aplica) antes del submit inicial.
            has_representado_select, selected_representado_cuit, representado_options = await select_representado_cuit(
                page1,
                representado_value,
            )
            if has_representado_select:
                if not selected_representado_cuit:
                    options_text = ", ".join(
                        (option.get("label") or option.get("value") or "").strip()
                        for option in representado_options
                        if (option.get("label") or option.get("value"))
                    )
                    error_message = (
                        f"No se encontró el CUIT representado {representado_value} en el selector de representados. "
                        f"Opciones disponibles: {options_text or 'sin opciones'}"
                    )
                    error_logs.append(error_message)
                    debug.log(error_message)
                    await debug.capture(page1, "error_cuit_representado_no_encontrado")
                    return results, error_logs, False

                representado_value = selected_representado_cuit
                debug.log(
                    f"Representado seleccionado: solicitado={cuit_representado or cuit_representante} "
                    f"aplicado={representado_value}"
                )
                await debug.capture(page1, "cuit_representado_seleccionado")

            # Navegacion inicial dentro del servicio
            await page1.get_by_role("button", name="Submit").click()
            await wait_for_ready(page1, "submit")
            await debug.capture(page1, "submit_inicial")
            
            # Ir a Declaraciones Juradas
            try:
                ingrese = page1.locator("a").filter(has_text=re.compile(r"INGRESE", re.I)).first
                if await ingrese.count() == 0:
                    ingrese = page1.locator("td").filter(has_text=re.compile(r"^INGRESE\.\.\.$")).first
                await ingrese.hover(timeout=5000)

                consultas = page1.locator("a").filter(has_text=re.compile(r"para consultar", re.I)).first
                await consultas.hover(timeout=10000)

                ddjj_link = page1.locator("a").filter(
                    has_text=re.compile(r"Declaraciones Juradas (presentadas|generadas)", re.I)
                )
                if await ddjj_link.count() == 0:
                    ddjj_link = page1.locator("a").filter(has_text=re.compile(r"Declaraciones Juradas", re.I))
                await ddjj_link.first.click(timeout=10000)
                await debug.capture(page1, "click_declaraciones_juradas")
            except Exception:
                 # Fallback directo
                direct_url = build_absolute_url(page1.url, "/djproforma/app/consultar/dj_generadas.aspx")
                await goto_with_retry(page1, direct_url, "listado_ddjj_directo")
                await debug.capture(page1, "goto_listado_ddjj_directo")
            
            await wait_for_ready(page1, "listado_ddjj")

            ddjj_rows = await collect_ddjj_rows(page1)
            selected_rows = [
                row_info
                for row_info in ddjj_rows
                if is_periodo_in_range(row_info.get("periodo"), range_start, range_end)
            ]
            if not selected_rows:
                debug.log("No se encontraron DDJJ en el rango solicitado.")
                error_logs.append("No se encontraron DDJJ en el rango solicitado.")
                return results, error_logs, False

            # Preparar popup de VEPs
            if selected_rows:
                primary_row = selected_rows[0]
                page3 = None
                target_url = None
                vep_button = primary_row["row"].locator("[id*='ImageButton3']")
                if await vep_button.count() == 0:
                    vep_button = page1.locator("#ctl00_ContentPlaceHolder1_RptDJGeneradas_ctl01_ImageButton3")
                try:
                    async with page1.expect_popup() as page3_info:
                        await vep_button.first.click()
                    page3 = await page3_info.value
                    await wait_for_ready(page3, "vep_popup")
                    await debug.capture(page3, "vep_popup_abierto")
                    base_url = page3.url.split("#")[0]
                    if "setiweb" in base_url:
                        target_url = f"{base_url}#/pago/consulta-veps"
                    else:
                        target_url = "https://seti.afip.gob.ar/setiweb/#/pago/consulta-veps"
                    await goto_with_retry(page3, target_url, "vep_consulta_inicial")
                    await debug.capture(page3, "vep_consulta_inicial")
                except Exception as e:
                    debug.log(f"Error abriendo popup VEPs: {e}")
                    pass

            periodos_data = {}
            for row_info in selected_rows:
                row_periodo = row_info.get("periodo") or "desconocido"
                debug.log(f"Procesando periodo: {row_periodo}")
                
                # Descargar Excel Nomina
                ddjj_excel_path = await download_ddjj_excel_from_row_logger(
                    page1,
                    row_info,
                    row_periodo,
                    representado_value,
                    representado_nombre or representado_value,
                    output_dir,
                    debug
                )

                # Abrir DDJJ Detalle
                ddjj_url = await build_ddjj_url(page1, row_info)
                if ddjj_url:
                    page2 = await context.new_page()
                    await goto_with_retry(page2, ddjj_url, f"ddjj_abierta_{row_periodo}")
                    await debug.capture(page2, f"ddjj_abierta_{row_periodo}")
                    
                    # Abrir VEP Detalle
                    vep_info = {}
                    vep_pdf_path = None
                    vep_url = await build_vep_url(page1, row_info)
                    if vep_url:
                        vep_page = await context.new_page()
                        await goto_with_retry(vep_page, vep_url, f"vep_detalle_{row_periodo}")
                        await debug.capture(vep_page, f"vep_detalle_{row_periodo}")
                        vep_html = await vep_page.content()
                        vep_info = extract_vep_info_from_html(vep_html, row_periodo)
                        vep_pdf_path = await download_vep_pdf_from_page_logger(
                            vep_page,
                            row_periodo,
                            representado_value,
                            representado_nombre or representado_value,
                            output_dir,
                            debug,
                            nro_vep=(vep_info or {}).get("nro_vep"),
                        )
                        if not vep_pdf_path:
                            vep_pdf_path = await save_vep_pdf_logger(
                                vep_page,
                                row_periodo,
                                representado_value,
                                representado_nombre or representado_value,
                                output_dir,
                                debug
                            )
                        await vep_page.close()

                    # Merge con VEP info global
                    if page3 and target_url:
                        try:
                            vep_info = merge_vep_info(
                                vep_info,
                                await fetch_vep_info_for_period(page3, target_url, row_periodo),
                            )
                        except Exception as e:
                            debug.log(f"Error merging VEP info: {e}")

                    # Click en Borrador
                    try:
                        await page2.locator("#ucF931_dvBorrador").click()
                        await wait_for_ready(page2, "ddjj_borrador")
                        await debug.capture(page2, f"ddjj_borrador_{row_periodo}")
                    except Exception:
                        pass

                    await wait_for_ddjj_content(page2)

                    ddjj_html = await get_ddjj_html(page2)
                    ddjj_fields = extract_ddjj_header_fields(ddjj_html)
                    ddjj_sections = extract_ddjj_sections(ddjj_html)
                    
                    save_ddjj_results_json(
                        ddjj_fields,
                        ddjj_sections,
                        row_periodo,
                        representado_value,
                        representado_nombre or representado_value,
                        vep_info,
                        download_dir=output_dir,
                    )
                    
                    ddjj_pdf_path = await save_ddjj_pdf_logger(
                        page2,
                        row_periodo,
                        representado_value,
                        representado_nombre or representado_value,
                        output_dir,
                        debug
                    )
                    
                    periodos_data[row_periodo] = {
                        "ddjj_pdf": str(ddjj_pdf_path) if ddjj_pdf_path else None,
                        "ddjj_excel": str(ddjj_excel_path) if ddjj_excel_path else None,
                        "vep_pdf": str(vep_pdf_path) if vep_pdf_path else None,
                    }
                    await page2.close()

            results["periodos"] = periodos_data
            
        except Exception as e:
            debug.log(f"Error general: {e}")
            error_logs.append(
                "Error general: "
                + safe_public_message(
                    e,
                    fallback=str(e),
                    context="consulta",
                    stage="consulta",
                )
            )
            return results, error_logs, False
        finally:
            await context.close()
            await browser.close()

    return results, error_logs, True

async def main():
    # Test local
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    
    cuit = os.getenv("DECLARACION_CUIT_REPRESENTANTE", "")
    cuit_representado = os.getenv("DECLARACION_CUIT_REPRESENTADO", "")
    clave = os.getenv("DECLARACION_CLAVE_REPRESENTANTE", "")
    desde = os.getenv("DECLARACION_DESDE", "")
    hasta = os.getenv("DECLARACION_HASTA", "")
    headless = os.getenv("HEADLESS", "True").lower() in ("true", "1", "yes")
    
    if cuit and clave:
        res = await bot_declaracion_en_linea(
            cuit, 
            clave, 
            cuit_representado=cuit_representado or cuit, 
            periodo_desde=desde, 
            periodo_hasta=hasta,
            headless=headless
        )
        print(res)

if __name__ == "__main__":
    asyncio.run(main())
