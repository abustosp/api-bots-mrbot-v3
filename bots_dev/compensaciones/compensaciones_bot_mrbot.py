import asyncio
import os
import tempfile
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from playwright.async_api import Locator, Page, async_playwright

from shared.arca_login import login_arca, open_arca_service
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser

COMPENSACIONES_VUE_URL = (
    "https://ctacte.cloud.afip.gob.ar/contribuyente/scripts/vue/consultaCompensacionesAfectaciones/index.html"
)


def _safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "_", value or "")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return cleaned or "archivo"


def _normalize_digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


async def _safe_close(context, browser) -> None:
    if context is not None:
        try:
            await context.close()
        except Exception:
            pass
    if browser is not None:
        try:
            await browser.close()
        except Exception:
            pass


async def _find_first_visible(
    candidates: list[Locator],
    wait_timeout_ms: int = 1500,
    visible_timeout_ms: int = 500,
    max_items: int = 10,
) -> Optional[Locator]:
    for candidate_group in candidates:
        try:
            count = await candidate_group.count()
            if count == 0:
                continue
            for idx in range(min(count, max_items)):
                element = candidate_group.nth(idx)
                try:
                    await element.wait_for(state="visible", timeout=wait_timeout_ms)
                except Exception:
                    pass
                if await element.is_visible(timeout=visible_timeout_ms):
                    return element
        except Exception:
            continue
    return None


async def _click_menu_item_by_href_keywords(
    page: Page,
    *,
    keywords: tuple[str, ...],
    max_links: int = 140,
) -> bool:
    keywords_norm = tuple(k.lower() for k in keywords)
    links = page.locator("a[href*='service=partial'][href*='panelMenu.$PartialLink']")
    try:
        count = await links.count()
    except Exception:
        return False

    for idx in range(min(count, max_links)):
        link = links.nth(idx)
        try:
            href = (await link.get_attribute("href") or "").strip()
            normalized_href = unquote(href).replace("+", " ").lower()
            if not all(token in normalized_href for token in keywords_norm):
                continue
            await link.evaluate("el => el.click()")
            await asyncio.sleep(0.8)
            return True
        except Exception:
            continue
    return False


async def _select_representado(page: Page, cuit_representado: str, *, error_logs: list[str]) -> None:
    try:
        combobox = page.locator('select[name="$PropertySelection"]')
        if await combobox.count() == 0:
            return

        await combobox.wait_for(state="visible", timeout=3000)
        opciones = combobox.locator("option")
        count = await opciones.count()
        objetivo = (cuit_representado or "").strip()
        objetivo_digits = _normalize_digits(objetivo)
        selected = False
        for i in range(count):
            text = (await opciones.nth(i).text_content() or "").strip()
            value = (await opciones.nth(i).get_attribute("value") or "").strip()
            text_digits = _normalize_digits(text)
            value_digits = _normalize_digits(value)
            if (
                text == objetivo
                or value == objetivo
                or (objetivo_digits and text_digits == objetivo_digits)
                or (objetivo_digits and value_digits == objetivo_digits)
            ):
                await combobox.select_option(value=value)
                selected = True
                break

        if not selected:
            error_logs.append(
                f"No se pudo seleccionar el representado. Verifique el CUIT {cuit_representado}. "
                "Continuando con la seleccion actual."
            )
    except Exception as exc:
        error_logs.append(f"Excepcion al seleccionar representado (continuando): {exc}")


async def _wait_compensaciones_ready(page: Page, timeout_ms: int = 35000) -> bool:
    deadline = time.monotonic() + (timeout_ms / 1000)
    while time.monotonic() < deadline:
        try:
            fecha_inputs = page.locator("input[name='fecha']")
            if await fecha_inputs.count() >= 2 and await fecha_inputs.first.is_visible(timeout=300):
                return True
        except Exception:
            pass

        try:
            consultar = page.get_by_role("button", name=re.compile(r"CONSULTAR", re.I))
            if await consultar.count() > 0 and await consultar.first.is_visible(timeout=300):
                return True
        except Exception:
            pass

        await asyncio.sleep(0.4)

    return False


