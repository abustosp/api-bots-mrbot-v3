import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

from shared.arca_login import (
    launch_arca_browser_context,
    login_arca,
    open_arca_service,
    wait_page_ready,
)
from app.utils.browser_limiter import limit_browser

try:
    from app.utils.proxies import get_proxy_config
except ImportError:
    get_proxy_config = None

SRT_ALICUOTAS_URL = "https://eservicios.srt.gob.ar/Consultas/Alicuotas/Default.aspx"

_NO_DATA_PATTERNS = (
    re.compile(r"no\s+tiene\s+afiliaci[oó]n\s+vigente", re.I),
    re.compile(r"no\s+registra\s+afiliaci[oó]n", re.I),
)

_SRT_SERVICE_MATCHERS = [
    re.compile(r"E-?SERVICIOS\s+SRT", re.I),
    re.compile(r"Sistema\s+de\s+Riesgos\s+del\s+Trabajo", re.I),
    "E-SERVICIOS SRT",
]

_TABLE_EXTRACT_JS = """
() => {
    const tables = document.querySelectorAll('table');
    const result = [];
    tables.forEach((table, idx) => {
        const captionEl = table.querySelector('caption');
        let title = captionEl ? captionEl.innerText.trim() : '';
        if (!title) {
            const prev = table.previousElementSibling;
            if (prev) title = prev.innerText.trim().split('\\n')[0] || '';
        }
        if (!title) title = 'tabla_' + (idx + 1);

        const headers = [];
        const headerCells = table.querySelectorAll('thead th, thead td, tr:first-child th');
        headerCells.forEach(th => headers.push(th.innerText.trim().replace(/\\s+/g, ' ')));

        const rows = [];
        const bodyRows = headers.length
            ? table.querySelectorAll('tbody tr')
            : table.querySelectorAll('tr');
        const startIdx = headers.length ? 0 : 1;

        bodyRows.forEach((tr, trIdx) => {
            if (!headers.length && trIdx === 0) return;
            const cells = [];
            tr.querySelectorAll('td, th').forEach(td => {
                cells.push(td.innerText.trim().replace(/\\s+/g, ' '));
            });
            if (cells.some(c => c !== '')) rows.push(cells);
        });

        if (rows.length > 0 || headers.length > 0) {
            result.push({ title, headers, rows });
        }
    });
    return result;
}
"""


async def _safe_close(browser=None, context=None) -> None:
    for obj in (context, browser):
        if obj is not None:
            try:
                await obj.close()
            except Exception:
                pass


async def _maybe_click_ingresar_temporal(page) -> bool:
    """Click temporal en 'Ingresar' de seleccion de representado (solo algunos contribuyentes)."""
    rep_candidates = [
        page.locator("a[onclick*='LoguearRespresentado']"),
        page.locator("a[onclick*='LoguearRepresentado']"),
        page.locator("a.btn-success[onclick*='Loguear']"),
    ]
    for candidate in rep_candidates:
        try:
            if await candidate.count() == 0:
                continue
            target = candidate.first
            try:
                await target.wait_for(state="visible", timeout=2_000)
            except Exception:
                continue
            await target.click(timeout=5_000)
            try:
                await page.wait_for_url(re.compile(r"Servicios\.aspx", re.I), timeout=10_000)
            except Exception:
                pass
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            except Exception:
                pass
            await page.wait_for_timeout(1000)
            return True
        except Exception:
            continue
    return False


async def _open_srt_query_page(srt_page, error_logs: List[str]) -> bool:
    """Navega a la página de consulta de alícuotas dentro del portal SRT."""
    try:
        # Esperar que cargue y buscar el link "Consulta de Alícuotas"
        await wait_page_ready(srt_page, timeout_ms=15_000)

        consulta_link = srt_page.locator(
            '[id="11"], a:has-text("Consulta de Alícuotas"), a:has-text("Consulta Alicuotas")'
        )
        try:
            if await consulta_link.count() > 0:
                await consulta_link.first.click(timeout=5_000)
                await wait_page_ready(srt_page)
                return True
        except Exception:
            pass

        # Fallback: navegar directamente a la URL
        await srt_page.goto(SRT_ALICUOTAS_URL, timeout=30_000)
        await wait_page_ready(srt_page)

        # Verificar que estamos en la página correcta
        if "Alicuotas" in srt_page.url or "CUIT" in await srt_page.content():
            return True

        error_logs.append(f"No se pudo abrir la página de consulta de alícuotas. URL: {srt_page.url}")
        return False

    except Exception as exc:
        error_logs.append(f"Error al abrir página de alícuotas SRT: {exc}")
        return False


