import asyncio
import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin

import pandas as pd
from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeoutError

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

# Import helpers from common version
from liquidacion_granos.liquidacion_granos_bot import (
    ComprobanteRecord,
    CertificadoDescargaRecord,
    normalize_text,
    safe_filename,
    only_digits,
    extract_download_key,
    parse_table_records,
    records_to_dataframe,
    parse_certificados_descarga_records,
    ensure_unique_path,
    click_by_role_name,
    fill_fechas,
    download_from_link,
    download_comprobante_direct,
    download_certificado_direct,
    seleccionar_denominacion,
    volver_menu_principal,
    try_volver_menu_principal,
    click_menu_option_flexible,
    preparar_menu_para_certificados,
    fill_fechas_certificados_deposito,
    obtener_tipos_consulta_certificados,
    comprobante_label,
    LOGIN_URL,
    SERVICE_NAME,
    SERVICE_NAME_PATTERN,
    BASE_DOWNLOAD_LINK_SELECTOR,
    BASE_BS4_DOWNLOAD_SELECTORS,
)

def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "liquidacion_granos") -> None:
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

async def capture_table_and_save_logger(
    page: Page,
    tipo: str,
    consulta: str,
    download_dir: Path,
    bs4_download_selectors: Sequence[str],
    debug: DebugCapture,
) -> Tuple[List[ComprobanteRecord], Path]:
    await debug.capture(page, f"tabla_{tipo}_{consulta}")
    html_content = await page.content()
    headers, records = parse_table_records(
        html_content,
        bs4_download_selectors=bs4_download_selectors,
    )
    debug.log(f"Tabla {tipo} {consulta}: {len(records)} registros encontrados")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_base = safe_filename(f"tabla_{tipo}_{consulta}_{stamp}")

    download_dir.mkdir(parents=True, exist_ok=True)
    if records:
        output_path = download_dir / f"{output_base}.xlsx"
        df = records_to_dataframe(headers, records)
        df.to_excel(output_path, index=False)
    else:
        output_path = download_dir / f"{output_base}.txt"
        output_path.write_text("Sin registros de comprobantes", encoding="utf-8")
    return records, output_path

async def descargar_comprobantes_logger(
    page: Page,
    tipo: str,
    consulta: str,
    cuit_representante: str,
    download_dir: Path,
    debug: DebugCapture,
    download_link_selector: str = BASE_DOWNLOAD_LINK_SELECTOR,
    bs4_download_selectors: Sequence[str] = BASE_BS4_DOWNLOAD_SELECTORS,
) -> List[dict]:
    records, tabla_path = await capture_table_and_save_logger(
        page,
        tipo,
        consulta,
        download_dir,
        bs4_download_selectors,
        debug,
    )
    records_by_key = {record.download_key: record for record in records if record.download_key}
    
    # Reload rows from page to interact
    rows = page.locator("tr").filter(has=page.locator(download_link_selector))
    row_count = await rows.count()
    debug.log(f"Filas con descarga: {row_count}")

    descargados: List[dict] = []
    descargados.append(
        {
            "categoria": "tabla",
            "tipo": tipo,
            "consulta": consulta,
            "archivo": tabla_path.name,
            "local_path": str(tabla_path),
            "cuit_vendedor": None,
            "coe": None,
        }
    )

    if row_count == 0:
        return descargados

    processed_keys: set[str] = set()

    for idx in range(row_count):
        row = rows.nth(idx)
        if await row.locator("table").count() > 0:
            continue

        link = row.locator(download_link_selector).first
        onclick = ""
        href = ""
        try:
            onclick = (await link.get_attribute("onclick")) or ""
            href = (await link.get_attribute("href")) or ""
        except Exception:
            pass

        key = extract_download_key(onclick or href)
        if key and key in processed_keys:
            continue
        if key:
            processed_keys.add(key)

        record = records_by_key.get(key)
        # Fallback identification logic if record not found in table parse (should match though)
        cuit_vendedor = "SIN_CUIT"
        coe = "SIN_COE"
        if record:
            cuit_vendedor = only_digits(record.cuit_vendedor) or "SIN_CUIT_VENDEDOR"
            coe = only_digits(record.coe) or "SIN_COE"
        
        comprobante_tipo = comprobante_label(consulta)
        file_name = safe_filename(
            f"{tipo} - {comprobante_tipo} - {cuit_representante} - {cuit_vendedor} - {coe}.pdf"
        )
        if not file_name.lower().endswith(".pdf"):
            file_name += ".pdf"

        target = download_dir / file_name
        saved_path = None
        raw_for_direct = onclick or href
        
        # Try direct download first (faster, reliable)
        if raw_for_direct:
            try:
                saved_path = await download_comprobante_direct(
                    page,
                    raw_for_direct,
                    target,
                    label=f"{tipo}_{consulta}_{idx + 1}_{key}_direct",
                )
                if saved_path: debug.log(f"Descarga directa OK: {saved_path.name}")
            except Exception as e:
                debug.log(f"Descarga directa fallo: {e}")

        # Fallback to click
        if saved_path is None:
            try:
                saved_path = await download_from_link(
                    page,
                    link,
                    target,
                    label=f"{tipo}_{consulta}_{idx + 1}_{key}_fallback_click",
                )
                if saved_path: debug.log(f"Descarga click OK: {saved_path.name}")
            except Exception as e:
                debug.log(f"Descarga click fallo: {e}")

        if saved_path is None:
            debug.log(f"Fallo descarga {key}")
            continue

        descargados.append(
            {
                "categoria": "comprobante",
                "tipo": tipo,
                "consulta": consulta,
                "archivo": saved_path.name,
                "local_path": str(saved_path),
                "cuit_vendedor": cuit_vendedor,
                "coe": coe,
            }
        )
    return descargados

