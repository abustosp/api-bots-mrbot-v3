import asyncio
from html import escape
import os
import re
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from shared.arca_login import login_arca, open_arca_service
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser

from playwright.async_api import (
    async_playwright,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)


_MOVIMIENTOS_TABLE_SELECTOR = (
    'table[width="729"][border="0"][cellpadding="0"][height="5"]'
    '[class="CabeceraTablaBorde_ConsDeuFec"]'
)
_MOVIMIENTOS_ROW_SELECTOR = "tbody tr[valign='middle'][align='center']"
_MOVIMIENTOS_NEXT_SELECTOR = "#mas1"
_CCMA_SUMMARY_SELECTOR = "td.CeldaTitularResaltado"
_CCMA_DEUDA_CAPITAL_XPATH = (
    "/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
    "table/tbody/tr[2]/td/table/tbody/tr[2]/td[2]/table/tbody/tr/td[1]"
)
_CCMA_DEUDA_ACCESORIOS_XPATH = (
    "/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
    "table/tbody/tr[2]/td/table/tbody/tr[3]/td[2]/table/tbody/tr/td[1]"
)
_CCMA_CREDITO_CAPITAL_XPATH = (
    "/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
    "table/tbody/tr[2]/td/table/tbody/tr[2]/td[4]/table/tbody/tr/td[1]"
)
_CCMA_CREDITO_ACCESORIOS_XPATH = (
    "/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
    "table/tbody/tr[2]/td/table/tbody/tr[3]/td[4]/table/tbody/tr/td[1]"
)

# ----------------------------------------------------------------------
# API principal (estilo bot_sct): devuelve (result_dict, error_logs, success)
# ----------------------------------------------------------------------
async def bot_ccma(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    movimientos: bool = False,
    proxy_request: bool = False,
    pdf: Optional[bool] = None,
) -> Tuple[Dict[str, Any], List[str], bool]:
    """
    Ejecuta la consulta CCMA en ARCA/AFIP y devuelve un JSON con:
        {
            "cuit": ...,
            "periodo": ...,
            "deuda_capital": ...,
            "deuda_accesorios": ...,
            "total_deuda": ...,
            "credito_capital": ...,
            "credito_accesorios": ...,
            "total_a_favor": ...
            "movimientos": [ ... ]  # opcional

        }

    Retorno:
        (result: dict, error_logs: list[str], success: bool)
    """
    result: Dict[str, Any] = {}
    error_logs: List[str] = []
    success = True


    proxy = get_proxy_config() if proxy_request==True else None

    try:
        async with limit_browser(), async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True,
                                               proxy=proxy)
            context = await browser.new_context()
            page = await context.new_page()

            login_result = await login_arca(
                page,
                str(cuit_representante),
                str(clave_representante),
                error_logs=error_logs,
            )
            if not login_result.success:
                await _safe_close(context, browser)
                return result, error_logs, False

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
                ver_todos_error_message="No se pudo desplegar 'Ver todos' (se intentará abrir el servicio igual)",
                service_unavailable_message="No se encuentra habilitado el servicio de CCMA",
            )
            if page1 is None:
                error_logs.append("No se encuentra habilitado el servicio de CCMA")
                await _safe_close(context, browser)
                return result, error_logs, False

            # -----------------------------------------
            # Seleccionar CUIT (si corresponde)
            # -----------------------------------------
            try:
                # Algunas versiones muestran un listbox + botón "Elegir CUIT"
                await page1.get_by_role("listbox").select_option(str(cuit_representado), timeout=2_000)
                await page1.get_by_role("button", name=re.compile(r"Elegir CUIT", re.I)).click()
            except Exception:
                # Si no existe el listbox, puede ya estar sobre el contribuyente buscado
                pass

            # -----------------------------------------
            # Ingresar PERÍODO DESDE y calcular
            # (El UI de CCMA pide MM/AAAA; por defecto usamos 01/2019
            #  como en el bot original. Podés adaptar a tu necesidad.)
            # -----------------------------------------
            try:
                await page1.get_by_role("cell", name="PERÍODO DESDE (MM/AAAA)", exact=True).click()
                campo = page1.locator('input[name="perdesde2"]')
                await campo.click()
                await campo.press("ArrowRight")
                await campo.fill("04/2000")
                await page1.get_by_role("button", name="CALCULO DE DEUDA").click()
                await page1.goto("https://servicios2.afip.gob.ar/tramites_con_clave_fiscal/ccam/P04_ctacte.asp")
                await _wait_for_ccma_summary_ready(page1)
            except Exception as e:
                error_logs.append(f"No se pudo cargar período o ejecutar el cálculo: {e}")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False
            

            # Seleccionar para mostrar 48 períodos (si aplica)
            try:
                await page1.locator('select[name="rango"]').select_option("48", timeout=3_000)
                try:
                    await page1.wait_for_load_state("networkidle", timeout=3_000)
                except PlaywrightTimeoutError:
                    pass
            except Exception:
                # No se encontró el selector o no es necesario
                pass

            # -----------------------------------------
            # Extraer datos requeridos
            # -----------------------------------------
            try:
                result = await _extract_ccma_summary(page1, cuit_representado)
            except Exception as e:
                error_logs.append(f"Error extrayendo datos de CCMA: {e}")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            movimientos_data: Optional[List[Dict[str, str]]] = None
            if movimientos or pdf:
                movimientos_data = await _collect_ccma_movimientos_with_fallback(page1)

            if movimientos:
                result["movimientos"] = movimientos_data or []


            # Cierre ordenado
            await _safe_close(context, browser, page1=page1, base_page=page)

    except Exception as e:
        error_logs.append(f"No se pudo iniciar sesión en ARCA. Verifique los datos ingresados: {e}")
        success = False

    return result, error_logs, success


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
async def _wait_for_ccma_summary_ready(page: Page, timeout_ms: int = 12_000) -> None:
    await page.wait_for_function(
        """
        ({ summarySelector }) => {
            const cleanText = (value) => (value || "").replace(/\\u00a0/g, " ").trim();
            const texts = Array.from(document.querySelectorAll(summarySelector))
                .map((cell) => cleanText(cell.textContent))
                .filter(Boolean);
            return texts.length >= 3;
        }
        """,
        arg={"summarySelector": _CCMA_SUMMARY_SELECTOR},
        timeout=timeout_ms,
    )