async def _open_compensaciones_submodule(page: Page, *, error_logs: list[str]) -> bool:
    opened_from_menu = False

    consultas_locator = await _find_first_visible(
        [
            page.get_by_role("cell", name=re.compile(r"Consultas", re.I)),
            page.get_by_role("link", name=re.compile(r"Consultas", re.I)),
            page.get_by_role("button", name=re.compile(r"Consultas", re.I)),
            page.locator("td", has_text=re.compile(r"Consultas", re.I)),
            page.locator("a", has_text=re.compile(r"Consultas", re.I)),
            page.get_by_text(re.compile(r"Consultas", re.I)),
        ]
    )
    if consultas_locator is None:
        clicked = await _click_menu_item_by_href_keywords(page, keywords=("consultas",))
        opened_from_menu = clicked
    else:
        try:
            await consultas_locator.click()
            opened_from_menu = True
        except Exception:
            try:
                await consultas_locator.evaluate("el => el.click()")
                opened_from_menu = True
            except Exception:
                clicked = await _click_menu_item_by_href_keywords(page, keywords=("consultas",))
                opened_from_menu = clicked

    await asyncio.sleep(0.7)

    compensaciones_locator = await _find_first_visible(
        [
            page.get_by_role("cell", name=re.compile(r"Consulta\s+Compensaciones", re.I)),
            page.get_by_role("link", name=re.compile(r"Consulta\s+Compensaciones", re.I)),
            page.get_by_role("button", name=re.compile(r"Consulta\s+Compensaciones", re.I)),
            page.locator("td", has_text=re.compile(r"Consulta\s+Compensaciones", re.I)),
            page.locator("a", has_text=re.compile(r"Consulta\s+Compensaciones", re.I)),
            page.get_by_text(re.compile(r"Consulta\s+Compensaciones", re.I)),
        ],
        wait_timeout_ms=3000,
    )
    if compensaciones_locator is None:
        clicked = await _click_menu_item_by_href_keywords(
            page,
            keywords=("consulta", "compensaciones", "afectaciones"),
        )
        opened_from_menu = opened_from_menu or clicked
    else:
        try:
            await compensaciones_locator.click()
            opened_from_menu = True
        except Exception:
            try:
                await compensaciones_locator.evaluate("el => el.click()")
                opened_from_menu = True
            except Exception:
                clicked = await _click_menu_item_by_href_keywords(
                    page,
                    keywords=("consulta", "compensaciones", "afectaciones"),
                )
                opened_from_menu = opened_from_menu or clicked

    await asyncio.sleep(1)

    try:
        await page.goto(COMPENSACIONES_VUE_URL, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
    except Exception as exc:
        error_logs.append(f"No se pudo abrir el submodulo de Consulta Compensaciones: {exc}")
        return False

    ready = await _wait_compensaciones_ready(page)
    if not ready:
        if opened_from_menu:
            error_logs.append("No se pudo cargar la pantalla de Consulta Compensaciones y Afectaciones")
        else:
            error_logs.append("No se pudo abrir la seccion Consultas en Sistema de Cuentas")
        return False

    return True


async def _fill_filters_and_consult(
    page: Page,
    *,
    desde: str,
    hasta: str,
    error_logs: list[str],
) -> bool:
    try:
        fecha_inputs = page.locator("input[name='fecha']")
        input_count = await fecha_inputs.count()
        if input_count >= 2:
            await fecha_inputs.nth(0).click()
            await fecha_inputs.nth(0).fill(desde)
            await fecha_inputs.nth(1).click()
            await fecha_inputs.nth(1).fill(hasta)
        else:
            desde_input = await _find_first_visible(
                [
                    page.get_by_role("textbox", name=re.compile(r"Fecha\s+Desde", re.I)),
                    page.locator("input[placeholder*='Desde' i], input[name*='desde' i], input[id*='desde' i]"),
                ],
                wait_timeout_ms=3000,
            )
            hasta_input = await _find_first_visible(
                [
                    page.get_by_role("textbox", name=re.compile(r"Fecha\s+Hasta", re.I)),
                    page.locator("input[placeholder*='Hasta' i], input[name*='hasta' i], input[id*='hasta' i]"),
                ],
                wait_timeout_ms=3000,
            )
            if desde_input is None or hasta_input is None:
                error_logs.append("No se encontraron los campos de fecha en Compensaciones")
                return False

            await desde_input.click()
            await desde_input.fill(desde)
            await hasta_input.click()
            await hasta_input.fill(hasta)

        consultar_btn = await _find_first_visible(
            [
                page.get_by_role("button", name=re.compile(r"CONSULTAR", re.I)),
                page.get_by_role("link", name=re.compile(r"CONSULTAR", re.I)),
                page.locator("button:has-text('CONSULTAR')"),
                page.locator("a:has-text('CONSULTAR')"),
            ],
            wait_timeout_ms=3000,
        )
        if consultar_btn is None:
            error_logs.append("No se encontro el boton CONSULTAR en Compensaciones")
            return False

        await consultar_btn.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass
        await asyncio.sleep(1.2)
        return True
    except Exception as exc:
        error_logs.append(f"Error al completar filtros/consulta en Compensaciones: {exc}")
        return False


async def _find_export_button(scope) -> Optional[Locator]:
    return await _find_first_visible(
        [
            scope.get_by_role("button", name=re.compile(r"Exportar", re.I)),
            scope.get_by_role("link", name=re.compile(r"Exportar", re.I)),
            scope.locator("button:has-text('Exportar')"),
            scope.locator("a:has-text('Exportar')"),
            scope.locator("div.buttons.bt-imp"),
        ],
        wait_timeout_ms=1800,
    )


async def _find_export_option(scope, fmt: str) -> Optional[Locator]:
    fmt_regex = re.compile(rf"\b{re.escape(fmt)}\b", re.I)
    return await _find_first_visible(
        [
            scope.get_by_role("button", name=fmt_regex),
            scope.get_by_role("link", name=fmt_regex),
            scope.locator(f"button:has-text('{fmt}')"),
            scope.locator(f"a:has-text('{fmt}')"),
            scope.get_by_text(fmt_regex),
        ],
        wait_timeout_ms=1200,
    )


async def _has_no_results_message(page: Page) -> bool:
    no_results = await _find_first_visible(
        [
            page.locator("div.d-empty h4", has_text=re.compile(r"No\s+se\s+encontraron\s+resultados", re.I)),
            page.get_by_role("heading", name=re.compile(r"No\s+se\s+encontraron\s+resultados", re.I)),
            page.get_by_text(re.compile(r"No\s+se\s+encontraron\s+resultados", re.I)),
        ],
        wait_timeout_ms=1200,
        visible_timeout_ms=600,
        max_items=4,
    )
    return no_results is not None


async def _download_format_to_temp(
    page: Page,
    download_dir: Path,
    *,
    fmt: str,
    error_logs: list[str],
) -> Optional[Path]:
    try:
        export_btn = await _find_export_button(page)
        if export_btn is None:
            return None

        await export_btn.click()
        await asyncio.sleep(0.35)

        export_option = await _find_export_option(page, fmt)
        if export_option is None:
            return None

        async with page.expect_download(timeout=30000) as dlinfo:
            await export_option.click()
        download = await dlinfo.value

        filename = _safe_filename(f"compensaciones_{fmt.lower()}_{download.suggested_filename}")
        filepath = download_dir / filename
        await download.save_as(str(filepath))
        return filepath
    except Exception as exc:
        error_logs.append(f"Error descargando formato {fmt}: {exc}")
        return None


async def bot_compensaciones(
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
    desde: str,
    hasta: str,
    *,
    excel: bool = False,
    csv: bool = False,
    pdf: bool = False,
    proxy_request: bool = False,
    headless: bool = True,
) -> tuple[dict[str, str], list[str], bool]:
    results: dict[str, str] = {}
    error_logs: list[str] = []
    success = True

    requested_formats = {


    }
    if not any(flag for flag, _ in requested_formats.values()):
        error_logs.append("Debe seleccionar al menos un formato de salida (Excel, CSV o PDF)")
        return results, error_logs, False

    proxy = get_proxy_config() if proxy_request else None

    browser = None
    context = None

    try:
        with tempfile.TemporaryDirectory(prefix="compensaciones_") as temp_dir:
            temp_download_dir = Path(temp_dir)

            async with limit_browser(), async_playwright() as pw:
                browser = await pw.chromium.launch(headless=headless, proxy=proxy)
                context = await browser.new_context(accept_downloads=True)
                page = await context.new_page()

                login_result = await login_arca(
                    page,
                    cuit_representante,
                    clave_representante,
                    error_logs=error_logs,
                )
                if not login_result.success:
                    return results, error_logs, False

                page1 = await open_arca_service(
                    page,
                    service_name="SISTEMA DE CUENTAS",
                    service_matchers=(re.compile("SISTEMA DE CUENTAS", re.I),),
                    error_logs=error_logs,
                    ver_todos_error_message="No se pudo desplegar 'Ver todos' en ARCA",
                    service_unavailable_message="No se encuentra habilitado el servicio de Compensaciones",
                )
                if page1 is None:
                    return results, error_logs, False

                if page1 is not page and not page.is_closed():
                    await page.close()
                try:
                    await page1.wait_for_load_state("networkidle")
                except Exception:
                    pass

                if "auth.afip.gob.ar/contribuyente_/login.xhtml" in (page1.url or ""):
                    relogin_result = await login_arca(
                        page1,
                        cuit_representante,
                        clave_representante,
                        error_logs=error_logs,
                    )
                    if not relogin_result.success:
                        return results, error_logs, False

                    if "ctacte.cloud.afip.gob.ar/contribuyente/externo" not in (page1.url or ""):
                        reopened_page = await open_arca_service(
                            page1,
                            service_name="SISTEMA DE CUENTAS",
                            service_matchers=(re.compile("SISTEMA DE CUENTAS", re.I),),
                            error_logs=error_logs,
                            ver_todos_error_message="No se pudo desplegar 'Ver todos' en ARCA",
                            service_unavailable_message="No se encuentra habilitado el servicio de Compensaciones",
                        )
                        if reopened_page is None:
                            return results, error_logs, False
                        page1 = reopened_page
                    try:
                        await page1.wait_for_load_state("networkidle")
                    except Exception:
                        pass

                await _select_representado(page1, cuit_representado, error_logs=error_logs)

                submodule_opened = await _open_compensaciones_submodule(page1, error_logs=error_logs)
                if not submodule_opened:
                    return results, error_logs, False

                consulted = await _fill_filters_and_consult(
                    page1,
                    desde=desde,
                    hasta=hasta,
                    error_logs=error_logs,
                )
                if not consulted:
                    return results, error_logs, False

                if await _has_no_results_message(page1):
                    results["message"] = "No se encontraron resultados"
                    return results, error_logs, True

                for fmt, (requested, result_key) in requested_formats.items():
                    if not requested:
                        continue

                    downloaded_path = await _download_format_to_temp(
                        page1,
                        temp_download_dir,
                        fmt=fmt,
                        error_logs=error_logs,
                    )
                    if downloaded_path is None:
                        continue

    except Exception as exc:
        error_logs.append(f"Error global en Compensaciones: {exc}")
        success = False
    finally:
        await _safe_close(context, browser)

    if not results:
        success = False

    return results, error_logs, success
