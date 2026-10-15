import asyncio
import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from shared.arca_login import login_arca, open_arca_service
from shared.proxy import get_proxy_config

from playwright.async_api import (
    async_playwright,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

DOWNLOAD_DIR = Path("./descargas")
DOWNLOAD_DIR.mkdir(exist_ok=True)
TEMP_DIR = Path("./temp")
TEMP_DIR.mkdir(exist_ok=True)

_MOVIMIENTOS_TABLE_SELECTOR = (
    'table[width="729"][border="0"][cellpadding="0"][height="5"]'
    '[class="CabeceraTablaBorde_ConsDeuFec"]'
)
_MOVIMIENTOS_ROW_SELECTOR = "tbody tr[valign='middle'][align='center']"
_MOVIMIENTOS_NEXT_SELECTOR = "#mas1"

import logging
def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"

class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "ccma") -> None:
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
# API principal (estilo bot_sct): devuelve (result_dict, error_logs, success)
# ----------------------------------------------------------------------
async def bot_ccma(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    movimientos: bool = False,
    proxy_request: bool = False,
    pdf: Optional[bool] = None,
    compensar: bool = True,
    confirmar: bool = True,
    headless: bool = True,
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
            "pdf_path": ...  # opcional
            "compensaciones": { ... }  # opcional (si compensar=True)
        }

    Compensación:
        Si `compensar=True`, busca los pagos con saldo a favor (pagos con botón
        Reafectar) y los reafecta contra las obligaciones con saldo deudor.
        Se prioriza el mismo impuesto/concepto/subconcepto y, agotado éste, se
        continúa con otros impuestos en orden cronológico (más antiguos primero).
        Si `confirmar=True` se confirma la reafectación (se ejecuta); si es
        False, los cambios quedan pendientes de confirmación en el detalle.

    Retorno:
        (result: dict, error_logs: list[str], success: bool)
    """
    result: Dict[str, Any] = {}
    error_logs: List[str] = []
    success = True
    
    debug = DebugCapture(prefix="ccma")
    debug.log("Iniciando bot CCMA Logger")

    proxy = get_proxy_config() if proxy_request else None

    try:
        async with async_playwright() as pw:
            har_path = debug.har_dir / "ccma.har"
            debug.log(f"Configurando HAR en {har_path}")
            
            browser = await pw.chromium.launch(headless=headless,
                                               proxy=proxy)
            context = await browser.new_context(record_har_path=har_path, record_har_content="embed")
            page = await context.new_page()
            await debug.capture(page, "inicio")

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
            await debug.capture(page, "servicio_abierto_main_page")
            
            if page1 is None:
                debug.log("Servicio CCMA no habilitado")
                error_logs.append("No se encuentra habilitado el servicio de CCMA")
                await _safe_close(context, browser)
                return result, error_logs, False
            
            await debug.capture(page1, "servicio_ccma_page")

            # -----------------------------------------
            # Seleccionar CUIT (si corresponde)
            # -----------------------------------------
            try:
                # Algunas versiones muestran un listbox + botón "Elegir CUIT"
                await page1.get_by_role("listbox").select_option(str(cuit_representado), timeout=2_000)
                await page1.get_by_role("button", name=re.compile(r"Elegir CUIT", re.I)).click()
                await debug.capture(page1, "cuit_seleccionado")
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
                await debug.capture(page1, "calculo_realizado")
            except Exception as e:
                debug.log(f"Error en calculo: {e}")
                error_logs.append(f"No se pudo cargar período o ejecutar el cálculo: {e}")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            # Seleccionar para mostrar 48 períodos (si aplica)
            try:
                await page1.locator('select[name="rango"]').select_option("48")
                await page1.wait_for_load_state("networkidle")
                await debug.capture(page1, "rango_seleccionado")
            except Exception:
                # No se encontró el selector o no es necesario
                pass

            # -----------------------------------------
            # Extraer datos requeridos
            # -----------------------------------------
            try:
                # El bot original se apoya en ubicaciones específicas del DOM
                elementos_td = page1.locator("td.CeldaTitularResaltado")
                
                try:
                     cuit = str(cuit_representado)
                     periodo = await elementos_td.nth(0).inner_text(timeout=5000)
                except Exception: periodo = "N/A"

                try:
                    deuda_capital = await page1.locator(
                        "xpath=/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
                        "table/tbody/tr[2]/td/table/tbody/tr[2]/td[2]/table/tbody/tr/td[1]"
                    ).inner_text(timeout=5000)
                except Exception: deuda_capital = "Error"

                try:
                    deuda_accesorios = await page1.locator(
                        "xpath=/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
                        "table/tbody/tr[2]/td/table/tbody/tr[3]/td[2]/table/tbody/tr/td[1]"
                    ).inner_text(timeout=5000)
                except Exception: deuda_accesorios = "Error"

                try:
                     total_deuda = await elementos_td.nth(1).inner_text(timeout=5000)
                except Exception: total_deuda = "Error"

                try:
                    credito_capital = await page1.locator(
                        "xpath=/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
                        "table/tbody/tr[2]/td/table/tbody/tr[2]/td[4]/table/tbody/tr/td[1]"
                    ).inner_text(timeout=5000)
                except Exception: credito_capital = "Error"

                try:
                    credito_accesorios = await page1.locator(
                        "xpath=/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
                        "table/tbody/tr[2]/td/table/tbody/tr[3]/td[4]/table/tbody/tr/td[1]"
                    ).inner_text(timeout=5000)
                except Exception: credito_accesorios = "Error"

                try:
                    total_a_favor = await elementos_td.nth(2).inner_text(timeout=5000)
                except Exception: total_a_favor = "Error"
                
                result = {
                    "cuit": cuit,
                    "periodo": periodo,
                    "deuda_capital": deuda_capital,
                    "deuda_accesorios": deuda_accesorios,
                    "total_deuda": total_deuda,
                    "credito_capital": credito_capital,
                    "credito_accesorios": credito_accesorios,
                    "total_a_favor": total_a_favor
                }
                if pdf:
                    pdf_path = await _descargar_pdf_ccma(
                        page1=page1,
                        cuit_representado=cuit_representado,
                        error_logs=error_logs,
                        debug=debug,
                    )
                    if pdf_path:
                        result["pdf_path"] = pdf_path

                if movimientos:
                    async def _log_movimientos_page(page_number: int, state: Dict[str, Any], page_movimientos: List[Dict[str, str]]) -> None:
                        debug.log(
                            f"movimientos_pagina={page_number} "
                            f"primer={state.get('primerPeriodo', '')} "
                            f"ultimo={state.get('ultimoPeriodo', '')} "
                            f"filas_visibles={state.get('visibleDataRows', 0)} "
                            f"extraidos={len(page_movimientos)} "
                            f"next_visible={state.get('nextVisible', False)}"
                        )
                        await debug.capture(page1, f"movimientos_pagina_{page_number:02d}")

                    result["movimientos"] = await _collect_all_ccma_movimientos(
                        page1,
                        on_page_loaded=_log_movimientos_page,
                    )
                    debug.log(f"movimientos_totales={len(result['movimientos'])}")

                if compensar:
                    try:
                        result["compensaciones"] = await _compensar_saldos_a_favor(
                            page1,
                            debug=debug,
                            error_logs=error_logs,
                            confirmar=confirmar,
                            # El cálculo de deuda ya dejó la sabana lista; sólo
                            # se recalcula si movimientos=True la dejó paginada.
                            sabana_fresca_inicial=(not movimientos),
                        )
                        debug.log(f"Compensaciones finalizadas: {result['compensaciones']}")
                    except Exception as e:
                        if _es_pagina_cerrada(e):
                            error_logs.append(
                                "La página de CCMA se cerró inesperadamente durante la compensación"
                            )
                            debug.log(f"compensacion_pagina_cerrada={e}")
                        else:
                            error_logs.append(f"Error en la compensación de saldos a favor: {e}")
                            debug.log(f"Error en compensación: {e}")
                        try:
                            await debug.capture(page1, "compensacion_error")
                        except Exception:
                            pass

                debug.log(f"Datos extraidos con exito: {result}")
                    
            except Exception as e:
                error_logs.append(f"Error extrayendo datos de CCMA: {e}")
                debug.log(f"Error extrayendo datos: {e}")
                
                # Debug HTML on failure
                try: 
                    html_debug = debug.html_dir / f"extraction_failure_{datetime.now().strftime('%H%M%S')}.html"
                    html_debug.write_text(await page1.content(), encoding='utf-8')
                    debug.log(f"Dumped HTML to {html_debug}")
                except: pass
                
                await debug.capture(page1, "error_extraccion")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            # Cierre ordenado
            await _safe_close(context, browser, page1=page1, base_page=page)

            # Save result to JSON
            import json
            json_path = debug.descargas_dir / f"ccma_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=4)
            debug.log(f"Saved CCMA data to {json_path}")
            
    except Exception as e:
        error_logs.append(f"No se pudo iniciar sesión en ARCA. Verifique los datos ingresados: {e}")
        debug.log(f"Login/Init failed: {e}")
        success = False

    return result, error_logs, success


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
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

        await next_button.scroll_into_view_if_needed()
        await next_button.click()

        try:
            await page.wait_for_load_state("domcontentloaded", timeout=1_500)
        except PlaywrightTimeoutError:
            pass

        if not await _wait_for_movimientos_state_change(page, current_state):
            break

        page_number += 1

    return movimientos_data


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


def _es_pagina_cerrada(exc: Exception) -> bool:
    texto = str(exc).lower()
    return (
        type(exc).__name__ == "TargetClosedError"
        or "has been closed" in texto
        or ("target" in texto and "closed" in texto)
    )


_ESTADO_COMPENSACION_PATH = DOWNLOAD_DIR / "ccma_compensacion_estado.json"


def _clave_destino_tuple(clave: Tuple[str, ...]) -> str:
    return "|".join(clave)


def _cargar_estado_compensacion(debug: DebugCapture) -> Dict[str, Any]:
    ruta = _ESTADO_COMPENSACION_PATH
    if not ruta.exists():
        return {"destinos_cancelados": [], "saldos_destino": {}}
    try:
        data = json.loads(ruta.read_text(encoding="utf-8"))
    except Exception as exc:
        debug.log(f"compensacion_estado_no_leido={exc}")
        return {"destinos_cancelados": [], "saldos_destino": {}}
    return {
        "destinos_cancelados": [tuple(item) for item in data.get("destinos_cancelados", [])],
        "saldos_destino": {
            k: float(v) for k, v in (data.get("saldos_destino") or {}).items()
        },
    }


def _guardar_estado_compensacion(
    debug: DebugCapture,
    *,
    destinos_cancelados: Set[Tuple[str, ...]],
    saldos_destino: Dict[str, float],
) -> None:
    ruta = _ESTADO_COMPENSACION_PATH
    data = {
        "destinos_cancelados": [list(item) for item in sorted(destinos_cancelados)],
        "saldos_destino": saldos_destino,
    }
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as exc:
        debug.log(f"compensacion_estado_no_guardado={exc}")


async def _extract_compensacion_page(page: Page) -> Dict[str, List[Dict[str, str]]]:
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
    page: Page,
    *,
    debug: DebugCapture,
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

        debug.log(
            f"compensacion_sabana_pagina={page_number} "
            f"primer={current_state.get('primerPeriodo', '')} "
            f"ultimo={current_state.get('ultimoPeriodo', '')} "
            f"movimientos={len(movimientos)} saldos={len(saldos)} pagos={len(pagos)} "
            f"next_visible={current_state.get('nextVisible', False)}"
        )

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
        except PlaywrightTimeoutError:
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


async def _leer_form_reafectacion(page: Page) -> Dict[str, Any]:
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


async def _leer_error_compensacion(page: Page) -> str:
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
    page: Page,
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
    page: Page,
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


async def _agregar_afectacion(page: Page, *, debug: DebugCapture) -> Dict[str, Any]:
    form_info = await _leer_form_reafectacion(page)
    total_antes = _parse_monto(form_info.get("total_reafectado", ""))
    try:
        async with page.expect_navigation(timeout=25_000, wait_until="domcontentloaded"):
            boton = page.locator("#Afectar")
            visible = await boton.count() > 0 and await boton.first.is_visible()
            if visible:
                try:
                    await boton.first.click(timeout=5_000)
                except Exception:
                    debug.log("compensacion_agregar_click_js (click normal falló)")
                    await page.evaluate("document.getElementById('Afectar').click()")
            else:
                debug.log("compensacion_agregar_click_js (botón oculto)")
                await page.evaluate("document.getElementById('Afectar').click()")
    except Exception as exc:
        debug.log(f"compensacion_agregar_error_navegacion={exc}")
        return {"error": f"Error al agregar la afectación: {exc}", "aceptado": False}

    try:
        await page.wait_for_selector("#impuestoAjax", timeout=20_000)
    except PlaywrightTimeoutError:
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
    page: Page,
    destino: Dict[str, str],
    remanente: float,
    *,
    debug: DebugCapture,
    saldo_grupo: float = 0.0,
    saldo_cache: float = 0.0,
    intentos: int = 3,
) -> Dict[str, Any]:
    tentativo = remanente
    if saldo_cache > 0:
        tentativo = min(remanente, saldo_cache)
    elif saldo_grupo > 0:
        tentativo = min(remanente, saldo_grupo)
    importe = _format_importe(tentativo)
    saldo_deudor_conocido = saldo_cache

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
                "saldo_deudor": saldo_deudor_conocido,
            }

        resultado = await _agregar_afectacion(page, debug=debug)
        error = resultado.get("error") or ""
        if resultado.get("aceptado"):
            return {
                "estado": "aceptado",
                "importe": float(importe),
                "detalle": "",
                "saldo_deudor": saldo_deudor_conocido,
            }
        if _ORA_DESTINO_CANCELADO_RE.search(error):
            return {
                "estado": "cancelado",
                "importe": 0.0,
                "detalle": error,
                "saldo_deudor": saldo_deudor_conocido,
            }

        match = _ORA_MONTO_MAYOR_RE.search(error)
        if match:
            saldo_deudor = _parse_monto(match.group(1))
            if saldo_deudor <= 0:
                return {
                    "estado": "cancelado",
                    "importe": 0.0,
                    "detalle": error,
                    "saldo_deudor": saldo_deudor_conocido,
                }
            saldo_deudor_conocido = saldo_deudor
            nuevo_importe = _format_importe(min(saldo_deudor, remanente))
            if float(nuevo_importe) >= float(importe) - 0.004:
                return {
                    "estado": "cancelado",
                    "importe": 0.0,
                    "detalle": error,
                    "saldo_deudor": saldo_deudor,
                }
            importe = nuevo_importe
            continue

        return {
            "estado": "error",
            "importe": 0.0,
            "detalle": error or "Respuesta desconocida del servidor",
            "saldo_deudor": saldo_deudor_conocido,
        }

    return {
        "estado": "error",
        "importe": 0.0,
        "detalle": "No se pudo determinar un importe válido para el destino",
        "saldo_deudor": saldo_deudor_conocido,
    }


async def _avanzar_pagina_sabana(page: Page, *, debug: DebugCapture) -> bool:
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
        except PlaywrightTimeoutError:
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


async def _abrir_reafectacion(
    page: Page,
    row_id: str,
    *,
    debug: DebugCapture,
) -> bool:
    try:
        async with page.expect_navigation(timeout=45_000, wait_until="domcontentloaded"):
            await page.evaluate(_ABRIR_REAFECTACION_JS, row_id)
        await page.wait_for_selector("#impuestoAjax", timeout=20_000)
        return True
    except Exception as exc:
        debug.log(f"compensacion_reafectar_error rowId={row_id} error={exc}")
        return False


async def _confirmar_reafectacion(page: Page) -> bool:
    boton = page.locator(_CONFIRMAR_BOTON_SELECTOR)
    try:
        if await boton.count() == 0:
            return False
        async with page.expect_navigation(timeout=45_000, wait_until="domcontentloaded"):
            await boton.first.click()
        return True
    except Exception:
        return False


async def _cancelar_reafectacion(page: Page) -> None:
    boton = page.locator(_CANCELAR_BOTON_SELECTOR)
    try:
        if await boton.count() == 0:
            return
        async with page.expect_navigation(timeout=30_000, wait_until="domcontentloaded"):
            await boton.first.click()
    except Exception:
        pass


async def _volver_a_sabana_compensacion(page: Page, *, debug: DebugCapture) -> bool:
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
    except Exception as exc:
        debug.log(f"compensacion_volver_p02_error={exc}")
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
        except PlaywrightTimeoutError:
            pass
        await page.wait_for_selector(_MOVIMIENTOS_TABLE_SELECTOR, timeout=30_000)
    except Exception as exc:
        debug.log(f"compensacion_volver_sabana_error={exc}")
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
    page: Page,
    *,
    debug: DebugCapture,
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
    estado_previo = _cargar_estado_compensacion(debug)
    destinos_cancelados.update(estado_previo["destinos_cancelados"])
    saldos_destino: Dict[str, float] = dict(estado_previo["saldos_destino"])
    debug.log(
        f"compensacion_estado_previo cancelados={len(estado_previo['destinos_cancelados'])} "
        f"saldos={len(estado_previo['saldos_destino'])}"
    )

    # La sabana queda recién calculada por bot_ccma (un solo cálculo de deuda
    # en el inicio). Sólo se recalcula aquí si bot_ccma la dejó paginada
    # (movimientos=True); el resto de los recálculos ocurren únicamente tras
    # una confirmación, que invalida los rowId.
    sabana_fresca = sabana_fresca_inicial
    escaneado = False
    movimientos: List[Dict[str, str]] = []
    pagos: List[Dict[str, str]] = []
    saldos_por_grupo: Dict[Tuple[str, str], float] = {}

    for numero in range(1, max_reafectaciones + 1):
        # La sabana se recalcula y re-escanea sólo cuando fue alterada por una
        # confirmación (los rowId cambian). Tras un intento cancelado los
        # rowId siguen vigentes y se reutiliza el relevamiento anterior con el
        # seguimiento local de saldos ya reafectados.
        if not sabana_fresca:
            debug.log("compensacion_recalcula_deuda")
            if not await _volver_a_sabana_compensacion(page, debug=debug):
                error_logs.append(
                    "No se pudo recalcular la deuda para continuar con las compensaciones"
                )
                break
            debug.log("compensacion_usa_sabana_recalculada")
            sabana_fresca = True
            escaneado = False

        if not escaneado:
            try:
                await page.wait_for_selector(_MOVIMIENTOS_TABLE_SELECTOR, timeout=20_000)
            except PlaywrightTimeoutError:
                debug.log("compensacion_sabana_no_presente: se recalcula")
                sabana_fresca = False
                continue
            movimientos, saldos, pagos = await _collect_compensacion_saldos(page, debug=debug)
            resultado["saldos"] = saldos
            saldos_por_grupo = _construir_saldos_por_grupo(saldos)
            escaneado = True
            debug.log(
                f"compensacion_sabana_fresca movimientos={len(movimientos)} "
                f"saldos={len(saldos)} pagos_reafectables={len(pagos)}"
            )
            if numero == 1:
                await debug.capture(page, "compensacion_saldos_inicial")
            if not pagos:
                debug.log("No se encontraron pagos con botón Reafectar")
                break

        candidatos = [
            pago
            for pago in pagos
            if _clave_pago(pago) not in sin_destino
            and _clave_pago(pago) not in finalizados
        ]
        if not candidatos:
            debug.log("No quedan pagos con saldo a favor para compensar")
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
        debug.log(f"compensacion_pago_numero={numero} pago={pago}")

        if not await _abrir_reafectacion(page, pago["rowId"], debug=debug):
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
        total_form = _parse_monto(form_info.get("total_reafectado", ""))
        debug.log(
            f"compensacion_form rowId={form_info.get('rowId')} "
            f"sobrante={remanente} total={total_form}"
        )
        await debug.capture(page, f"compensacion_form_{numero:03d}")

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
        debug.log(f"compensacion_destinos={len(destinos)}")

        aplicadas: List[Dict[str, Any]] = []
        errores_consecutivos = 0
        for indice, destino in enumerate(destinos, start=1):
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
            clave_destino_str = _clave_destino_tuple(clave_destino)
            resultado_destino = await _imputar_destino(
                page,
                destino,
                remanente,
                saldo_grupo=saldo_grupo,
                saldo_cache=saldos_destino.get(clave_destino_str, 0.0),
                debug=debug,
            )
            if resultado_destino.get("saldo_deudor"):
                saldos_destino[clave_destino_str] = resultado_destino["saldo_deudor"]
            debug.log(
                f"compensacion_destino periodo={destino['periodo']} "
                f"imp={destino['impuesto']}/{destino['concepto']}/{destino['subconcepto']} "
                f"estado={resultado_destino['estado']} "
                f"importe={resultado_destino['importe']} "
                f"saldo_deudor={resultado_destino.get('saldo_deudor', 0.0)} "
                f"detalle={resultado_destino['detalle']}"
            )
            if resultado_destino["estado"] == "aceptado":
                errores_consecutivos = 0
                remanente -= resultado_destino["importe"]
                aplicadas.append({**destino, "importe": resultado_destino["importe"]})
                await debug.capture(
                    page, f"compensacion_agregado_{numero:03d}_{indice:03d}"
                )
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
                debug.log(
                    f"compensacion_confirmada={confirmado} aplicadas={len(aplicadas)}"
                )
                if not confirmado:
                    error_logs.append(
                        "No se pudo confirmar la reafectación del pago "
                        f"{pago['periodo']} {pago['impuesto']}/{pago['concepto']}/{pago['subconcepto']}"
                    )
            else:
                debug.log("compensacion_sin_confirmar: cambios pendientes en el detalle")
            resultado["reafectaciones"].append(
                {"pago": pago, "aplicadas": aplicadas, "confirmado": confirmado}
            )
            finalizados.add(clave_pago)
            if confirmado:
                sabana_fresca = False
                escaneado = False
                # Los saldos deudores cambiaron: el caché de sondeo ya no sirve.
                saldos_destino.clear()
            for aplicada in aplicadas:
                grupo = (aplicada["periodo"], aplicada["impuesto"])
                if grupo in saldos_por_grupo:
                    saldos_por_grupo[grupo] -= aplicada["importe"]
                    if saldos_por_grupo[grupo] <= 0.004:
                        del saldos_por_grupo[grupo]
            await debug.capture(page, f"compensacion_pago_{numero:03d}_fin")
        else:
            resultado["pagos_sin_destino"].append(pago)
            sin_destino.add(clave_pago)
            debug.log(f"compensacion_pago_sin_destino pago={pago}")
            if await page.locator(_CONFIRMAR_BOTON_SELECTOR).count() > 0:
                await _cancelar_reafectacion(page)

        _guardar_estado_compensacion(
            debug,
            destinos_cancelados=destinos_cancelados,
            saldos_destino=saldos_destino,
        )

    debug.log(
        f"compensacion_fin reafectaciones={len(resultado['reafectaciones'])} "
        f"pagos_sin_destino={len(resultado['pagos_sin_destino'])} "
        f"errores={len(resultado['errores'])}"
    )
    return resultado


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


async def _descargar_pdf_ccma(
    page1: Page,
    cuit_representado: str,
    error_logs: List[str],
    debug: DebugCapture,
) -> str:
    
    tmp_pdf_path = ""
    try:
        try:
            await page1.emulate_media(media="screen")
        except Exception:
            pass

        nombre_archivo_pdf = f"ccma_{cuit_representado}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        tmp_pdf_path = debug.descargas_dir / nombre_archivo_pdf

        await page1.pdf(path=str(tmp_pdf_path), format="A4", print_background=True)
        debug.log(f"PDF guardado en {tmp_pdf_path}")
        return str(tmp_pdf_path)

    except Exception as exc:
        debug.log(f"Error generando PDF: {exc}")
        error_logs.append(f"Error al generar/guardar PDF de CCMA: {exc}")
        
    return ""

async def main() -> None:
    try:
        from dotenv import load_dotenv
    except Exception:
        load_dotenv = None

    if load_dotenv is not None:
        load_dotenv()
        env_local = Path(__file__).resolve().parent / ".env"
        if env_local.exists():
            load_dotenv(env_local, override=True)
    
    def _env_flag(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}

    cuit_representante = (
        os.getenv("CCMA_COMPENSACION_CUIT_REPRESENTANTE")
        or os.getenv("CCMA_CUIT_REPRESENTANTE", "")
    )
    clave_representante = (
        os.getenv("CCMA_COMPENSACION_CLAVE_REPRESENTANTE")
        or os.getenv("CCMA_CLAVE_REPRESENTANTE", "")
    )
    cuit_representado = (
        os.getenv("CCMA_COMPENSACION_CUIT_REPRESENTADO")
        or os.getenv("CCMA_CUIT_REPRESENTADO", "")
    )
    movimientos = _env_flag("CCMA_MOVIMIENTOS")
    proxy_request = _env_flag("CCMA_PROXY_REQUEST")
    pdf = _env_flag("CCMA_PDF")
    compensar = _env_flag("CCMA_COMPENSACION_COMPENSAR", True)
    confirmar = _env_flag("CCMA_COMPENSACION_CONFIRMAR", True)
    headless = _env_flag("CCMA_COMPENSACION_HEADLESS", _env_flag("ARCA_LOGIN_HEADLESS", True))

    result, error_logs, success = await bot_ccma(
        cuit_representante=cuit_representante,
        clave_representante=clave_representante,
        cuit_representado=cuit_representado,
        movimientos=movimientos,
        proxy_request=proxy_request,
        pdf=pdf,
        compensar=compensar,
        confirmar=confirmar,
        headless=headless,
    )

    if error_logs:
        for entry in error_logs:
            print(entry)
    elif success:
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