async def _extract_ccma_summary(page: Page, cuit_representado: str) -> Dict[str, str]:
    result = await page.evaluate(
        """
        ({ cuit, summarySelector, deudaCapitalXpath, deudaAccesoriosXpath, creditoCapitalXpath, creditoAccesoriosXpath }) => {
            const cleanText = (value) => (value || "").replace(/\\u00a0/g, " ").trim();
            const getByXpath = (xpath) => document.evaluate(
                xpath,
                document,
                null,
                XPathResult.FIRST_ORDERED_NODE_TYPE,
                null
            ).singleNodeValue;
            const summaryTexts = Array.from(document.querySelectorAll(summarySelector))
                .map((cell) => cleanText(cell.textContent));

            return {
                cuit,
                periodo: summaryTexts[0] || "",
                deuda_capital: cleanText(getByXpath(deudaCapitalXpath)?.textContent),
                deuda_accesorios: cleanText(getByXpath(deudaAccesoriosXpath)?.textContent),
                total_deuda: summaryTexts[1] || "",
                credito_capital: cleanText(getByXpath(creditoCapitalXpath)?.textContent),
                credito_accesorios: cleanText(getByXpath(creditoAccesoriosXpath)?.textContent),
                total_a_favor: summaryTexts[2] || "",
            };
        }
        """,
        {
            "cuit": str(cuit_representado),
            "summarySelector": _CCMA_SUMMARY_SELECTOR,
            "deudaCapitalXpath": _CCMA_DEUDA_CAPITAL_XPATH,
            "deudaAccesoriosXpath": _CCMA_DEUDA_ACCESORIOS_XPATH,
            "creditoCapitalXpath": _CCMA_CREDITO_CAPITAL_XPATH,
            "creditoAccesoriosXpath": _CCMA_CREDITO_ACCESORIOS_XPATH,
        },
    )

    missing_fields = [
        field
        for field in (
            "periodo",
            "deuda_capital",
            "deuda_accesorios",
            "total_deuda",
            "credito_capital",
            "credito_accesorios",
            "total_a_favor",
        )
        if not result.get(field)
    ]
    if missing_fields:
        raise ValueError(
            "No se pudieron obtener los campos principales de CCMA: "
            + ", ".join(missing_fields)
        )

    return result