async def descargar_certificados_deposito_logger(
    page: Page,
    cuit_representante: str,
    tipo_consulta: str,
    download_dir: Path,
    debug: DebugCapture,
) -> List[dict]:
    # Capture table first
    await capture_table_and_save_logger(
        page,
        "CD",
        f"deposito_{tipo_consulta}",
        download_dir,
        ("a.imprimir[data-tipocertificado][data-liqcodigo][data-coe]",),
        debug,
    )
    
    html_content = await page.content()
    certificados = parse_certificados_descarga_records(html_content)
    debug.log(f"Certificados deposito encontrados: {len(certificados)}")
    
    descargados = []
    
    for idx, certificado in enumerate(certificados, start=1):
        file_name = safe_filename(
            f"CD - {tipo_consulta} - deposito - {cuit_representante} - SIN_CUIT_VENDEDOR - {certificado.coe}.pdf"
        )
        target = download_dir / file_name
        label = (
            f"CD_{tipo_consulta}_direct_{idx}_{certificado.tipo_certificado}_{certificado.cee_codigo}_{certificado.coe}"
        )
        
        saved_path = await download_certificado_direct(
            page,
            certificado,
            target,
            label=label,
        )
        
        if saved_path is None:
            # Fallback click
             fallback_link = page.locator(
                "a.imprimir"
                f"[data-tipocertificado='{certificado.tipo_certificado}']"
                f"[data-liqcodigo='{certificado.cee_codigo}']"
                f"[data-coe='{certificado.coe}']"
            )
             if await fallback_link.count() > 0:
                saved_path = await download_from_link(
                    page,
                    fallback_link.first,
                    target,
                    label=f"{label}_fallback_click",
                )
        
        if saved_path:
            debug.log(f"Descargado certificado: {saved_path.name}")
            descargados.append(
                {
                    "categoria": "certificado_deposito",
                    "tipo": "CD",
                    "consulta": tipo_consulta,
                    "archivo": saved_path.name,
                    "local_path": str(saved_path),
                    "cuit_vendedor": "SIN_CUIT_VENDEDOR",
                    "coe": certificado.coe,
                }
            )
        else:
            debug.log(f"Fallo certificado {certificado.coe}")

    return descargados

async def procesar_lpg_emitidas_logger(
    page: Page,
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
    debug: DebugCapture,
) -> List[dict]:
    debug.log("Procesando LPG Emitidas")
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Liquidaci[oó]n\s+Primaria\s+de\s+Granos", re.I),
        "lpg_menu",
    )
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Consulta\s+de\s+Liquidaciones", re.I),
        "lpg_consulta_emitidas",
    )
    await fill_fechas(page, desde, hasta, "lpg_emitidas")
    return await descargar_comprobantes_logger(page, "LPG", "emitidas", cuit_representante, download_dir, debug)

