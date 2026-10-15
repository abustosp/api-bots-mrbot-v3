import asyncio
import base64
import json
import os
import tempfile
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from shared.arca_login import login_arca, open_arca_service
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser

from playwright.async_api import (
    async_playwright,
    Download,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

# ----------------------------------------------------------------------

# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# Helpers
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


async def _extract_qr_src(page: Page) -> Optional[str]:
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


async def _scrape_volante_table(page: Page) -> List[Dict[str, Any]]:
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


def _row_matches_filtro(
    row: Dict[str, Any],
    filtro_entry: Dict[str, Any],
    field_map: Dict[str, str],
) -> bool:
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


FIELD_MAP_IMPUESTOS: Dict[str, str] = {
    "periodo": "periodo",
    "impuesto": "impuesto",
    "concepto": "concepto",
    "subconcepto": "subconcepto",
    "categoria": "categoria",
}
FIELD_MAP_INTERESES: Dict[str, str] = {
    "periodo": "periodo",
    "impuesto": "impuesto",
    "concepto": "concepto",
    "subcpto": "subconcepto",
}


async def _click_button_by_text(page: Page, text: str) -> bool:
    text_upper = text.upper()
    text_lower = text.lower()
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


async def _safe_close(
    context,
    browser,
    *,
    page1: Optional[Page] = None,
    base_page: Optional[Page] = None,
) -> None:
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




# ----------------------------------------------------------------------
# Bot principal
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
    Genera un VEP desde la Cuenta Corriente de Monotributistas y Autónomos
    (CCMA) para un contribuyente.

    Retorna:
        (result: dict, error_logs: list[str], success: bool)

    Result keys:
        - impuestos, intereses: listas completas scrapeadas
        - total_impuestos, total_intereses, total_seleccionado: int
        - volante_data: filas seleccionadas
        - NOMBRE_ARCHIVO, B64 (PDF)

    """
    result: Dict[str, Any] = {}
    error_logs: List[str] = []
    success = True

    medio_pagos_habilitados = [
        "link", "pago_mis_cuentas", "internet_banking", "xn_group", "qr",
    ]
    if medio_pago not in medio_pagos_habilitados:
        error_logs.append(
            f"Medio de pago '{medio_pago}' no está habilitado. "
            f"Usar: {', '.join(medio_pagos_habilitados)}"
        )
        return result, error_logs, False

    proxy = get_proxy_config() if proxy_request else None

    tmp_dir = None
    try:
        async with limit_browser(), async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True, proxy=proxy)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()

            # ---- 1) LOGIN ----
            login_result = await login_arca(
                page,
                str(cuit_representante),
                str(clave_representante),
                error_logs=error_logs,
            )
            if not login_result.success:
                await _safe_close(context, browser)
                return result, error_logs, False

            # ---- 2) ABRIR CCMA ----
            page1 = await open_arca_service(
                page,
                service_name="CCMA",
                service_matchers=(
                    re.compile(
                        r"CCMA\s*-\s*CUENTA CORRIENTE DE CONTRIBUYENTES "
                        r"MONOTRIBUTISTAS Y AUTONOMOS",
                        re.I,
                    ),
                ),
                error_logs=error_logs,
                ver_todos_error_message=(
                    "No se pudo desplegar 'Ver todos' "
                    "(se intentará abrir el servicio igual)"
                ),
                service_unavailable_message=(
                    "No se encuentra habilitado el servicio de CCMA"
                ),
            )
            if page1 is None:
                error_logs.append("No se encuentra habilitado el servicio de CCMA")
                await _safe_close(context, browser)
                return result, error_logs, False

            # ---- 3) SELECCIONAR CUIT ----
            try:
                await page1.get_by_role("listbox").select_option(
                    str(cuit_representado), timeout=2_000
                )
                await page1.get_by_role(
                    "button", name=re.compile(r"Elegir CUIT", re.I)
                ).click()
            except Exception:
                pass

            # ---- 3b) PERÍODO Y CÁLCULO ----
            try:
                await page1.get_by_role(
                    "cell", name="PERÍODO DESDE (MM/AAAA)", exact=True
                ).click()
                campo = page1.locator('input[name="perdesde2"]')
                await campo.click()
                await campo.press("ArrowRight")
                await campo.fill("04/2000")
                await page1.get_by_role("button", name="CALCULO DE DEUDA").click()
            except Exception as e:
                error_logs.append(
                    f"No se pudo cargar período o ejecutar el cálculo: {e}"
                )
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            try:
                await page1.locator('select[name="rango"]').select_option(
                    "48", timeout=3_000
                )
                try:
                    await page1.wait_for_load_state("networkidle", timeout=3_000)
                except PlaywrightTimeoutError:
                    pass
            except Exception:
                pass

            # ---- 4) CLICK VOLANTE DE PAGO ----
            volante_clicked = False
            for _ in range(2):
                if await _click_button_by_text(page1, "VOLANTE DE PAGO"):
                    volante_clicked = True
                    break
                await page1.wait_for_timeout(1_000)
            if not volante_clicked:
                error_logs.append("No se encontró el botón VOLANTE DE PAGO")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            await page1.wait_for_timeout(2_000)

            # ---- 5) SCRAPEAR TABLA ----
            volante_rows = await _scrape_volante_table(page1)
            if not volante_rows:
                error_logs.append(
                    "No se encontraron filas en la tabla del volante de pago"
                )
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            capital_rows = [r for r in volante_rows if r.get("tipo") == "capital"]
            interes_rows = [r for r in volante_rows if r.get("tipo") == "interes"]

            result["impuestos"] = capital_rows
            result["intereses"] = interes_rows
            result["total_impuestos"] = len(capital_rows)
            result["total_intereses"] = len(interes_rows)

            # ---- Determinar índices a seleccionar ----
            indices_a_chequear: Set[int] = set()

            if seleccionar_impuestos:
                if filtro_impuestos:
                    for row in capital_rows:
                        for entry in filtro_impuestos:
                            if not entry:
                                continue
                            if _row_matches_filtro(row, entry, FIELD_MAP_IMPUESTOS):
                                indices_a_chequear.add(row.get("checkbox_index", -1))
                                break
                else:
                    for row in capital_rows:
                        indices_a_chequear.add(row.get("checkbox_index", -1))

            if seleccionar_intereses:
                if filtro_intereses:
                    for row in interes_rows:
                        for entry in filtro_intereses:
                            if not entry:
                                continue
                            if _row_matches_filtro(row, entry, FIELD_MAP_INTERESES):
                                indices_a_chequear.add(row.get("checkbox_index", -1))
                                break
                else:
                    for row in interes_rows:
                        indices_a_chequear.add(row.get("checkbox_index", -1))

            indices_a_chequear.discard(-1)
            indices_list = sorted(indices_a_chequear)

            if not indices_list:
                error_logs.append(
                    "No hay filas que coincidan con los filtros en el volante de pago"
                )
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            all_rows = capital_rows + interes_rows
            volante_data_selected = [
                r for r in all_rows if r.get("checkbox_index") in indices_a_chequear
            ]
            result["volante_data"] = volante_data_selected
            result["total_seleccionado"] = len(volante_data_selected)

            # Deseleccionar todo y chequear solo las seleccionadas
            await _uncheck_all_volante_rows(page1)
            await _check_volante_rows(page1, indices_list)
            await page1.wait_for_timeout(1_000)

            if not generar_volante:
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, True

            # ---- 6) CLICK GENERAR VEP O QR ----
            generar_clicked = False
            for _ in range(2):
                if await _click_button_by_text(page1, "GENERAR VEP O QR"):
                    generar_clicked = True
                    break
                await page1.wait_for_timeout(1_000)
            if not generar_clicked:
                error_logs.append("No se encontró el botón GENERAR VEP O QR")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            await page1.wait_for_timeout(3_000)

            # ---- 7) CERRAR MODALES PREVIOS ----
            for _ in range(3):
                try:
                    await page1.get_by_text("close", exact=True).click(timeout=2_000)
                except Exception:
                    pass
                try:
                    await page1.get_by_role("button", name="Close").click(
                        timeout=2_000
                    )
                except Exception:
                    pass
                try:
                    await page1.get_by_role("button", name="Aceptar").click(
                        timeout=2_000
                    )
                except Exception:
                    pass

            # ---- 8) SELECCIONAR MEDIO DE PAGO ----
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
                    await page1.get_by_role("button", name="Aceptar").click(
                        timeout=5_000
                    )
                except Exception:
                    pass
            except Exception as e:
                error_logs.append(f"Error al seleccionar medio de pago: {e}")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            # ---- 9) DESCARGA PDF ----
            # Directorio temporal para la descarga
            tmp_dir = tempfile.mkdtemp(prefix="vep_ccma_")
            output_dir = Path(tmp_dir)

            download_path: Optional[str] = None
            download_name: Optional[str] = None
            pdf_data: Optional[bytes] = None
            qr_path: Optional[str] = None

            try:
                async with page1.expect_download() as download_info:
                    await page1.get_by_role(
                        "button", name="Descargar VEP"
                    ).click()
                download = await download_info.value
                download_name = download.suggested_filename or "vep_detalle.pdf"
                download_path = str(output_dir / download_name)
                await download.save_as(path=download_path)
                with open(download_path, "rb") as f:
                    pdf_data = f.read()
            except Exception as e:
                error_logs.append(f"Error al descargar el detalle del VEP: {e}")

            if pdf_data is None:
                try:
                    async with page1.expect_popup() as page2_info:
                        await page1.get_by_title("Ver detalle").click()
                    page2 = await page2_info.value
                    async with page2.expect_download() as download1_info:
                        await page2.get_by_role(
                            "button", name="picture_as_pdf"
                        ).click()
                    download1 = await download1_info.value
                    pdf_data, download_name = await _download_to_bytes(download1)
                    download_path = str(output_dir / (download_name or "vep_detalle.pdf"))
                    with open(download_path, "wb") as f:
                        f.write(pdf_data)
                    await page2.close()
                except Exception as e2:
                    error_logs.append(f"Error al descargar PDF: {e2}")
                    success = False

            # ---- 10) RESPUESTA PDF ----
            if pdf_data:
                result["NOMBRE_ARCHIVO"] = download_name or "vep_detalle.pdf"
                if b64_PDF_data:
                    result["B64"] = base64.b64encode(pdf_data).decode("utf-8")
            # ---- 11) QR (si aplica) ----
            if medio_pago == "qr":
                qr_src = await _extract_qr_src(page1)
                qr_bytes = _decode_qr_src(qr_src) if qr_src else None
                if qr_bytes:
                    qr_name = _qr_filename(download_name or "vep_detalle.pdf")
                    qr_path = str(output_dir / qr_name)
                    try:
                        with open(qr_path, "wb") as f:
                            f.write(qr_bytes)
                        result["NOMBRE_QR"] = qr_name
                        if b64_PDF_data:
                            result["B64_QR"] = base64.b64encode(qr_bytes).decode(
                                "utf-8"
                            )
                    except Exception as e:
                        error_logs.append(f"Error al guardar QR: {e}")
                else:
                    error_logs.append("No se pudo obtener el QR del VEP")

            # ---- 12) CIERRE ORDENADO ----
            await _safe_close(context, browser, page1=page1, base_page=page)

    except Exception as e:
        error_logs.append(f"Error global en vep_ccma: {e}")
        success = False

    # ---- 13) LIMPIEZA TEMPORAL ----
    if tmp_dir:
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except Exception as e:
            error_logs.append(f"Error al limpiar directorio temporal: {e}")

    return result, error_logs, success