async def _get_ccma_movimientos_state(page: Page) -> Dict[str, Any]:
    return await page.evaluate(
        """
        ({ tableSelector, rowSelector, nextSelector }) => {
            const cleanText = (value) => (value || "").replace(/\\u00a0/g, " ").trim();
            const table = document.querySelector(tableSelector);
            const nextButton = document.querySelector(nextSelector);
            const rows = table ? Array.from(table.querySelectorAll(rowSelector)) : [];
            const visibleRows = rows.filter(
                (row) => window.getComputedStyle(row).display !== "none"
            );
            const visibleDataRows = visibleRows.filter((row) => {
                const cells = row.querySelectorAll("td.CeldaBorde_ConsDeuFec");
                if (cells.length < 10) {
                    return false;
                }
                return cleanText(cells[6]?.textContent) !== "Saldo";
            });
            const firstRow = visibleDataRows[0];
            const lastRow = visibleDataRows[visibleDataRows.length - 1];
            const getPeriodo = (row) => {
                if (!row) {
                    return "";
                }
                const cells = row.querySelectorAll("td.CeldaBorde_ConsDeuFec");
                return cleanText(cells[2]?.textContent);
            };

            return {
                tablePresent: Boolean(table),
                primerPeriodo: cleanText(document.getElementById("primerPeriodo")?.value),
                ultimoPeriodo: cleanText(document.getElementById("ultimoPeriodo")?.value),
                periodoMinimo: cleanText(document.getElementById("periodoMinimo")?.value),
                periodoMaximo: cleanText(document.getElementById("periodoMaximo")?.value),
                nextVisible: Boolean(
                    nextButton && window.getComputedStyle(nextButton).display !== "none"
                ),
                firstVisiblePeriodo: getPeriodo(firstRow),
                lastVisiblePeriodo: getPeriodo(lastRow),
                visibleDataRows: visibleDataRows.length,
            };
        }
        """,
        {
            "tableSelector": _MOVIMIENTOS_TABLE_SELECTOR,
            "rowSelector": _MOVIMIENTOS_ROW_SELECTOR,
            "nextSelector": _MOVIMIENTOS_NEXT_SELECTOR,
        },
    )


async def _extract_visible_ccma_movimientos(page: Page) -> List[Dict[str, str]]:
    return await page.evaluate(
        """
        ({ tableSelector, rowSelector }) => {
            const cleanText = (value) => (value || "").replace(/\\u00a0/g, " ").trim();
            const table = document.querySelector(tableSelector);
            if (!table) {
                return [];
            }

            return Array.from(table.querySelectorAll(rowSelector))
                .filter((row) => window.getComputedStyle(row).display !== "none")
                .map((row) => {
                    const cells = Array.from(
                        row.querySelectorAll("td.CeldaBorde_ConsDeuFec")
                    ).map((cell) => cleanText(cell.textContent));
                    if (cells.length < 10 || cells[6] === "Saldo") {
                        return null;
                    }

                    return {
                        periodo: cells[2],
                        impuesto: cells[3],
                        concepto: cells[4],
                        subconcepto: cells[5],
                        descripcion: cells[6],
                        fecha_movimiento: cells[7],
                        debe: cells[8],
                        haber: cells[9],
                    };
                })
                .filter(Boolean);
        }
        """,
        {
            "tableSelector": _MOVIMIENTOS_TABLE_SELECTOR,
            "rowSelector": _MOVIMIENTOS_ROW_SELECTOR,
        },
    )