async def procesar_lpg_recibidas_logger(
    page: Page,
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
    debug: DebugCapture,
) -> List[dict]:
    debug.log("Procesando LPG Recibidas")
    await volver_menu_principal(page, "menu_principal_lpg_1")
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Liquidaci[oó]n\s+Primaria\s+de\s+Granos", re.I),
        "lpg_menu_recibidas",
    )
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Consulta\s+Liquidaciones\s+Recibidas", re.I),
        "lpg_consulta_recibidas",
    )
    await fill_fechas(page, desde, hasta, "lpg_recibidas")
    return await descargar_comprobantes_logger(page, "LPG", "recibidas", cuit_representante, download_dir, debug)


async def procesar_lsg_emitidas_logger(
    page: Page,
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
    debug: DebugCapture,
) -> List[dict]:
    debug.log("Procesando LSG Emitidas")
    await volver_menu_principal(page, "menu_principal_lsg_1")
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Liquidaci[oó]n\s+Secundaria\s+de", re.I),
        "lsg_menu_emitidas",
    )
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Consulta\s+de\s+Liquidaciones\s+Emitidas", re.I),
        "lsg_consulta_emitidas",
    )
    await fill_fechas(page, desde, hasta, "lsg_emitidas")
    return await descargar_comprobantes_logger(page, "LSG", "emitidas", cuit_representante, download_dir, debug)

async def procesar_lsg_recibidas_logger(
    page: Page,
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
    debug: DebugCapture,
) -> List[dict]:
    debug.log("Procesando LSG Recibidas")
    await volver_menu_principal(page, "menu_principal_lsg_2")
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Liquidaci[oó]n\s+Secundaria\s+de", re.I),
        "lsg_menu_recibidas",
    )
    await click_by_role_name(
        page,
        "button",
        re.compile(r"Consulta\s+de\s+Liquidaciones\s+Recibidas", re.I),
        "lsg_consulta_recibidas",
    )
    await fill_fechas(page, desde, hasta, "lsg_recibidas")
    return await descargar_comprobantes_logger(page, "LSG", "recibidas", cuit_representante, download_dir, debug)

async def procesar_certificados_deposito_logger(
    page: Page,
    cuit_representante: str,
    desde: str,
    hasta: str,
    download_dir: Path,
    debug: DebugCapture,
) -> List[dict]:
    debug.log("Procesando Certificados Deposito")
    await preparar_menu_para_certificados(page)
    await click_menu_option_flexible(
        page,
        re.compile(r"Certificaci[oó]n\s+Electr[oó]nica\s+de", re.I),
        "certificacion_electronica_menu",
    )
    await click_menu_option_flexible(
        page,
        re.compile(r"Consulta\s+de\s+Certificados\s+de", re.I),
        "consulta_certificados_menu",
    )

    tipos_consulta = await obtener_tipos_consulta_certificados(page)
    descargados: List[dict] = []
    for tipo_valor, tipo_texto in tipos_consulta:
        debug.log(f"Certificados Tipo: {tipo_texto}")
        await fill_fechas_certificados_deposito(page, desde, hasta, tipo_valor)
        descargados.extend(
            await descargar_certificados_deposito_logger(
                page=page,
                cuit_representante=cuit_representante,
                tipo_consulta=tipo_valor,
                download_dir=download_dir,
                debug=debug,
            )
        )
    return descargados

async def liquidacion_granos(
    playwright: Sequence[object], # Unused, but kept for signature compatibility if needed
    cuit_representante: str,
    clave: str,
    denominacion: str,
    desde: str,
    hasta: str,
    headless: bool,
    download_dir: Path,
    error_logs: List[str],
    proxy: Optional[dict] = None,
) -> List[dict]:
    # We will ignore 'playwright' arg here and do our own context with har
    # But wait, main wrapper calls this.
    # To easily integrate with HAR, we should use the playwright instance passed or created in wrapper.
    # The wrapper below creates it.
    pass 