async def _fill_srt_cuit(page, cuit: str, error_logs: List[str]) -> bool:
    """Llena el campo de CUIT en el formulario de consulta SRT."""
    selectors = [
        page.locator("#txtCuilCuit"),
        page.get_by_role("spinbutton", name=re.compile(r"CUIT/CUIL|CUIT|CUIL", re.I)),
        page.get_by_label(re.compile(r"CUIT/CUIL|Ingrese CUIT", re.I)),
        page.locator("input[aria-label*='CUIT' i]"),
        page.locator("input[placeholder*='CUIT' i]"),
        page.locator("input[id*='cuit' i], input[name*='cuit' i]"),
        page.locator("input[type='number']"),
    ]

    for locator in selectors:
        try:
            if await locator.count() == 0:
                continue
            target = locator.first
            await target.click(timeout=2_000)
            await target.fill(cuit, timeout=5_000)
            filled = await target.input_value()
            if filled.replace("-", "").replace(".", "") == cuit:
                return True
        except Exception:
            continue

    error_logs.append(f"No se pudo ingresar el CUIT {cuit} en el formulario SRT")
    return False


async def _click_consultar(page, error_logs: List[str]) -> bool:
    """Hace clic en el botón Consultar."""
    selectors = [
        page.locator("#btnConsultar"),
        page.get_by_role("button", name=re.compile(r"^Consultar$", re.I)),
        page.locator("input[type='submit'][value*='Consultar' i]"),
        page.locator("button:has-text('Consultar')"),
    ]

    for locator in selectors:
        try:
            if await locator.count() == 0:
                continue
            await locator.first.click(timeout=5_000)
            return True
        except Exception:
            continue

    error_logs.append("No se pudo hacer clic en el botón Consultar de SRT")
    return False


async def _clear_previous_result(page) -> None:
    """Limpia el resultado anterior cerrando modal y haciendo clic en Limpiar."""
    # Cerrar modal si está abierto
    for close_sel in [
        "#myModal button.close",
        "#myModal [data-dismiss='modal']",
        "button.close",
        "[data-dismiss='modal']",
    ]:
        try:
            loc = page.locator(close_sel)
            if await loc.count() > 0:
                await loc.first.click(timeout=1_500)
                await asyncio.sleep(0.3)
                break
        except Exception:
            pass

    # Hacer clic en Limpiar
    for clean_sel in [
        page.locator("#btnLimpiar"),
        page.get_by_role("button", name=re.compile(r"^Limpiar$", re.I)),
        page.locator("button:has-text('Limpiar')"),
    ]:
        try:
            if await clean_sel.count() > 0:
                await clean_sel.first.click(timeout=2_000)
                await asyncio.sleep(0.3)
                break
        except Exception:
            pass


async def _detect_and_solve_captcha(page, website_url: str, capmonster_cfg: dict, error_logs: List[str]) -> bool:
    """Detecta reCAPTCHA, lo resuelve con CapMonster e inyecta el token. Retorna True si ok."""
    sitekey = await detect_recaptcha_sitekey(page)
    if not sitekey:
        return True  # No hay captcha, continuar normalmente

    token = await solve_recaptcha_v2(
        api_key=capmonster_cfg["api_key"],
        website_url=website_url,
        sitekey=sitekey,
        timeout_s=capmonster_cfg["timeout_s"],
        poll_s=capmonster_cfg["poll_s"],
        task_type=capmonster_cfg["task_type"],
    )

    if not token:
        error_logs.append(f"No se pudo resolver el captcha (sitekey: {sitekey[:20]}...)")
        return False

    await inject_recaptcha_token(page, token)
    return True


async def _check_no_data(page) -> Optional[str]:
    """Detecta si la respuesta indica 'sin afiliación vigente'. Retorna el mensaje o None."""
    # Buscar en modal
    for modal_sel in ["#myModal .modal-body", "#myModal"]:
        try:
            loc = page.locator(modal_sel)
            if await loc.count() > 0:
                text = (await loc.first.inner_text(timeout=3_000) or "").strip()
                text = re.sub(r"\s+", " ", text)
                for pattern in _NO_DATA_PATTERNS:
                    if pattern.search(text):
                        return text
        except Exception:
            pass

    # Buscar en iframe de resultados
    try:
        iframe_loc = page.frame_locator("#ifrmResultadoPorCuit")
        body_text = await iframe_loc.locator("body").inner_text(timeout=3_000)
        body_text = re.sub(r"\s+", " ", body_text.strip())
        for pattern in _NO_DATA_PATTERNS:
            if pattern.search(body_text):
                return body_text
    except Exception:
        pass

    # Buscar en la página principal
    try:
        page_text = await page.locator("body").inner_text(timeout=2_000)
        page_text = re.sub(r"\s+", " ", page_text.strip())
        for pattern in _NO_DATA_PATTERNS:
            if pattern.search(page_text):
                return page_text[:300]
    except Exception:
        pass

    return None


async def _extract_tables_from_iframe(page) -> List[Dict[str, Any]]:
    """Extrae las tablas del iframe de resultados."""
    try:
        iframe_loc = page.frame_locator("#ifrmResultadoPorCuit")
        tables = await iframe_loc.locator("body").evaluate(_TABLE_EXTRACT_JS)
        return tables if tables else []
    except Exception:
        pass

    # Fallback: intentar extraer de la página principal
    try:
        tables = await page.evaluate(_TABLE_EXTRACT_JS)
        return tables if tables else []
    except Exception:
        return []


