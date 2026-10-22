import asyncio
import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from playwright.async_api import async_playwright, Page, TimeoutError

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

# Import helpers from common version
from mis_facilidades.mis_facilidades_bot import (
    extract_fecha_consolidacion_from_detail,
    parse_fecha,
    normalize_text,
    normalize_cuit,
    extract_cuit_from_text,
    extract_cuit_from_html,
    safe_filename,
    inject_base_tag,
    expand_cuotas_for_export,
    expand_obligaciones_for_export,
    try_select_cuit_option,
    get_available_cuits,
    select_cuit_if_available,
    verify_contribuyente_cuit,
    extract_plan_entries,
    extract_plan_num_from_detail,
    build_report_path,
    save_tables_to_excel,
    save_pdf_from_page,
    collect_table,
    open_plan_detail,
    scrape_optional_obligaciones,
    find_deuda_table_selector,
    navigate_back_to_plan_list,
    verify_on_plan_detail_page,
    verify_on_plan_list_page,
    ensure_on_plan_detail,
    wait_loaded,
    DOWNLOAD_DIR,
)

def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "mis_facilidades") -> None:
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

async def bot_mis_facilidades_logger(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: Optional[str] = None,
    denominacion: Optional[str] = None,
    situacion_excluyente: Optional[List[str]] = None,
    fecha_consolidacion_desde: Optional[str] = None,
    fecha_consolidacion_hasta: Optional[str] = None,
    proxy_request: bool = False,
) -> tuple[dict[str, Any], list[str], bool]:
    results: dict[str, Any] = {"archivos": []}
    error_logs: list[str] = []
    success = True
    fuera_de_rango = 0
    fecha_desde = parse_fecha(fecha_consolidacion_desde)
    fecha_hasta = parse_fecha(fecha_consolidacion_hasta)
    if (fecha_consolidacion_desde and not fecha_desde) or (fecha_consolidacion_hasta and not fecha_hasta):
        return results, ["Las fechas de consolidación deben tener formato dd/mm/aaaa"], False
    if fecha_desde and fecha_hasta and fecha_desde > fecha_hasta:
        return results, ["fecha_consolidacion_desde no puede ser posterior a fecha_consolidacion_hasta"], False
    found_plan: Optional[bool] = None

    debug = DebugCapture(prefix="mis_facilidades")
    debug.log("Iniciando bot Mis Facilidades Logger")

    proxy = get_proxy_config() if proxy_request else None

    # Use debug directory for downloads
    base_dir = debug.descargas_dir

    har_path = debug.har_dir / "mis_facilidades.har"

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(record_har_path=har_path, record_har_content="embed")
            page = await context.new_page()

            await debug.capture(page, "inicio")

            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
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
                service_name="MIS FACILIDADES",
                service_matchers=(re.compile("MIS FACILIDADES", re.I),),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
                service_unavailable_message="No se pudo abrir Mis Facilidades",
            )
            if page1 is None:
                debug.log("No se pudo abrir servicio")
                await context.close()
                await browser.close()
                return results, error_logs, False
            await page1.wait_for_load_state("domcontentloaded")
            await debug.capture(page1, "servicio_abierto")

            available_cuits = await get_available_cuits(page1)
            if not available_cuits:
                page_cuit = extract_cuit_from_html(await page1.content())
                if page_cuit:
                    available_cuits = [page_cuit]
                else:
                    available_cuits = [cuit_representante] if cuit_representante else []
            results["cuits_disponibles"] = available_cuits

            try:
                await select_cuit_if_available(page1, cuit_representado)
                await debug.capture(page1, "cuit_seleccionado")
            except Exception as exc:
                error_logs.append(f"No se pudo seleccionar el representado: {exc}")
                await page1.close()
                await context.close()
                await browser.close()
                return results, error_logs, False

            expected_cuit = cuit_representado or cuit_representante
            actual_cuit, cuit_ok = await verify_contribuyente_cuit(page1, expected_cuit)
            if not cuit_ok and actual_cuit is None and available_cuits:
                if len(available_cuits) == 1:
                    actual_cuit = available_cuits[0]
                    if normalize_cuit(actual_cuit) == normalize_cuit(expected_cuit):
                        cuit_ok = True
            if not cuit_ok:
                error_logs.append(
                    "CUIT seleccionado invalido o distinto al esperado"
                    f" (esperado: {expected_cuit}, actual: {actual_cuit})."
                )
                await page1.close()
                await context.close()
                await browser.close()
                return results, error_logs, False

            cuit_objetivo = actual_cuit or expected_cuit or ""
            if not cuit_objetivo:
                error_logs.append("No se pudo determinar el CUIT del contribuyente.")
                await page1.close()
                await context.close()
                await browser.close()
                return results, error_logs, False

            safe_representado = safe_filename(cuit_objetivo)
            fin_cuit = (cuit_objetivo or "")[-1:]
            plan_entries = extract_plan_entries(await page1.content())
            debug.log(f"Planes encontrados: {len(plan_entries)}")
            
            found_plan = False
            for entry in plan_entries:
                plan_num = entry.get("plan_num") or "plan"
                situacion = entry.get("situacion") or ""
                detalle_id = entry.get("detalle_id")

                if "Plan Cancelado" in situacion:
                    continue
                if situacion_excluyente and any(excl.lower() in situacion.lower() for excl in situacion_excluyente):
                    continue
                found_plan = True
                if not detalle_id:
                    continue

                plan_denominacion = denominacion or entry.get("denominacion") or "denominacion"
                plan_result = {
                    "plan_num": plan_num,
                    "denominacion": plan_denominacion,
                }
                debug.log(f"Procesando plan {plan_num}")
                
                # Create subfolder for plan
                plan_dir = base_dir / safe_filename(plan_num)
                plan_dir.mkdir(parents=True, exist_ok=True)

                try:
                    opened = await open_plan_detail(page1, detalle_id)
                    if not opened:
                        raise RuntimeError("No se pudo abrir el detalle del plan")
                    await debug.capture(page1, f"detalle_plan_{plan_num}")
                except Exception as exc:
                    error_logs.append(f"Error al abrir detalle del plan {plan_num}: {exc}")
                    success = False
                    continue

                if fecha_desde or fecha_hasta:
                    fecha_consolidacion = extract_fecha_consolidacion_from_detail(await page1.content())
                    if fecha_consolidacion is None:
                        error_logs.append(f"No se pudo leer la fecha de consolidación del plan {plan_num}")
                        success = False
                    if fecha_consolidacion and (fecha_desde and fecha_consolidacion < fecha_desde or fecha_hasta and fecha_consolidacion > fecha_hasta):
                        fuera_de_rango += 1
                    if fecha_consolidacion is None or (fecha_desde and fecha_consolidacion < fecha_desde) or (
                        fecha_hasta and fecha_consolidacion > fecha_hasta
                    ):
                        debug.log(f"Plan {plan_num} fuera del rango de consolidación")
                        plan_dir.rmdir()
                        if not await navigate_back_to_plan_list(page1):
                            error_logs.append("No se pudo volver al listado de planes")
                            success = False
                            break
                        continue

                detail_plan_num = extract_plan_num_from_detail(await page1.content())
                if detail_plan_num:
                    plan_num = detail_plan_num
                    plan_result["plan_num"] = plan_num
                    # Update plan dir if plan num changed
                    new_plan_dir = base_dir / safe_filename(plan_num)
                    new_plan_dir.mkdir(parents=True, exist_ok=True)
                    plan_dir = new_plan_dir

                pagos_headers: List[str] = []
                pagos_rows: List[List[str]] = []
                cuotas_headers: List[str] = []
                cuotas_rows: List[List[str]] = []
                obligaciones_tables: List[Tuple[str, List[str], List[List[str]]]] = []

                try:
                    if not await ensure_on_plan_detail(page1, detalle_id, logger=debug):
                        debug.log(f"No se pudo asegurar pagina de detalle antes de Ver Pagos (plan {plan_num})")
                        raise RuntimeError("No se pudo asegurar pagina de detalle")
                    await page1.get_by_role("button", name="Ver Pagos").click()
                    await wait_loaded(page1)
                    await debug.capture(page1, f"ver_pagos_{plan_num}")
                    
                    pagos_headers, pagos_rows = await collect_table(page1, "#tableDetallePagos")
                    pagos_pdf = build_report_path(
                        fin_cuit,
                        cuit_objetivo,
                        "pagos",
                        plan_num,
                        plan_denominacion,
                        ".pdf",
                        base_dir=plan_dir,
                    )
                    await save_pdf_from_page(page1, pagos_pdf, playwright, context)
                    plan_result["pagos_pdf"] = pagos_pdf.name
                    await page1.get_by_role("link", name="Volver").click()
                    await wait_loaded(page1)
                except Exception as exc:
                    error_logs.append(f"Error al obtener pagos del plan {plan_num}: {exc}")
                    success = False

                try:
                    if not await ensure_on_plan_detail(page1, detalle_id, logger=debug):
                        debug.log(f"No se pudo asegurar pagina de detalle antes de Plan de Pago (plan {plan_num})")
                        raise RuntimeError("No se pudo asegurar pagina de detalle")
                    await page1.get_by_role("button", name="Plan de Pago").click()
                    await wait_loaded(page1)
                    await debug.capture(page1, f"ver_cuotas_{plan_num}")
                    
                    cuotas_headers, cuotas_rows = await collect_table(page1, "#tableDetallePlanPago")
                    cuotas_pdf = build_report_path(
                        fin_cuit,
                        cuit_objetivo,
                        "cuotas",
                        plan_num,
                        plan_denominacion,
                        ".pdf",
                        base_dir=plan_dir,
                    )
                    await save_pdf_from_page(page1, cuotas_pdf, playwright, context, label="cuotas")
                    plan_result["cuotas_pdf"] = cuotas_pdf.name
                    await page1.get_by_role("link", name="Volver").click()
                    await wait_loaded(page1)
                except Exception as exc:
                    error_logs.append(f"Error al obtener cuotas del plan {plan_num}: {exc}")
                    success = False

                try:
                    if not await ensure_on_plan_detail(page1, detalle_id, navigate_back=False, logger=debug):
                        debug.log(f"No se pudo asegurar pagina de detalle antes de Obligaciones (plan {plan_num})")
                    oblig_impositivas = await scrape_optional_obligaciones(
                        page1,
                        playwright,
                        context,
                        button_name="Obligaciones Impositivas",
                        sheet_name="Obligaciones Impositivas",
                        file_label="obligaciones",
                        table_selectors=["#tableDetalleDeuda_2", "#tableDetalleDeuda_1"],
                        fin_cuit=fin_cuit,
                        cuit_representado=cuit_objetivo,
                        plan_num=plan_num,
                        denominacion_base=plan_denominacion,
                        base_dir=plan_dir,
                    )
                    if oblig_impositivas:
                        table_data, oblig_pdf = oblig_impositivas
                        obligaciones_tables.append(table_data)
                        plan_result["obligaciones_pdf"] = oblig_pdf.name
                        debug.log(f"Obligaciones impositivas capturadas: {oblig_pdf.name}")
                except Exception as exc:
                    error_logs.append(
                        f"Error al obtener obligaciones impositivas del plan {plan_num}: {exc}"
                    )
                    success = False

                try:
                    if not await ensure_on_plan_detail(page1, detalle_id, navigate_back=False, logger=debug):
                        debug.log(f"No se pudo asegurar pagina de detalle antes de Obligaciones Previsionales (plan {plan_num})")
                    oblig_previsionales = await scrape_optional_obligaciones(
                        page1,
                        playwright,
                        context,
                        button_name="Obligaciones Previsionales",
                        sheet_name="Obligaciones Previsionales",
                        file_label="obligaciones_previsionales",
                        table_selectors=["#tableDetalleDeuda_1", "#tableDetalleDeuda_2"],
                        fin_cuit=fin_cuit,
                        cuit_representado=cuit_objetivo,
                        plan_num=plan_num,
                        denominacion_base=plan_denominacion,
                        base_dir=plan_dir,
                    )
                    if oblig_previsionales:
                        table_data, oblig_pdf = oblig_previsionales
                        obligaciones_tables.append(table_data)
                        plan_result["obligaciones_previsionales_pdf"] = oblig_pdf.name
                        debug.log(f"Obligaciones previsionales capturadas: {oblig_pdf.name}")
                except Exception as exc:
                    error_logs.append(
                        f"Error al obtener obligaciones previsionales del plan {plan_num}: {exc}"
                    )
                    success = False

                try:
                    excel_path = build_report_path(
                        fin_cuit,
                        cuit_objetivo,
                        "tablas",
                        plan_num,
                        plan_denominacion,
                        ".xlsx",
                        base_dir=plan_dir,
                    )
                    save_tables_to_excel(
                        excel_path,
                        [
                            ("Pagos", pagos_headers, pagos_rows),
                            ("Cuotas", cuotas_headers, cuotas_rows),
                            *obligaciones_tables,
                        ],
                    )
                    plan_result["tablas_excel"] = excel_path.name
                except Exception as exc:
                    error_logs.append(f"Error al generar tablas del plan {plan_num}: {exc}")
                    success = False

                if not await navigate_back_to_plan_list(page1):
                    error_logs.append("No se pudo volver al listado de planes")
                    success = False
                    results["archivos"].append(plan_result)
                    continue

                results["archivos"].append(plan_result)

            if not found_plan:
                error_logs.append("No se encontraron planes disponibles")

            await context.close()
            await browser.close()
    except Exception as exc:
        error_logs.append(f"Error en bot mis facilidades: {exc}")
        success = False

    if not results["archivos"] and found_plan is not False and not fuera_de_rango:
        success = False

    return results, error_logs, success