async def bot_liquidacion_granos(
    cuit_representante: str,
    clave: str,
    denominacion: str,
    desde: str,
    hasta: str,
    cuit_representado: Optional[str] = None,
    proxy_request: bool = False,
    headless: bool = True,
    **kwargs
) -> Tuple[List[dict], List[str], bool]:
    error_logs: List[str] = []
    results: List[dict] = []
    
    debug = DebugCapture(prefix="liquidacion_granos")
    debug.log("Iniciando bot Liquidacion Granos Logger")
    
    download_root = debug.descargas_dir
    proxy = get_proxy_config() if proxy_request else None
    
    async with async_playwright() as pw:
        har_path = debug.har_dir / "liquidacion_granos.har"
        browser = await pw.chromium.launch(headless=headless, proxy=proxy)
        context = await browser.new_context(accept_downloads=True, record_har_path=har_path, record_har_content="embed")
        page = await context.new_page()
        await debug.capture(page, "inicio")
        
        try:
            # Login
            login_result = await login_arca(
                page,
                cuit_representante,
                clave,
                error_logs=error_logs,
                login_url=LOGIN_URL,
            )
            await debug.capture(page, "login_completo")
            if not login_result.success:
                debug.log(f"Login fallo: {error_logs}")
                return results, error_logs, False

            # Open Service
            service_page = await open_arca_service(
                page,
                service_name=SERVICE_NAME,
                service_matchers=(SERVICE_NAME_PATTERN,),
            )
            if not service_page:
                debug.log("No se pudo abrir servicio")
                return results, error_logs, False
            await debug.capture(service_page, "servicio_abierto")
            
            # Select Persona
            try:
                await seleccionar_denominacion(service_page, denominacion)
                await debug.capture(service_page, "denominacion_seleccionada")
            except Exception as e:
                debug.log(f"Error seleccion denominacion: {e}")
                error_logs.append(str(e))
                return results, error_logs, False
                
            resultados: List[dict] = []
            
            try:
                resultados.extend(await procesar_lpg_emitidas_logger(service_page, cuit_representante, desde, hasta, download_root, debug))
            except Exception as e:
                debug.log(f"Error LPG Emitidas: {e}")
            
            try:
                resultados.extend(await procesar_lpg_recibidas_logger(service_page, cuit_representante, desde, hasta, download_root, debug))
            except Exception as e:
                debug.log(f"Error LPG Recibidas: {e}")
            
            try:
                resultados.extend(await procesar_lsg_emitidas_logger(service_page, cuit_representante, desde, hasta, download_root, debug))
            except Exception as e:
                debug.log(f"Error LSG Emitidas: {e}")
                
            try:
                resultados.extend(await procesar_lsg_recibidas_logger(service_page, cuit_representante, desde, hasta, download_root, debug))
            except Exception as e:
                debug.log(f"Error LSG Recibidas: {e}")
                
            try:
                resultados.extend(await procesar_certificados_deposito_logger(service_page, cuit_representante, desde, hasta, download_root, debug))
            except Exception as e:
                debug.log(f"Error Certificados: {e}")
            
            await try_volver_menu_principal(service_page, "menu_principal_final")
            
            return resultados, error_logs, True

        except Exception as e:
            debug.log(f"Error general: {e}")
            error_logs.append(str(e))
            return [], error_logs, False
        finally:
            await context.close()
            await browser.close()

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    cuit = os.getenv("LIQUIDACION_GRANOS_CUIT_REPRESENTANTE", "")
    clave = os.getenv("LIQUIDACION_GRANOS_CLAVE", "")
    denominacion = os.getenv("LIQUIDACION_GRANOS_DENOMINACION", "MI EMPRESA")
    desde = os.getenv("LIQUIDACION_GRANOS_DESDE", "01/01/2024")
    hasta = os.getenv("LIQUIDACION_GRANOS_HASTA", "31/01/2024")
    headless_env = os.getenv("LIQUIDACION_GRANOS_HEADLESS", "true").lower() == "true"
    if cuit and clave:
        res = await bot_liquidacion_granos(
            cuit, clave, denominacion, desde, hasta, headless=headless_env
        )
        print(res)

if __name__ == "__main__":
    asyncio.run(main())