async def _wait_for_result(page, timeout_ms: int = 20_000) -> bool:
    """Espera a que aparezca el iframe de resultados o un mensaje de no datos."""
    try:
        await page.wait_for_selector(
            "#ifrmResultadoPorCuit, #myModal, [id*='Resultado' i]",
            timeout=timeout_ms,
        )
        return True
    except PlaywrightTimeoutError:
        return False


async def bot_srt_alicuotas(
    cuit_representante: str,
    clave_representante: str,
    cuits_consulta: List[str],
    proxy_request: bool = False,
) -> Tuple[Dict[str, Any], List[str], bool]:
    """
    Consulta alícuotas ART en SRT/ARCA para una lista de CUITs.

    Retorna (results_dict, error_logs, overall_success).
    overall_success = True si al menos 1 consulta OK y ninguna con error.
    """
    errors: List[str] = []
    consultas: List[Dict[str, Any]] = []
    capmonster_cfg = get_capmonster_config()

    proxy = None
    if proxy_request and get_proxy_config is not None:
        try:
            proxy = get_proxy_config()
        except Exception:
            pass

    browser = None
    context = None

    try:
        async with limit_browser(), async_playwright() as pw:
            browser, context, page = await launch_arca_browser_context(
                pw,
                headless=True,
                proxy=proxy,
            )

            # Login ARCA
            login_result = await login_arca(
                page,
                cuit_representante,
                clave_representante,
                error_logs=errors,
            )
            if not login_result.success:
                await _safe_close(browser, context)
                return {}, errors, False

            # Abrir servicio SRT
            srt_page = await open_arca_service(
                page,
                service_name="E-SERVICIOS SRT",
                service_matchers=_SRT_SERVICE_MATCHERS,
                error_logs=errors,
            )
            if srt_page is None:
                await _safe_close(browser, context)
                return {}, errors, False

            # Temporal: algunos contribuyentes piden seleccionar representado con 'Ingresar'
            await _maybe_click_ingresar_temporal(srt_page)

            # Navegar a la página de consulta de alícuotas
            ok = await _open_srt_query_page(srt_page, errors)
            if not ok:
                await _safe_close(browser, context)
                return {}, errors, False

            website_url = srt_page.url

            # Iterar CUITs secuencialmente — garantiza aislamiento
            for cuit in cuits_consulta:
                entry: Dict[str, Any] = {"cuit": cuit, "status": "ERROR", "message": None, "data": None}

                try:
                    await _clear_previous_result(srt_page)

                    # Ingresar CUIT
                    filled = await _fill_srt_cuit(srt_page, cuit, errors)
                    if not filled:
                        entry["message"] = f"No se pudo ingresar el CUIT {cuit}"
                        consultas.append(entry)
                        continue

                    # Detectar y resolver captcha
                    captcha_ok = await _detect_and_solve_captcha(
                        srt_page, website_url, capmonster_cfg, errors
                    )
                    if not captcha_ok:
                        entry["message"] = f"No se pudo resolver el captcha para CUIT {cuit}"
                        consultas.append(entry)
                        continue

                    # Consultar
                    clicked = await _click_consultar(srt_page, errors)
                    if not clicked:
                        entry["message"] = f"No se pudo ejecutar la consulta para CUIT {cuit}"
                        consultas.append(entry)
                        continue

                    # Esperar resultado
                    await _wait_for_result(srt_page)
                    await asyncio.sleep(1.0)

                    # Verificar "sin afiliación"
                    no_data_msg = await _check_no_data(srt_page)
                    if no_data_msg:
                        entry["status"] = "SIN_AFILIACION_VIGENTE"
                        entry["message"] = no_data_msg
                        entry["data"] = None
                        consultas.append(entry)

                        continue

                    # Extraer tablas
                    tables = await _extract_tables_from_iframe(srt_page)
                    if tables:
                        entry["status"] = "OK"
                        entry["message"] = None
                        entry["data"] = tables
                    else:
                        entry["status"] = "SIN_AFILIACION_VIGENTE"
                        entry["message"] = "No se encontraron datos de alícuotas"
                        entry["data"] = None

                    consultas.append(entry)

                except Exception as exc:
                    entry["status"] = "ERROR"
                    entry["message"] = str(exc)
                    entry["data"] = None
                    consultas.append(entry)
                    errors.append(f"CUIT {cuit}: {exc}")

                    # Intentar volver a la página de consulta para el siguiente CUIT
                    try:
                        await srt_page.goto(SRT_ALICUOTAS_URL, timeout=20_000)
                        await wait_page_ready(srt_page)
                    except Exception:
                        pass

        await _safe_close(browser, context)

    except Exception as exc:
        await _safe_close(browser, context)
        errors.append(f"Error general del bot SRT: {exc}")
        return {}, errors, False

    consultas_ok = sum(1 for c in consultas if c["status"] in ("OK", "SIN_AFILIACION_VIGENTE"))
    consultas_error = sum(1 for c in consultas if c["status"] == "ERROR")

    # success = al menos 1 OK y ningún ERROR (binario como el resto de los bots)
    overall_success = consultas_ok >= 1 and consultas_error == 0

    results = {
        "consultas": consultas,
        "consultas_ok": consultas_ok,
        "consultas_error": consultas_error,
    }

    return results, errors, overall_success
