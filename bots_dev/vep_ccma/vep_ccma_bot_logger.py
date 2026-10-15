import asyncio
import base64
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

from playwright.async_api import (
    async_playwright,
    Download,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

BOT_NAME = "vep_ccma"

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

# ----------------------------------------------------------------------
# Helpers VEP (tomados de vep_archivo_bot_logger)
# ----------------------------------------------------------------------

async def _download_to_bytes(download: Download) -> Tuple[bytes, str]:
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

# ----------------------------------------------------------------------
# Helper: extraer columnas de la tabla volante usando headers
# ----------------------------------------------------------------------

async def _scrape_volante_table(page: Page) -> List[Dict[str, str]]:
    resultado = await page.evaluate(
        """
        () => {
            const cleanText = (value) => (value || "").replace(/\\u00a0/g, " ").trim();

            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            const rows = [];

            checkboxes.forEach((cb, index) => {
                const row = cb.closest('tr');
                if (!row) return;

                const cells = Array.from(row.querySelectorAll('td'));
                if (cells.length < 6) return;

                const cellTexts = cells.map(c => cleanText(c.textContent));

                const periodo = cellTexts[0] || '';
                if (periodo.length > 10 || !periodo) return;
                if (/[{<>}]/.test(cellTexts.join(''))) return;

                const cbName = (cb.name || '').toLowerCase();
                const tipo = cbName.includes('interes') ? 'interes' : 'capital';

                const parseImporte = (val) => {
                    if (!val) return 0;
                    const cleaned = val.replace(/,/g, '');
                    const num = parseFloat(cleaned);
                    return isNaN(num) ? 0 : num;
                };

                const esInteres = tipo === 'interes';
                const catStr = esInteres ? '' : (cellTexts[4] || '');
                const impStr = esInteres ? (cellTexts[4] || '') : (cellTexts[5] || '');

                rows.push({
                    checkbox_index: index,
                    checkbox_name: cb.name || '',
                    checkbox_value: cb.value || '',
                    tipo: tipo,
                    periodo: periodo,
                    impuesto: cellTexts[1] || '',
                    concepto: cellTexts[2] || '',
                    subconcepto: cellTexts[3] || '',
                    categoria: esInteres ? '' : catStr,
                    importe: parseImporte(impStr),
                });
            });

            return rows;
        }
        """,
    )

    if not isinstance(resultado, list):
        return []
    return resultado


async def _check_volante_rows(page: Page, indices: List[int]) -> None:
    await page.evaluate(
        """
        ({ indices }) => {
            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            indices.forEach(idx => {
                if (idx >= 0 && idx < checkboxes.length) {
                    const cb = checkboxes[idx];
                    if (!cb.checked) {
                        cb.checked = true;
                        // Disparar eventos para que el sistema detecte el cambio
                        cb.dispatchEvent(new Event('change', { bubbles: true }));
                        cb.dispatchEvent(new Event('click', { bubbles: true }));
                    }
                }
            });
        }
        """,
        {"indices": indices},
    )

async def _uncheck_all_volante_rows(page: Page) -> None:
    await page.evaluate(
        """
        () => {
            const checkboxes = document.querySelectorAll('input[type="checkbox"]');
            checkboxes.forEach(cb => {
                cb.checked = false;
                cb.dispatchEvent(new Event('change', { bubbles: true }));
                cb.dispatchEvent(new Event('click', { bubbles: true }));
            });
        }
        """,
    )

# ----------------------------------------------------------------------
# Helpers de filtro
# ----------------------------------------------------------------------

def _row_matches_filtro(row: Dict, filtro_entry: Dict, field_map: Dict[str, str]) -> bool:
    for filter_key, filter_value in filtro_entry.items():
        row_key = field_map.get(filter_key, filter_key)
        row_value = str(row.get(row_key, "")).strip()
        if isinstance(filter_value, list):
            str_values = [str(v).strip() for v in filter_value if v is not None]
            if not str_values:
                continue
            if row_value not in str_values:
                return False
        else:
            str_val = str(filter_value).strip() if filter_value is not None else ""
            if str_val and row_value != str_val:
                return False
    return True

FIELD_MAP_IMPUESTOS = {
    "periodo": "periodo", "impuesto": "impuesto",
    "concepto": "concepto", "subconcepto": "subconcepto",
    "categoria": "categoria",
}
FIELD_MAP_INTERESES = {
    "periodo": "periodo", "impuesto": "impuesto",
    "concepto": "concepto", "subcpto": "subconcepto",
}

# ----------------------------------------------------------------------
# Main bot function
# ----------------------------------------------------------------------

async def bot_vep_ccma(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    medio_pago: str = "internet_banking",
    filtro_impuestos: Optional[List[Dict[str, Any]]] = None,
    filtro_intereses: Optional[List[Dict[str, Any]]] = None,
    seleccionar_impuestos: bool = True,
    seleccionar_intereses: bool = True,
    b64_PDF_data: bool = False,
    proxy_request: bool = False,
    generar_volante: bool = True,
) -> Tuple[Dict[str, Any], List[str], bool]:
    """
    Flujo:
      1) Login ARCA
      2) Abrir servicio CCMA
      3) Seleccionar CUIT, cargar periodo, calcular deuda
      4) Click VOLANTE DE PAGO
      5) Scrapear tabla de volante (impuestos e intereses), guardar JSON
      6) Si generar_volante=False, detenerse ahi (solo scraping)
      7) Click GENERAR VEP O QR
      8) Seleccionar medio de pago, descargar PDF/QR
    """
    result: Dict[str, Any] = {}
    error_logs: List[str] = []
    success = True

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot VEP CCMA")

    medio_pagos_habilitados = [
        "link", "pago_mis_cuentas", "internet_banking", "xn_group", "qr",
    ]
    if medio_pago not in medio_pagos_habilitados:
        error_logs.append(f"Medio de pago '{medio_pago}' no esta habilitado. Usar: {', '.join(medio_pagos_habilitados)}")
        return result, error_logs, False

    proxy = get_proxy_config() if proxy_request else None

    try:
        async with async_playwright() as pw:
            har_path = debug.har_dir / f"{BOT_NAME}.har"
            debug.log(f"Configurando HAR en {har_path}")

            browser = await pw.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(
                accept_downloads=True,
                record_har_path=har_path,
                record_har_content="embed",
            )
            page = await context.new_page()
            await debug.capture(page, "inicio")

            # ---- 1) LOGIN ----
            login_result = await login_arca(
                page,
                str(cuit_representante),
                str(clave_representante),
                error_logs=error_logs,
            )
            await debug.capture(page, "login_completo")

            if not login_result.success:
                debug.log(f"Login fallido: {error_logs}")
                await _safe_close(context, browser)
                return result, error_logs, False

            # ---- 2) ABRIR CCMA ----
            page1 = await open_arca_service(
                page,
                service_name="CCMA",
                service_matchers=(
                    re.compile(
                        r"CCMA\s*-\s*CUENTA CORRIENTE DE CONTRIBUYENTES MONOTRIBUTISTAS Y AUTONOMOS",
                        re.I,
                    ),
                ),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo desplegar 'Ver todos' (se intentara abrir el servicio igual)",
                service_unavailable_message="No se encuentra habilitado el servicio de CCMA",
            )
            await debug.capture(page, "servicio_abierto_main_page")

            if page1 is None:
                debug.log("Servicio CCMA no habilitado")
                error_logs.append("No se encuentra habilitado el servicio de CCMA")
                await _safe_close(context, browser)
                return result, error_logs, False

            await debug.capture(page1, "servicio_ccma_page")

            # ---- 3) SELECCIONAR CUIT ----
            try:
                await page1.get_by_role("listbox").select_option(str(cuit_representado), timeout=2_000)
                await page1.get_by_role("button", name=re.compile(r"Elegir CUIT", re.I)).click()
                await debug.capture(page1, "cuit_seleccionado")
            except Exception:
                pass

            # ---- 3b) PERIODO Y CALCULO ----
            try:
                await page1.get_by_role("cell", name="PERÍODO DESDE (MM/AAAA)", exact=True).click()
                campo = page1.locator('input[name="perdesde2"]')
                await campo.click()
                await campo.press("ArrowRight")
                await campo.fill("04/2000")
                await page1.get_by_role("button", name="CALCULO DE DEUDA").click()
                await debug.capture(page1, "calculo_realizado")
            except Exception as e:
                debug.log(f"Error en calculo: {e}")
                error_logs.append(f"No se pudo cargar periodo o ejecutar el calculo: {e}")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            try:
                await page1.locator('select[name="rango"]').select_option("48")
                await page1.wait_for_load_state("networkidle")
                await debug.capture(page1, "rango_seleccionado")
            except Exception:
                pass

            # ---- 4) CLICK VOLANTE DE PAGO ----
            debug.log("Buscando boton VOLANTE DE PAGO")
            await debug.capture(page1, "antes_volante_pago")

            volante_clicked = await _click_button_by_text(page1, "VOLANTE DE PAGO")
            if not volante_clicked:
                error_logs.append("No se encontro el boton VOLANTE DE PAGO")
                debug.log("No se pudo hacer click en VOLANTE DE PAGO")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            debug.log("Click en VOLANTE DE PAGO exitoso")
            await page1.wait_for_timeout(2_000)
            await debug.capture(page1, "despues_volante_pago")

            # ---- 5) SCRAPEAR TABLA DE IMPUESTOS ----
            debug.log("Scrapeando tabla de volante (impuestos)")
            volante_rows = await _scrape_volante_table(page1)
            debug.log(f"Filas encontradas en volante: {len(volante_rows)}")

            if not volante_rows:
                error_logs.append("No se encontraron filas en la tabla del volante de pago")
                debug.log("Tabla volante vacia o no encontrada")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            # Separar capital e intereses
            capital_rows = [r for r in volante_rows if r.get("tipo") == "capital"]
            interes_rows = [r for r in volante_rows if r.get("tipo") == "interes"]

            debug.log(f"Capital: {len(capital_rows)}, Intereses: {len(interes_rows)}")

            # Guardar datos en JSON
            import json
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            capital_json_path = debug.descargas_dir / f"impuestos_{timestamp}.json"
            with open(capital_json_path, "w", encoding="utf-8") as f:
                json.dump(capital_rows, f, ensure_ascii=False, indent=4)
            debug.log(f"Impuestos guardados en {capital_json_path}")

            if interes_rows:
                interes_json_path = debug.descargas_dir / f"intereses_{timestamp}.json"
                with open(interes_json_path, "w", encoding="utf-8") as f:
                    json.dump(interes_rows, f, ensure_ascii=False, indent=4)
                debug.log(f"Intereses guardados en {interes_json_path}")

            result["impuestos"] = capital_rows
            result["intereses"] = interes_rows
            result["total_impuestos"] = len(capital_rows)
            result["total_intereses"] = len(interes_rows)

            # Determinar indices a seleccionar (filtros separados para impuestos e intereses)
            indices_a_chequear: Set[int] = set()

            # Impuestos
            if seleccionar_impuestos:
                if filtro_impuestos:
                    for row in capital_rows:
                        for entry in filtro_impuestos:
                            if _row_matches_filtro(row, entry, FIELD_MAP_IMPUESTOS):
                                indices_a_chequear.add(row.get("checkbox_index", -1))
                                break
                else:
                    for row in capital_rows:
                        indices_a_chequear.add(row.get("checkbox_index", -1))

            # Intereses
            if seleccionar_intereses:
                if filtro_intereses:
                    for row in interes_rows:
                        for entry in filtro_intereses:
                            if _row_matches_filtro(row, entry, FIELD_MAP_INTERESES):
                                indices_a_chequear.add(row.get("checkbox_index", -1))
                                break
                else:
                    for row in interes_rows:
                        indices_a_chequear.add(row.get("checkbox_index", -1))

            indices_a_chequear.discard(-1)
            indices_list = sorted(indices_a_chequear)

            debug.log(
                f"Filtro impuestos: {filtro_impuestos is not None}, "
                f"intereses: {filtro_intereses is not None} | "
                f"Seleccionados: {len(indices_list)} filas"
            )

            if not indices_list:
                debug.log("No hay filas para seleccionar")
                error_logs.append("No hay filas que coincidan con los filtros en el volante de pago")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            # Guardar datos de las filas seleccionadas en el resultado
            all_rows = capital_rows + interes_rows
            volante_data_selected = [r for r in all_rows if r.get("checkbox_index") in indices_a_chequear]
            result["volante_data"] = volante_data_selected
            result["total_seleccionado"] = len(volante_data_selected)
            debug.log(f"Datos de volante guardados: {len(volante_data_selected)} filas")

            # Deseleccionar todo y luego chequear solo las filas filtradas
            await _uncheck_all_volante_rows(page1)
            await _check_volante_rows(page1, indices_list)
            debug.log(f"Chequeadas {len(indices_list)} filas en volante")
            await page1.wait_for_timeout(1_000)
            await debug.capture(page1, "volante_opciones_seleccionadas")

            # ---- 5b) Si generar_volante=False, solo scraping ----
            if not generar_volante:
                debug.log("generar_volante=False: deteniendo antes de GENERAR VEP O QR (solo scraping)")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, True

            # ---- 6) CLICK GENERAR VEP O QR ----
            debug.log("Buscando boton GENERAR VEP O QR")
            generar_clicked = await _click_button_by_text(page1, "GENERAR VEP O QR")
            if not generar_clicked:
                error_logs.append("No se encontro el boton GENERAR VEP O QR")
                debug.log("No se pudo hacer click en GENERAR VEP O QR")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            debug.log("Click en GENERAR VEP O QR exitoso")
            await page1.wait_for_timeout(3_000)
            await debug.capture(page1, "vep_generado")

            # ---- 7) FLUJO VEP (medio pago + descarga) ----
            # Cerrar posibles modales
            for intento in range(3):
                try:
                    await page1.get_by_text("close", exact=True).click(timeout=2_000)
                except Exception:
                    pass
                try:
                    await page1.get_by_role("button", name="Close").click(timeout=2_000)
                except Exception:
                    pass
                try:
                    await page1.get_by_role("button", name="Aceptar").click(timeout=2_000)
                except Exception:
                    pass

            await debug.capture(page1, "post_generar_vep")

            medio_pago_img_ids = {
                "qr": "0",
                "link": "1001",
                "pago_mis_cuentas": "1002",
                "internet_banking": "1003",
                "xn_group": "1005",
            }

            try:
                img_id = medio_pago_img_ids[medio_pago]
                await page1.locator(f'img[id="{img_id}"]').click(timeout=5_000)
                await page1.wait_for_timeout(500)
                try:
                    await page1.get_by_role("button", name="Aceptar").click(timeout=5_000)
                except Exception:
                    pass
                await debug.capture(page1, "medio_pago_seleccionado")
            except Exception as e:
                errMsg = f"Error al seleccionar medio de pago: {e}"
                error_logs.append(errMsg)
                debug.log(errMsg)
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            # Descargar PDF
            download_path = None
            download_name = None
            pdf_data = None

            try:
                async with page1.expect_download() as download_info:
                    await page1.get_by_role("button", name="Descargar VEP").click()
                download = await download_info.value
                download_name = download.suggested_filename or "vep_detalle.pdf"
                download_path = debug.descargas_dir / download_name
                await download.save_as(path=str(download_path))
                with open(download_path, "rb") as f:
                    pdf_data = f.read()
                debug.log(f"PDF descargado: {download_path}")
            except Exception as e:
                debug.log(f"Error en descarga directa: {e}")
                try:
                    async with page1.expect_popup() as page2_info:
                        await page1.get_by_title("Ver detalle").click()
                    page2 = await page2_info.value
                    async with page2.expect_download() as download1_info:
                        await page2.get_by_role("button", name="picture_as_pdf").click()
                    download1 = await download1_info.value
                    pdf_data, download_name = await _download_to_bytes(download1)
                    download_path = debug.descargas_dir / download_name
                    with open(download_path, "wb") as f:
                        f.write(pdf_data)
                    debug.log(f"PDF descargado (metodo 2): {download_path}")
                    await page2.close()
                except Exception as e2:
                    error_logs.append(f"Error al descargar PDF: {e2}")
                    debug.log(f"Error downloading PDF (method 2): {e2}")
                    success = False

            if pdf_data:
                result["NOMBRE_ARCHIVO"] = download_name or "vep_detalle.pdf"
                if download_path:
                    result["RUTA_ARCHIVO"] = str(download_path)
                if b64_PDF_data:
                    result["B64"] = base64.b64encode(pdf_data).decode("utf-8")

            # QR
            if medio_pago == "qr":
                qr_src = await _extract_qr_src(page1)
                qr_bytes = _decode_qr_src(qr_src) if qr_src else None
                if qr_bytes:
                    qr_name = _qr_filename(download_name or "vep_detalle.pdf")
                    qr_path = debug.descargas_dir / qr_name
                    try:
                        with open(qr_path, "wb") as f:
                            f.write(qr_bytes)
                        result["NOMBRE_QR"] = qr_name
                        result["RUTA_QR"] = str(qr_path)
                        debug.log(f"QR guardado: {qr_path}")
                        if b64_PDF_data:
                            result["B64_QR"] = base64.b64encode(qr_bytes).decode("utf-8")
                    except Exception as e:
                        error_logs.append(f"Error al guardar QR: {e}")
                else:
                    error_logs.append("No se pudo obtener el QR del VEP")
                    debug.log("QR no encontrado")

            # ---- FIN ----
            await _safe_close(context, browser, page1=page1, base_page=page)
            debug.log("Bot VEP CCMA finalizado exitosamente")

    except Exception as e:
        error_logs.append(f"Error global en vep_ccma: {e}")
        debug.log(f"Critical error: {e}")
        success = False

    return result, error_logs, success


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

async def _click_button_by_text(page: Page, text: str) -> bool:
    text_upper = text.upper()
    text_lower = text.lower()
    # Escapar caracteres especiales para CSS selectors
    safe_upper = text_upper.replace("'", "\\'").replace('"', '\\"')
    safe_lower = text_lower.replace("'", "\\'").replace('"', '\\"')
    strategies = [
        page.get_by_role("button", name=re.compile(re.escape(text), re.I)),
        page.get_by_role("link", name=re.compile(re.escape(text), re.I)),
        page.get_by_text(text, exact=True),
        page.locator(f"button:has-text('{safe_upper}')"),
        page.locator(f"button:has-text('{safe_lower}')"),
        page.locator(f"a:has-text('{safe_upper}')"),
        page.locator(f"a:has-text('{safe_lower}')"),
        page.locator(f"input[type='button'][value*='{safe_upper}']"),
        page.locator(f"input[type='submit'][value*='{safe_upper}']"),
        page.locator(f"input[value*='{safe_upper}']"),
        page.locator(f"[class*='button']:has-text('{safe_upper}')"),
        page.locator(f"[class*='btn']:has-text('{safe_upper}')"),
        page.locator(f"[id='{safe_upper}'], [id='{safe_lower}']"),
        page.locator(f"[name='{safe_upper}'], [name='{safe_lower}']"),
        page.locator(f"//input[contains(@value, '{safe_upper}')]"),
        page.locator(f"//*[contains(@value, '{safe_upper}')]"),
        page.locator(f"//*[contains(text(), '{safe_upper}')]"),
    ]
    for locator in strategies:
        try:
            count = await locator.count()
            if count > 0:
                for i in range(count):
                    target = locator.nth(i)
                    if await target.is_visible():
                        await target.scroll_into_view_if_needed()
                        await target.click(timeout=5_000)
                        return True
                target = locator.first
                await target.scroll_into_view_if_needed()
                await target.click(timeout=5_000)
                return True
        except Exception:
            continue
    try:
        result = await page.evaluate(
            """(text) => {
                var safe = text.replace(/"/g, '\\\\"').replace(/'/g, "\\\\'");
                var el = document.querySelector('input[value*="' + safe + '"]:not([style*="display: none"])');
                if (!el) {
                    var all = document.querySelectorAll('[value*="' + safe + '"]');
                    for (var i = 0; i < all.length; i++) {
                        if (all[i].offsetParent !== null) {
                            el = all[i];
                            break;
                        }
                    }
                }
                if (el) {
                    el.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                    return true;
                }
                return false;
            }""",
            text,
        )
        if result:
            return True
    except Exception:
        pass
    return False


async def _safe_close(context, browser, *, page1: Optional[Page] = None, base_page: Optional[Page] = None):
    try:
        if page1 is not None and base_page is not None and page1 is not base_page:
            await page1.close()
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


async def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    def _env_flag(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}

    import sys
    # Support: python vep_ccma_bot_logger.py CUIT:CLAVE [CUIT_REPRESENTADO]
    if len(sys.argv) > 1 and ":" in sys.argv[1]:
        partes = sys.argv[1].split(":")
        cuit_representante = partes[0]
        clave_representante = partes[1]
        cuit_representado = sys.argv[2] if len(sys.argv) > 2 else cuit_representante
        generar_volante = False
    else:
        cuit_representante = os.getenv("VEP_CCMA_CUIT_REPRESENTANTE", "")
        clave_representante = os.getenv("VEP_CCMA_CLAVE_REPRESENTANTE", "")
        cuit_representado = os.getenv("VEP_CCMA_CUIT_REPRESENTADO", "")
        generar_volante_env = os.getenv("VEP_CCMA_GENERAR_VOLANTE", "true")
        generar_volante = generar_volante_env.strip().lower() in {"1", "true", "yes", "y", "on"}

    medio_pago = os.getenv("VEP_CCMA_MEDIO_PAGO", "internet_banking")
    b64_PDF_data = _env_flag("VEP_CCMA_B64_PDF")
    proxy_request = _env_flag("VEP_CCMA_PROXY_REQUEST")

    result, error_logs, success = await bot_vep_ccma(
        cuit_representante=cuit_representante,
        clave_representante=clave_representante,
        cuit_representado=cuit_representado,
        medio_pago=medio_pago,
        b64_PDF_data=b64_PDF_data,
        proxy_request=proxy_request,
        generar_volante=generar_volante,
    )

    if error_logs:
        for entry in error_logs:
            print(entry)
    if success:
        import json
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
