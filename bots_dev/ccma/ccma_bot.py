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

_MOVIMIENTOS_TABLE_SELECTOR = (
    'table[width="729"][border="0"][cellpadding="0"][height="5"]'
    '[class="CabeceraTablaBorde_ConsDeuFec"]'
)
_MOVIMIENTOS_ROW_SELECTOR = "tbody tr[valign='middle'][align='center']"
_MOVIMIENTOS_NEXT_SELECTOR = "#mas1"

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


    proxy = get_proxy_config() if proxy_request else None

    download_dir = DOWNLOAD_DIR / "ccma" / str(cuit_representado)
    download_dir.mkdir(parents=True, exist_ok=True)

    try:
        async with async_playwright() as pw:
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
            except Exception as e:
                error_logs.append(f"No se pudo cargar período o ejecutar el cálculo: {e}")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            # Seleccionar para mostrar 48 períodos (si aplica)
            try:
                await page1.locator('select[name="rango"]').select_option("48")
                await page1.wait_for_load_state("networkidle")
            except Exception:
                # No se encontró el selector o no es necesario
                pass

            # -----------------------------------------
            # Extraer datos requeridos
            # -----------------------------------------
            try:
                # El bot original se apoya en ubicaciones específicas del DOM
                # y en celdas de clase 'CeldaTitularResaltado'
                elementos_td = page1.locator("td.CeldaTitularResaltado")

                cuit = str(cuit_representado)

                periodo = await elementos_td.nth(0).inner_text()

                deuda_capital = await page1.locator(
                    "xpath=/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
                    "table/tbody/tr[2]/td/table/tbody/tr[2]/td[2]/table/tbody/tr/td[1]"
                ).inner_text()

                deuda_accesorios = await page1.locator(
                    "xpath=/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
                    "table/tbody/tr[2]/td/table/tbody/tr[3]/td[2]/table/tbody/tr/td[1]"
                ).inner_text()

                total_deuda = await elementos_td.nth(1).inner_text()

                credito_capital = await page1.locator(
                    "xpath=/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
                    "table/tbody/tr[2]/td/table/tbody/tr[2]/td[4]/table/tbody/tr/td[1]"
                ).inner_text()

                credito_accesorios = await page1.locator(
                    "xpath=/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/"
                    "table/tbody/tr[2]/td/table/tbody/tr[3]/td[4]/table/tbody/tr/td[1]"
                ).inner_text()

                total_a_favor = await elementos_td.nth(2).inner_text()
                
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
                        download_dir=download_dir,
                        error_logs=error_logs,
                    )
                    if pdf_path:
                        result["pdf_path"] = str(pdf_path)

                if movimientos:
                    result["movimientos"] = await _collect_all_ccma_movimientos(page1)
                    
            except Exception as e:
                error_logs.append(f"Error extrayendo datos de CCMA: {e}")
                await _safe_close(context, browser, page1=page1, base_page=page)
                return result, error_logs, False

            # Cierre ordenado
            await _safe_close(context, browser, page1=page1, base_page=page)

    except Exception as e:
        error_logs.append(f"No se pudo iniciar sesión en ARCA. Verifique los datos ingresados: {e}")
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
    download_dir: Path,
    error_logs: List[str],
) -> str:
    
    tmp_pdf_path = ""
    try:
        try:
            await page1.emulate_media(media="screen")
        except Exception:
            pass

        nombre_archivo_pdf = f"ccma_{cuit_representado}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
        tmp_pdf_path = download_dir / nombre_archivo_pdf

        await page1.pdf(path=tmp_pdf_path, format="A4", print_background=True)
        return tmp_pdf_path

    except Exception as exc:
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