async def main():
    try: from dotenv import load_dotenv; load_dotenv()
    except: pass
    cuit = os.getenv("MIS_FACILIDADES_CUIT_LOGIN", "")
    clave = os.getenv("MIS_FACILIDADES_CLAVE", "")
    cuit_representado = os.getenv("MIS_FACILIDADES_CUIT_REPRESENTADO", "")
    situacion_excluyente = os.getenv("MIS_FACILIDADES_SITUACION_EXCLUYENTE", "")
    fecha_desde = os.getenv("MIS_FACILIDADES_FECHA_CONSOLIDACION_DESDE", "") or None
    fecha_hasta = os.getenv("MIS_FACILIDADES_FECHA_CONSOLIDACION_HASTA", "") or None
    if situacion_excluyente:
        situacion_excluyente = [s.strip() for s in situacion_excluyente.split(",") if s.strip()]
    else:
        situacion_excluyente = None
    
    if cuit and clave:
        res = await bot_mis_facilidades_logger(
            cuit, clave, cuit_representado=cuit_representado, denominacion="MI EMPRESA", situacion_excluyente=situacion_excluyente,
            fecha_consolidacion_desde=fecha_desde, fecha_consolidacion_hasta=fecha_hasta, proxy_request=False
        )
        print(res)

if __name__ == "__main__":
    asyncio.run(main())