async def _collect_all_ccma_movimientos(
    page: Page,
    *,
    on_page_loaded: Optional[
        Callable[[int, Dict[str, Any], List[Dict[str, str]]], Awaitable[None]]
    ] = None,
    max_pages: int = 100,
) -> List[Dict[str, str]]:
    movimientos_data: List[Dict[str, str]] = []
    movimientos_seen: Set[Tuple[str, ...]] = set()
    states_seen: Set[Tuple[str, str, str, str]] = set()

    page_number = 1
    while page_number <= max_pages:
        current_state = await _get_ccma_movimientos_state(page)
        if not current_state.get("tablePresent"):
            break

        state_key = (
            str(current_state.get("primerPeriodo", "")),
            str(current_state.get("ultimoPeriodo", "")),
            str(current_state.get("firstVisiblePeriodo", "")),
            str(current_state.get("lastVisiblePeriodo", "")),
        )
        if state_key in states_seen:
            break
        states_seen.add(state_key)

        page_movimientos = await _extract_visible_ccma_movimientos(page)
        for movimiento in page_movimientos:
            movimiento_key = (
                movimiento["periodo"],
                movimiento["impuesto"],
                movimiento["concepto"],
                movimiento["subconcepto"],
                movimiento["descripcion"],
                movimiento["fecha_movimiento"],
                movimiento["debe"],
                movimiento["haber"],
            )
            if movimiento_key in movimientos_seen:
                continue
            movimientos_seen.add(movimiento_key)
            movimientos_data.append(movimiento)

        if on_page_loaded is not None:
            await on_page_loaded(page_number, current_state, page_movimientos)

        if not current_state.get("nextVisible"):
            break

        next_button = page.locator(_MOVIMIENTOS_NEXT_SELECTOR)
        if await next_button.count() == 0:
            break

        if not await _click_ccma_movimientos_next(page):
            break

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1_500)
        except PlaywrightTimeoutError:
            pass

        if not await _wait_for_movimientos_state_change(page, current_state):
            break

        page_number += 1

    return movimientos_data


async def _collect_ccma_movimientos_with_fallback(page: Page) -> List[Dict[str, str]]:
    try:
        visible_movimientos = await _extract_visible_ccma_movimientos(page)
    except Exception:
        visible_movimientos = []

    try:
        movimientos = await _collect_all_ccma_movimientos(page)
        return movimientos or visible_movimientos
    except PlaywrightTimeoutError:
        return visible_movimientos
    except Exception:
        return visible_movimientos


async def _click_ccma_movimientos_next(page: Page) -> bool:
    next_button = page.locator(_MOVIMIENTOS_NEXT_SELECTOR).first

    try:
        await next_button.scroll_into_view_if_needed(timeout=2_000)
    except Exception:
        pass

    for click_kwargs in (
        {"timeout": 3_000},
        {"timeout": 3_000, "force": True},
    ):
        try:
            await next_button.click(**click_kwargs)
            return True
        except Exception:
            continue

    try:
        handle = await next_button.element_handle()
        if handle is not None:
            await handle.evaluate("(el) => el.click()")
            return True
    except Exception:
        pass

    try:
        return bool(
            await page.evaluate(
                """
                (selector) => {
                    const element = document.querySelector(selector);
                    if (!element) {
                        return false;
                    }
                    element.click();
                    return true;
                }
                """,
                _MOVIMIENTOS_NEXT_SELECTOR,
            )
        )
    except Exception:
        return False


