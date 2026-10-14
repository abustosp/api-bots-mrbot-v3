"""Portal CCMA compensación: reafectación de pagos con saldo a favor.

Port de ``bots_dev/ccma_compenscion/ccma_compensacion_bot.py`` (V1/V2
``app/bot/ccma_compensacion_bot.py``). Extiende el portal ``ccma`` (selección
de CUIT, cálculo de deuda, resumen y movimientos) con el recorrido de la
sabana que detecta los pagos con botón *Reafectar* y los imputa contra las
obligaciones con saldo deudor: primero el mismo impuesto/concepto/subconcepto
y luego otros impuestos, en orden cronológico. Con ``confirmar=False`` las
reafectaciones quedan agregadas al detalle sin confirmar.

El módulo no importa Playwright: los timeouts se capturan como ``Exception``.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .ccma import CcmaPortal

_MOVIMIENTOS_TABLE_SELECTOR = (
    'table[width="729"][border="0"][cellpadding="0"][height="5"]'
    '[class="CabeceraTablaBorde_ConsDeuFec"]'
)
_MOVIMIENTOS_ROW_SELECTOR = "tbody tr[valign='middle'][align='center']"
_MOVIMIENTOS_NEXT_SELECTOR = "#mas1"


async def _get_ccma_movimientos_state(page: Any) -> Dict[str, Any]:
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


async def _wait_for_movimientos_state_change(
    page: Any,
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
    except Exception:
        return False


# ----------------------------------------------------------------------
# Compensación de saldos a favor (reafectación de pagos)
# ----------------------------------------------------------------------
_P02_PAGE = "P02_ctacte.asp"
_P04_PAGE = "P04_ctacte.asp"
_CALCULO_DESDE = "04/2000"
_REAFECTAR_IMG_SELECTOR = 'img[src*="reafectar"]'
_CONFIRMAR_BOTON_SELECTOR = 'input[name="btn_confirma"][value="Confirmar cambios"]'
_CANCELAR_BOTON_SELECTOR = 'input[name="btn_confirma"][value="Cancelar"]'

_ORA_DESTINO_CANCELADO_RE = re.compile(r"totalmente cancelada", re.I)
_ORA_MONTO_MAYOR_RE = re.compile(
    r"no puede ser mayor al saldo deudor del periodo destino de\s*([\d.,]+)",
    re.I,
)


def _periodo_key(periodo: str) -> Tuple[int, int]:
    try:
        mes, anio = str(periodo).split("/")
        return int(anio), int(mes)
    except Exception:
        return 9999, 99


def _parse_monto(value: str) -> float:
    texto = str(value or "").replace("\u00a0", " ").strip()
    if not texto:
        return 0.0
    negativo = texto.startswith("(") and texto.endswith(")")
    texto = texto.strip("()").replace(",", "").replace(" ", "")
    try:
        monto = float(texto)
    except ValueError:
        return 0.0
    return -monto if negativo else monto


def _format_importe(value: float) -> str:
    return f"{max(value, 0.0):.2f}"


async def _extract_compensacion_page(page: Any) -> Dict[str, List[Dict[str, str]]]:
    return await page.evaluate(
        """
        ({ tableSelector, rowSelector }) => {
            const cleanText = (value) => (value || "").replace(/\\u00a0/g, " ").trim();
            const table = document.querySelector(tableSelector);
            if (!table) {
                return { movimientos: [], saldos: [], pagos: [] };
            }

            const movimientos = [];
            const saldos = [];
            const pagos = [];
            let periodoActual = "";
            let impuestoActual = "";
            let conceptoActual = "";
            let subconceptoActual = "";

            const rows = Array.from(table.querySelectorAll(rowSelector))
                .filter((row) => window.getComputedStyle(row).display !== "none");

            for (const row of rows) {
                const cells = Array.from(row.querySelectorAll("td.CeldaBorde_ConsDeuFec"));
                if (cells.length < 10) {
                    continue;
                }
                const values = cells.map((cell) => cleanText(cell.textContent));
                const descripcion = values[6] || "";

                if (descripcion === "Saldo") {
                    const contenedor = cells[10]
                        ? cells[10].querySelector('[id^="saldoPadre-"]')
                        : null;
                    saldos.push({
                        periodo: values[2] || periodoActual,
                        impuesto: impuestoActual,
                        concepto: conceptoActual,
                        subconcepto: subconceptoActual,
                        descripcion: "Saldo",
                        saldo: cleanText(contenedor ? contenedor.textContent : ""),
                        rowId: contenedor
                            ? (contenedor.id || "").replace("saldoPadre-", "")
                            : "",
                    });
                } else if (values[3]) {
                    periodoActual = values[2];
                    impuestoActual = values[3];
                    conceptoActual = values[4];
                    subconceptoActual = values[5];
                    movimientos.push({
                        periodo: values[2],
                        impuesto: values[3],
                        concepto: values[4],
                        subconcepto: values[5],
                        descripcion: descripcion,
                        debe: values[8],
                        haber: values[9],
                    });
                }

                const reafectar = row.querySelector('img[src*="reafectar"]');
                if (reafectar) {
                    const onclick = reafectar.getAttribute("onclick") || "";
                    const match = onclick.match(/aReafectar\\('([^']*)'/);
                    pagos.push({
                        periodo: values[2],
                        impuesto: values[3],
                        concepto: values[4],
                        subconcepto: values[5],
                        haber: values[9],
                        rowId: match ? match[1] : "",
                    });
                }
            }

            return { movimientos, saldos, pagos };
        }
        """,
        {
            "tableSelector": _MOVIMIENTOS_TABLE_SELECTOR,
            "rowSelector": _MOVIMIENTOS_ROW_SELECTOR,
        },
    )


async def _collect_compensacion_saldos(
    page: Any,
    *,
    max_pages: int = 100,
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[Dict[str, str]]]:
    movimientos: List[Dict[str, str]] = []
    saldos: List[Dict[str, str]] = []
    pagos: List[Dict[str, str]] = []
    movimientos_seen: Set[Tuple[str, ...]] = set()
    saldos_seen: Set[Tuple[str, ...]] = set()
    pagos_seen: Set[str] = set()
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

        page_data = await _extract_compensacion_page(page)
        for movimiento in page_data.get("movimientos", []):
            movimiento_key = (
                movimiento["periodo"],
                movimiento["impuesto"],
                movimiento["concepto"],
                movimiento["subconcepto"],
                movimiento["descripcion"],
                movimiento["debe"],
                movimiento["haber"],
            )
            if movimiento_key in movimientos_seen:
                continue
            movimientos_seen.add(movimiento_key)
            movimientos.append(movimiento)

        for saldo in page_data.get("saldos", []):
            saldo_key = (
                saldo["periodo"],
                saldo["impuesto"],
                saldo["concepto"],
                saldo["subconcepto"],
                saldo["saldo"],
            )
            if saldo_key in saldos_seen:
                continue
            saldos_seen.add(saldo_key)
            saldos.append(saldo)

        for pago in page_data.get("pagos", []):
            row_id = pago.get("rowId") or ""
            if not row_id or row_id in pagos_seen:
                continue
            pagos_seen.add(row_id)
            pago["pagina"] = page_number
            pagos.append(pago)

        if not current_state.get("nextVisible"):
            break

        next_button = page.locator(_MOVIMIENTOS_NEXT_SELECTOR)
        if await next_button.count() == 0:
            break

        await next_button.scroll_into_view_if_needed()
        try:
            await next_button.click()
        except Exception:
            pass

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1_500)
        except Exception:
            pass

        if not await _wait_for_movimientos_state_change(page, current_state):
            try:
                await page.evaluate("cargaSabana('mas')")
            except Exception:
                break
            if not await _wait_for_movimientos_state_change(page, current_state):
                break

        page_number += 1

    return movimientos, saldos, pagos


def _construir_destinos(
    movimientos: List[Dict[str, str]],
    origen: Dict[str, str],
    grupos_deudores: Optional[Set[Tuple[str, str]]] = None,
) -> List[Dict[str, str]]:
    mismo: List[Dict[str, str]] = []
    otros: List[Dict[str, str]] = []
    vistos: Set[Tuple[str, ...]] = set()
    origen_key = (
        origen.get("periodo", ""),
        origen.get("impuesto", ""),
        origen.get("concepto", ""),
        origen.get("subconcepto", ""),
    )
    origen_combo = (
        origen.get("impuesto", ""),
        origen.get("concepto", ""),
        origen.get("subconcepto", ""),
    )

    for movimiento in movimientos:
        if _parse_monto(movimiento.get("debe", "")) <= 0:
            continue
        if grupos_deudores is not None:
            grupo = (movimiento["periodo"], movimiento["impuesto"])
            if grupo not in grupos_deudores:
                continue
        clave = (
            movimiento["periodo"],
            movimiento["impuesto"],
            movimiento["concepto"],
            movimiento["subconcepto"],
        )
        if clave in vistos or clave == origen_key:
            continue
        vistos.add(clave)
        combo = (
            movimiento["impuesto"],
            movimiento["concepto"],
            movimiento["subconcepto"],
        )
        if combo == origen_combo:
            mismo.append(movimiento)
        else:
            otros.append(movimiento)

    def _orden(movimiento: Dict[str, str]) -> Tuple[int, int, str, str, str]:
        anio, mes = _periodo_key(movimiento["periodo"])
        return (anio, mes, movimiento["impuesto"], movimiento["concepto"], movimiento["subconcepto"])

    return sorted(mismo, key=_orden) + sorted(otros, key=_orden)


async def _leer_form_reafectacion(page: Any) -> Dict[str, Any]:
    return await page.evaluate(
        """
        () => {
            const val = (name) => {
                const el = document.querySelector(`[name="${name}"]`);
                return el ? (el.value || "") : "";
            };
            return {
                rowId: val("rowId"),
                impuesto: val("impuesto_orig"),
                periodo: val("periodo_orig"),
                concepto: val("concepto_orig"),
                subconcepto: val("subconcepto_orig"),
                importe_orig: val("importe_orig"),
                importe_sobrante: val("importe_sobrante"),
                total_reafectado: val("total_reafectado"),
            };
        }
        """
    )


async def _leer_error_compensacion(page: Any) -> str:
    try:
        errores = await page.locator("div.error").all_inner_texts()
    except Exception:
        return ""
    for texto in errores:
        limpio = re.sub(r"\s+", " ", texto or "").strip()
        if "ORA-" in limpio.upper() or "reafect" in limpio.lower():
            return limpio
    return ""


async def _esperar_opcion_select(
    page: Any,
    select_id: str,
    value: str,
    *,
    timeout_ms: int = 30_000,
) -> None:
    await page.wait_for_function(
        """
        ({ selectId, value }) => {
            const select = document.getElementById(selectId);
            if (!select || select.disabled) {
                return false;
            }
            return Array.from(select.options).some((option) => option.value === value);
        }
        """,
        arg={"selectId": select_id, "value": value},
        timeout=timeout_ms,
    )


async def _seleccionar_destino(
    page: Any,
    *,
    periodo: str,
    impuesto: str,
    concepto: str,
    subconcepto: str,
    importe: float,
) -> None:
    ultimo_error: Optional[Exception] = None
    for _ in range(3):
        try:
            await page.wait_for_selector("#impuestoAjax", state="visible", timeout=15_000)
            # 1) impuesto: espera el AJAX que carga los conceptos.
            # Se resetea el select por JS para garantizar que select_option
            # dispare el evento change aunque el valor estuviera repetido.
            await page.evaluate("document.getElementById('impuestoAjax').value = '-1';")
            await page.select_option("#impuestoAjax", impuesto, timeout=10_000)
            await _esperar_opcion_select(page, "conceptoAjax", concepto)
            # 2) concepto: espera el AJAX que carga los subconceptos
            await page.select_option("#conceptoAjax", concepto, timeout=10_000)
            await _esperar_opcion_select(page, "subconceptoAjax", subconcepto)
            # 3) subconcepto
            await page.select_option("#subconceptoAjax", subconcepto, timeout=10_000)
            # 4) monto
            await page.fill("#periodo", periodo)
            await page.fill("#importe", _format_importe(importe))
            return
        except Exception as exc:
            ultimo_error = exc
            await asyncio.sleep(1)
    raise RuntimeError(
        f"No se pudieron seleccionar impuesto/concepto/subconcepto: {ultimo_error}"
    )


async def _agregar_afectacion(page: Any) -> Dict[str, Any]:
    form_info = await _leer_form_reafectacion(page)
    total_antes = _parse_monto(form_info.get("total_reafectado", ""))
    try:
        await page.wait_for_selector("#Afectar", state="visible", timeout=15_000)
    except Exception:
        pass
    try:
        async with page.expect_navigation(timeout=45_000, wait_until="domcontentloaded"):
            try:
                await page.click("#Afectar", timeout=10_000)
            except Exception:
                await page.evaluate("document.getElementById('Afectar').click()")
    except Exception as exc:
        return {"error": f"Error al agregar la afectación: {exc}", "aceptado": False}

    try:
        await page.wait_for_selector("#impuestoAjax", timeout=20_000)
    except Exception:
        pass

    error = await _leer_error_compensacion(page)
    form_info = await _leer_form_reafectacion(page)
    total_despues = _parse_monto(form_info.get("total_reafectado", ""))
    return {
        "error": error,
        "total_antes": total_antes,
        "total_despues": total_despues,
        "aceptado": not error and total_despues > total_antes + 1e-6,
    }


async def _imputar_destino(
    page: Any,
    destino: Dict[str, str],
    remanente: float,
    *,
    saldo_grupo: float = 0.0,
    intentos: int = 3,
) -> Dict[str, Any]:
    tentativo = remanente
    if saldo_grupo > 0:
        tentativo = min(remanente, saldo_grupo)
    importe = _format_importe(tentativo)
    for _ in range(intentos):
        try:
            await _seleccionar_destino(
                page,
                periodo=destino["periodo"],
                impuesto=destino["impuesto"],
                concepto=destino["concepto"],
                subconcepto=destino["subconcepto"],
                importe=float(importe),
            )
        except Exception as exc:
            return {
                "estado": "error",
                "importe": 0.0,
                "detalle": f"No se pudo seleccionar el destino: {exc}",
            }

        resultado = await _agregar_afectacion(page)
        error = resultado.get("error") or ""
        if resultado.get("aceptado"):
            return {"estado": "aceptado", "importe": float(importe), "detalle": ""}
        if _ORA_DESTINO_CANCELADO_RE.search(error):
            return {"estado": "cancelado", "importe": 0.0, "detalle": error}

        match = _ORA_MONTO_MAYOR_RE.search(error)
        if match:
            saldo_deudor = _parse_monto(match.group(1))
            if saldo_deudor <= 0:
                return {"estado": "cancelado", "importe": 0.0, "detalle": error}
            nuevo_importe = _format_importe(min(saldo_deudor, remanente))
            if float(nuevo_importe) >= float(importe) - 0.004:
                return {"estado": "cancelado", "importe": 0.0, "detalle": error}
            importe = nuevo_importe
            continue

        return {
            "estado": "error",
            "importe": 0.0,
            "detalle": error or "Respuesta desconocida del servidor",
        }

    return {
        "estado": "error",
        "importe": 0.0,
        "detalle": "No se pudo determinar un importe válido para el destino",
    }


async def _avanzar_pagina_sabana(page: Any) -> bool:
    current_state = await _get_ccma_movimientos_state(page)
    if not current_state.get("nextVisible"):
        return False
    next_button = page.locator(_MOVIMIENTOS_NEXT_SELECTOR)
    if await next_button.count() > 0:
        await next_button.scroll_into_view_if_needed()
        try:
            await next_button.click()
        except Exception:
            pass
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1_500)
        except Exception:
            pass
    if await _wait_for_movimientos_state_change(page, current_state):
        return True
    try:
        await page.evaluate("cargaSabana('mas')")
    except Exception:
        return False
    return await _wait_for_movimientos_state_change(page, current_state)


_ABRIR_REAFECTACION_JS = """
(rowId) => {
    if (typeof aReafectar === 'function') {
        aReafectar(rowId, '');
        return 'aReafectar';
    }
    document.frm.rowId.value = rowId;
    document.frm.hm_estado.value = '';
    document.frm.action = 'carga_reafectacion.asp';
    document.frm.submit();
    return 'submit';
}
"""


async def _abrir_reafectacion(page: Any, row_id: str) -> bool:
    try:
        async with page.expect_navigation(timeout=45_000, wait_until="domcontentloaded"):
            await page.evaluate(_ABRIR_REAFECTACION_JS, row_id)
        await page.wait_for_selector("#impuestoAjax", timeout=20_000)
        return True
    except Exception:
        return False


async def _confirmar_reafectacion(page: Any) -> bool:
    boton = page.locator(_CONFIRMAR_BOTON_SELECTOR)
    try:
        if await boton.count() == 0:
            return False
        async with page.expect_navigation(timeout=45_000, wait_until="domcontentloaded"):
            await boton.first.click()
        return True
    except Exception:
        return False


async def _cancelar_reafectacion(page: Any) -> None:
    boton = page.locator(_CANCELAR_BOTON_SELECTOR)
    try:
        if await boton.count() == 0:
            return
        async with page.expect_navigation(timeout=30_000, wait_until="domcontentloaded"):
            await boton.first.click()
    except Exception:
        pass


async def _volver_a_sabana_compensacion(page: Any) -> bool:
    url_actual = page.url or ""
    if "/ccam/" in url_actual:
        base_url = url_actual.split("/ccam/")[0] + "/ccam/"
    else:
        base_url = url_actual.rsplit("/", 1)[0] + "/"

    # La pantalla posterior a una reafectación ("Última deuda calculada") no
    # expone los botones Reafectar: se recalcula la deuda desde P02 para
    # obtener una sabana fresca.
    try:
        await page.goto(base_url + _P02_PAGE, wait_until="domcontentloaded", timeout=45_000)
    except Exception:
        return False

    try:
        await page.wait_for_selector('input[name="perdesde2"]', timeout=15_000)
        campo = page.locator('input[name="perdesde2"]')
        try:
            await campo.click(timeout=2_000)
            await campo.press("ArrowRight")
        except Exception:
            pass
        await campo.fill(_CALCULO_DESDE)
        for locator in (
            page.get_by_role("button", name=re.compile(r"CALCULO DE DEUDA", re.I)),
            page.locator("input[value*='CALCULO DE DEUDA' i]"),
            page.locator("input[value*='CALCULO' i]"),
        ):
            if await locator.count() > 0:
                await locator.first.click()
                break
        try:
            await page.wait_for_load_state("networkidle", timeout=20_000)
        except Exception:
            pass
        await page.wait_for_selector(_MOVIMIENTOS_TABLE_SELECTOR, timeout=30_000)
    except Exception:
        return False

    try:
        await page.locator('select[name="rango"]').select_option("48", timeout=3_000)
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:
        pass

    return await page.locator(_MOVIMIENTOS_TABLE_SELECTOR).count() > 0


def _clave_pago(pago: Dict[str, str]) -> Tuple[str, ...]:
    return (
        pago.get("periodo", ""),
        pago.get("impuesto", ""),
        pago.get("concepto", ""),
        pago.get("subconcepto", ""),
        pago.get("haber", ""),
    )


def _construir_saldos_por_grupo(
    saldos: List[Dict[str, str]],
) -> Dict[Tuple[str, str], float]:
    saldos_por_grupo: Dict[Tuple[str, str], float] = {}
    for saldo in saldos:
        monto = _parse_monto(saldo.get("saldo", ""))
        if monto < 0:
            saldos_por_grupo[
                (saldo.get("periodo", ""), saldo.get("impuesto", ""))
            ] = abs(monto)
    return saldos_por_grupo


async def _compensar_saldos_a_favor(
    page: Any,
    *,
    error_logs: List[str],
    confirmar: bool = True,
    sabana_fresca_inicial: bool = False,
    max_reafectaciones: int = 200,
) -> Dict[str, Any]:
    resultado: Dict[str, Any] = {
        "saldos": [],
        "reafectaciones": [],
        "pagos_sin_destino": [],
        "errores": [],
    }
    sin_destino: Set[Tuple[str, ...]] = set()
    finalizados: Set[Tuple[str, ...]] = set()
    destinos_cancelados: Set[Tuple[str, ...]] = set()

    # La sabana queda recién calculada por bot_ccma (un solo cálculo de deuda
    # en el inicio). Sólo se recalcula aquí si bot_ccma la dejó paginada
    # (movimientos=True); el resto de los recálculos ocurren únicamente tras
    # una confirmación, que invalida los rowId.
    sabana_fresca = sabana_fresca_inicial
    escaneado = False
    movimientos: List[Dict[str, str]] = []
    pagos: List[Dict[str, str]] = []
    saldos_por_grupo: Dict[Tuple[str, str], float] = {}

    for _ in range(max_reafectaciones):
        # La sabana se recalcula y re-escanea sólo cuando fue alterada por una
        # confirmación (los rowId cambian). Tras un intento cancelado los
        # rowId siguen vigentes y se reutiliza el relevamiento anterior con el
        # seguimiento local de saldos ya reafectados.
        if not sabana_fresca:
            if not await _volver_a_sabana_compensacion(page):
                error_logs.append(
                    "No se pudo recalcular la deuda para continuar con las compensaciones"
                )
                break
            sabana_fresca = True
            escaneado = False

        if not escaneado:
            try:
                await page.wait_for_selector(_MOVIMIENTOS_TABLE_SELECTOR, timeout=20_000)
            except Exception:
                sabana_fresca = False
                continue
            movimientos, saldos, pagos = await _collect_compensacion_saldos(page)
            resultado["saldos"] = saldos
            saldos_por_grupo = _construir_saldos_por_grupo(saldos)
            escaneado = True
            if not pagos:
                break

        candidatos = [
            pago
            for pago in pagos
            if _clave_pago(pago) not in sin_destino
            and _clave_pago(pago) not in finalizados
        ]
        if not candidatos:
            break
        pago = min(
            candidatos,
            key=lambda p: (
                *_periodo_key(p["periodo"]),
                p["impuesto"],
                p["concepto"],
                p["subconcepto"],
            ),
        )
        clave_pago = _clave_pago(pago)

        if not await _abrir_reafectacion(page, pago["rowId"]):
            sabana_fresca = False
            escaneado = False
            sin_destino.add(clave_pago)
            error_logs.append(
                "No se pudo abrir la reafectación del pago "
                f"{pago['periodo']} {pago['impuesto']}/{pago['concepto']}/{pago['subconcepto']}"
            )
            continue

        form_info = await _leer_form_reafectacion(page)
        remanente = _parse_monto(form_info.get("importe_sobrante", ""))

        destinos = _construir_destinos(
            movimientos,
            {
                "periodo": form_info.get("periodo") or pago["periodo"],
                "impuesto": form_info.get("impuesto") or pago["impuesto"],
                "concepto": form_info.get("concepto") or pago["concepto"],
                "subconcepto": form_info.get("subconcepto") or pago["subconcepto"],
            },
            grupos_deudores=set(saldos_por_grupo.keys()),
        )

        aplicadas: List[Dict[str, Any]] = []
        errores_consecutivos = 0
        for destino in destinos:
            if remanente <= 0.004:
                break
            clave_destino = (
                destino["periodo"],
                destino["impuesto"],
                destino["concepto"],
                destino["subconcepto"],
            )
            if clave_destino in destinos_cancelados:
                continue
            saldo_grupo = saldos_por_grupo.get(
                (destino["periodo"], destino["impuesto"]), 0.0
            )
            resultado_destino = await _imputar_destino(
                page, destino, remanente, saldo_grupo=saldo_grupo
            )
            if resultado_destino["estado"] == "aceptado":
                errores_consecutivos = 0
                remanente -= resultado_destino["importe"]
                aplicadas.append({**destino, "importe": resultado_destino["importe"]})
            elif resultado_destino["estado"] == "cancelado":
                errores_consecutivos = 0
                destinos_cancelados.add(clave_destino)
            elif resultado_destino["estado"] == "error":
                errores_consecutivos += 1
                resultado["errores"].append(
                    {"destino": destino, "detalle": resultado_destino["detalle"]}
                )
                if errores_consecutivos >= 3:
                    error_logs.append(
                        "Se aborta la imputación del pago por errores consecutivos: "
                        f"{pago['periodo']} {pago['impuesto']}/{pago['concepto']}/{pago['subconcepto']}"
                    )
                    break

        if aplicadas:
            confirmado = False
            if confirmar:
                confirmado = await _confirmar_reafectacion(page)
                if not confirmado:
                    error_logs.append(
                        "No se pudo confirmar la reafectación del pago "
                        f"{pago['periodo']} {pago['impuesto']}/{pago['concepto']}/{pago['subconcepto']}"
                    )
            resultado["reafectaciones"].append(
                {"pago": pago, "aplicadas": aplicadas, "confirmado": confirmado}
            )
            finalizados.add(clave_pago)
            if confirmado:
                sabana_fresca = False
                escaneado = False
            for aplicada in aplicadas:
                grupo = (aplicada["periodo"], aplicada["impuesto"])
                if grupo in saldos_por_grupo:
                    saldos_por_grupo[grupo] -= aplicada["importe"]
                    if saldos_por_grupo[grupo] <= 0.004:
                        del saldos_por_grupo[grupo]
        else:
            resultado["pagos_sin_destino"].append(pago)
            sin_destino.add(clave_pago)
            if await page.locator(_CONFIRMAR_BOTON_SELECTOR).count() > 0:
                await _cancelar_reafectacion(page)

    return resultado


class CcmaCompensacionPortal(CcmaPortal):
    """Portal CCMA con la compensación de saldos a favor."""

    nombre = "ccma_compensacion"

    async def esperar_resumen(self) -> None:
        """Espera tolerante del resumen tras el cálculo de deuda."""
        try:
            await self._esperar_resumen()
        except Exception:
            pass

    async def compensar_saldos_a_favor(
        self,
        *,
        confirmar: bool,
        sabana_fresca_inicial: bool,
        max_reafectaciones: int = 200,
    ) -> Tuple[Dict[str, Any], List[str]]:
        """Reafecta los pagos con saldo a favor. Devuelve ``(detalle, avisos)``."""
        avisos: List[str] = []
        detalle = await _compensar_saldos_a_favor(
            self.page,
            error_logs=avisos,
            confirmar=confirmar,
            sabana_fresca_inicial=sabana_fresca_inicial,
            max_reafectaciones=max_reafectaciones,
        )
        return detalle, avisos
