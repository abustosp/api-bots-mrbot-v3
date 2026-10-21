import asyncio
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
        }

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
            
            browser = await pw.chromium.launch(headless=True,
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
    
    def _env_flag(name: str, default: bool = False) -> bool:
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}

    cuit_representante = os.getenv("CCMA_CUIT_REPRESENTANTE", "")
    clave_representante = os.getenv("CCMA_CLAVE_REPRESENTANTE", "")
    cuit_representado = os.getenv("CCMA_CUIT_REPRESENTADO", "")
    movimientos = _env_flag("CCMA_MOVIMIENTOS")
    proxy_request = _env_flag("CCMA_PROXY_REQUEST")
    pdf = _env_flag("CCMA_PDF")

    result, error_logs, success = await bot_ccma(
        cuit_representante=cuit_representante,
        clave_representante=clave_representante,
        cuit_representado=cuit_representado,
        movimientos=movimientos,
        proxy_request=proxy_request,
        pdf=pdf
    )

    if error_logs:
        for entry in error_logs:
            print(entry)
    elif success:
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