async def _wait_for_movimientos_state_change(
    page: Page,
    previous_state: Dict[str, Any],
    timeout_ms: int = 8_000,
) -> bool:
    try:
        await page.wait_for_function(
            """
            ({ tableSelector, rowSelector, nextSelector, previousState }) => {
                const cleanText = (value) => (value || "").replace(/\\u00a0/g, " ").trim();
                const table = document.querySelector(tableSelector);
                const nextButton = document.querySelector(nextSelector);
                const rows = table ? Array.from(table.querySelectorAll(rowSelector)) : [];
                const visibleRows = rows.filter(
                    (row) => window.getComputedStyle(row).display !== "none"
                );
                const visibleDataRows = visibleRows.filter((row) => {
                    const cells = row.querySelectorAll("td.CeldaBorde_ConsDeuFec");
                    if (cells.length < 10) {
                        return false;
                    }
                    return cleanText(cells[6]?.textContent) !== "Saldo";
                });
                const firstRow = visibleDataRows[0];
                const lastRow = visibleDataRows[visibleDataRows.length - 1];
                const getPeriodo = (row) => {
                    if (!row) {
                        return "";
                    }
                    const cells = row.querySelectorAll("td.CeldaBorde_ConsDeuFec");
                    return cleanText(cells[2]?.textContent);
                };

                const currentState = {
                    primerPeriodo: cleanText(document.getElementById("primerPeriodo")?.value),
                    ultimoPeriodo: cleanText(document.getElementById("ultimoPeriodo")?.value),
                    firstVisiblePeriodo: getPeriodo(firstRow),
                    lastVisiblePeriodo: getPeriodo(lastRow),
                    nextVisible: Boolean(
                        nextButton && window.getComputedStyle(nextButton).display !== "none"
                    ),
                };

                return (
                    currentState.primerPeriodo !== previousState.primerPeriodo ||
                    currentState.ultimoPeriodo !== previousState.ultimoPeriodo ||
                    currentState.firstVisiblePeriodo !== previousState.firstVisiblePeriodo ||
                    currentState.lastVisiblePeriodo !== previousState.lastVisiblePeriodo ||
                    currentState.nextVisible !== previousState.nextVisible
                );
            }
            """,
            arg={
                "tableSelector": _MOVIMIENTOS_TABLE_SELECTOR,
                "rowSelector": _MOVIMIENTOS_ROW_SELECTOR,
                "nextSelector": _MOVIMIENTOS_NEXT_SELECTOR,
                "previousState": {
                    "primerPeriodo": str(previous_state.get("primerPeriodo", "")),
                    "ultimoPeriodo": str(previous_state.get("ultimoPeriodo", "")),
                    "firstVisiblePeriodo": str(previous_state.get("firstVisiblePeriodo", "")),
                    "lastVisiblePeriodo": str(previous_state.get("lastVisiblePeriodo", "")),
                    "nextVisible": bool(previous_state.get("nextVisible")),
                },
            },
            timeout=timeout_ms,
        )
        return True
    except PlaywrightTimeoutError:
        return False


async def _safe_close(context, browser, *, page1: Optional[Page] = None, base_page: Optional[Page] = None):
    # Cerrar popup si era distinto
    try:
        if page1 is not None and base_page is not None and page1 is not base_page:
            await page1.close()
    except Exception:
        pass
    # Cerrar todo lo demás
    try:
        await context.close()
    except Exception:
        pass
    try:
        await browser.close()
    except Exception:
        pass


def _build_ccma_pdf_html(
    summary_data: Dict[str, Any],
    movimientos_data: List[Dict[str, str]],
) -> str:
    generated_at = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

    summary_labels = (
        ("CUIT", "cuit"),
        ("Periodo", "periodo"),
        ("Deuda capital", "deuda_capital"),
        ("Deuda accesorios", "deuda_accesorios"),
        ("Total deuda", "total_deuda"),
        ("Credito capital", "credito_capital"),
        ("Credito accesorios", "credito_accesorios"),
        ("Total a favor", "total_a_favor"),
    )
    summary_cards = "".join(
        (
            "<div class='summary-card'>"
            f"<div class='summary-label'>{escape(label)}</div>"
            f"<div class='summary-value'>{escape(str(summary_data.get(key, '') or '-'))}</div>"
            "</div>"
        )
        for label, key in summary_labels
    )

    movimientos_count = len(movimientos_data)
    if movimientos_data:
        movimientos_rows = "".join(
            (
                "<tr>"
                f"<td>{escape(movimiento.get('periodo', '') or '-')}</td>"
                f"<td>{escape(movimiento.get('impuesto', '') or '-')}</td>"
                f"<td>{escape(movimiento.get('concepto', '') or '-')}</td>"
                f"<td>{escape(movimiento.get('subconcepto', '') or '-')}</td>"
                f"<td>{escape(movimiento.get('descripcion', '') or '-')}</td>"
                f"<td>{escape(movimiento.get('fecha_movimiento', '') or '-')}</td>"
                f"<td class='numeric'>{escape(movimiento.get('debe', '') or '-')}</td>"
                f"<td class='numeric'>{escape(movimiento.get('haber', '') or '-')}</td>"
                "</tr>"
            )
            for movimiento in movimientos_data
        )
        movimientos_html = f"""
        <table class="movimientos-table">
          <thead>
            <tr>
              <th>Periodo</th>
              <th>Impuesto</th>
              <th>Concepto</th>
              <th>Subconcepto</th>
              <th>Descripcion</th>
              <th>Fecha movimiento</th>
              <th>Debe</th>
              <th>Haber</th>
            </tr>
          </thead>
          <tbody>
            {movimientos_rows}
          </tbody>
        </table>
        """
    else:
        movimientos_html = "<div class='empty-state'>No se encontraron movimientos para consolidar.</div>"

    return f"""<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8">
    <title>CCMA {escape(str(summary_data.get('cuit', '') or ''))}</title>
    <style>
      @page {{
        size: A4 landscape;
        margin: 10mm 8mm;
      }}
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        font-family: Arial, sans-serif;
        color: #1f2937;
        font-size: 10px;
        background: #ffffff;
      }}
      .report {{
        width: 100%;
      }}
      .header {{
        display: flex;
        justify-content: space-between;
        align-items: flex-end;
        gap: 16px;
        margin-bottom: 12px;
        padding-bottom: 8px;
        border-bottom: 2px solid #0f172a;
      }}
      .title {{
        font-size: 18px;
        font-weight: 700;
        margin: 0;
      }}
      .subtitle {{
        margin: 4px 0 0 0;
        color: #475569;
        font-size: 10px;
      }}
      .meta {{
        text-align: right;
        color: #475569;
        font-size: 9px;
      }}
      .section-title {{
        margin: 14px 0 8px 0;
        font-size: 12px;
        font-weight: 700;
        color: #0f172a;
      }}
      .summary-grid {{
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 8px;
      }}
      .summary-card {{
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        padding: 8px;
        background: #f8fafc;
      }}
      .summary-label {{
        color: #475569;
        font-size: 9px;
        text-transform: uppercase;
        margin-bottom: 4px;
      }}
      .summary-value {{
        color: #0f172a;
        font-size: 12px;
        font-weight: 700;
        word-break: break-word;
      }}
      .table-meta {{
        margin-bottom: 6px;
        color: #475569;
        font-size: 9px;
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
      }}
      thead {{
        display: table-header-group;
      }}
      tbody tr {{
        page-break-inside: avoid;
      }}
      th, td {{
        border: 1px solid #cbd5e1;
        padding: 4px 5px;
        vertical-align: top;
        word-break: break-word;
      }}
      th {{
        background: #e2e8f0;
        color: #0f172a;
        font-size: 9px;
        text-transform: uppercase;
      }}
      td {{
        font-size: 9px;
      }}
      .numeric {{
        text-align: right;
        white-space: nowrap;
      }}
      .empty-state {{
        padding: 12px;
        border: 1px solid #cbd5e1;
        border-radius: 6px;
        background: #f8fafc;
        color: #475569;
      }}
    </style>
  </head>
  <body>
    <div class="report">
      <div class="header">
        <div>
          <h1 class="title">CCMA consolidado</h1>
          <p class="subtitle">Cuenta Corriente de Monotributistas y Autonomos</p>
        </div>
        <div class="meta">
          <div>Generado: {escape(generated_at)}</div>
          <div>Movimientos consolidados: {movimientos_count}</div>
        </div>
      </div>

      <div class="section-title">Resumen</div>
      <div class="summary-grid">
        {summary_cards}
      </div>

      <div class="section-title">Movimientos</div>
      <div class="table-meta">Se incluyen todos los movimientos recolectados de la paginacion disponible.</div>
      {movimientos_html}
    </div>
  </body>
</html>
"""
