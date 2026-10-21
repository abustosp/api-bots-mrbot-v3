"""
Bot retenciones AGIP (Ret/Per IIBB - Clave Ciudad) - variante xvfb (Xvfb).

Copia dev de `app/bot/retper_iibb_agip_bot_xvfb.py`: mismo flujo que el bot AGIP
headless (`retper_iibb_agip_bot.py`), pero lanzando Chromium con
`headless=False` sobre un display virtual (Xvfb) gestionado por
`app.utils.virtual_display.con_pantalla_virtual`.

Difiere de su hermano `retper_iibb_agip_bot_mrbot.py` solo en el lanzamiento del
navegador (headless=False + display virtual); conserva la ausencia de MinIO del
resto de los arneses dev.

Se usa cuando el request trae `pv=True` (ver `app/api/routes/retper_iibb_agip.py`).
Para correrlo desde la raiz del repo: `PYTHONPATH=. python -m ...` o importar
`bots_dev.agip.retper_iibb_agip_bot_xvfb`.
"""

import os
import tempfile
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser
from app.utils.virtual_display import con_pantalla_virtual


INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")


def _safe_filename_part(value: str, default: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    text = INVALID_FILENAME_RE.sub("", text)
    text = text.strip()
    return text or default


def _period_to_yyyymm(periodo: str) -> str:
    digits = re.sub(r"\D", "", periodo or "")
    if len(digits) < 6:
        raise ValueError(f"Periodo invalido: {periodo!r}. Debe ser AAAAMM.")
    return digits[:6]


def _yyyymm_to_mmyyyy(periodo_yyyymm: str) -> str:
    return f"{periodo_yyyymm[4:6]}/{periodo_yyyymm[:4]}"


def _parse_period_mmyyyy(periodo_mmyyyy: str) -> Tuple[int, int]:
    match = re.fullmatch(r"(\d{2})/(\d{4})", (periodo_mmyyyy or "").strip())
    if not match:
        raise ValueError(f"Periodo invalido: {periodo_mmyyyy!r}. Debe ser MM/AAAA.")
    month = int(match.group(1))
    year = int(match.group(2))
    if not 1 <= month <= 12:
        raise ValueError(f"Mes invalido en periodo: {periodo_mmyyyy!r}.")
    return month, year


def _build_download_name(
    cuit_representado: str,
    tipo: str,
    periodo_desde: str,
    periodo_hasta: str,
    denominacion: str,
    extension: str,
) -> str:
    cuit = re.sub(r"\D", "", cuit_representado or "")
    if not cuit:
        raise ValueError("CUIT representado invalido o vacio.")
    fin_cuit = cuit[-1]
    tipo_safe = _safe_filename_part(tipo, "SIN_TIPO")
    denominacion_safe = _safe_filename_part(denominacion, "SIN_DENOMINACION")
    return (
        f"{fin_cuit} - {cuit} - {tipo_safe} - "
        f"{periodo_desde} - {periodo_hasta} - "
        f"{denominacion_safe}.{extension}"
    )


def _service_selector(cuit_representado: str) -> str:
    cuit_digits = re.sub(r"\D", "", cuit_representado or "")
    if not cuit_digits:
        raise ValueError("CUIT representado invalido o vacio.")
    return f'#aplicaciones a[onclick*="ir_servicio(43,{cuit_digits})"]'


async def _set_period_textbox(
    page: Page,
    input_selector: str,
    periodo_mmyyyy: str,
    field_name: str,
) -> None:
    month, year = _parse_period_mmyyyy(periodo_mmyyyy)
    await page.wait_for_selector(input_selector, state="attached", timeout=30_000)
    result = await page.evaluate(
        """
        ({ selector, value, month, year }) => {
            const el = document.querySelector(selector);
            if (!el) {
                throw new Error(`No se encontro selector: ${selector}`);
            }
            const $ = window.jQuery || window.$;
            if (!$) {
                throw new Error("jQuery no esta disponible en la pagina");
            }
            const $el = $(el);
            const dateValue = new Date(year, month - 1, 1);
            el.removeAttribute("readonly");
            el.removeAttribute("disabled");
            if (typeof $el.datepicker === "function") {
                try { $el.datepicker("update", value); } catch (_) {}
                try { $el.datepicker("setDate", dateValue); } catch (_) {}
                try { $el.datepicker("hide"); } catch (_) {}
            }
            el.value = value;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            el.dispatchEvent(new Event("blur", { bubbles: true }));
            return { actual: el.value || null };
        }
        """,
        {"selector": input_selector, "value": periodo_mmyyyy, "month": month, "year": year},
    )
    if result.get("actual") != periodo_mmyyyy:
        raise RuntimeError(
            f"No se pudo setear {field_name}. esperado={periodo_mmyyyy} actual={result.get('actual')}"
        )


async def _set_period_filters(page: Page, desde_mmyyyy: str, hasta_mmyyyy: str) -> None:
    await _set_period_textbox(page, "#fechaDesdeCo", desde_mmyyyy, "desde")
    await _set_period_textbox(page, "#fechaHastaCo", hasta_mmyyyy, "hasta")
    values = await page.evaluate(
        """
        () => ({
            desde: document.querySelector("#fechaDesdeCo")?.value ?? null,
            hasta: document.querySelector("#fechaHastaCo")?.value ?? null,
        })
        """
    )
    if values.get("desde") != desde_mmyyyy:
        await _set_period_textbox(page, "#fechaDesdeCo", desde_mmyyyy, "desde_retry")
    if values.get("hasta") != hasta_mmyyyy:
        await _set_period_textbox(page, "#fechaHastaCo", hasta_mmyyyy, "hasta_retry")
    final_values = await page.evaluate(
        """
        () => ({
            desde: document.querySelector("#fechaDesdeCo")?.value ?? null,
            hasta: document.querySelector("#fechaHastaCo")?.value ?? null,
        })
        """
    )
    if final_values.get("desde") != desde_mmyyyy or final_values.get("hasta") != hasta_mmyyyy:
        raise RuntimeError(
            "AGIP no mantuvo los filtros del periodo antes de buscar. "
            f"desde={final_values.get('desde')} hasta={final_values.get('hasta')}"
        )


async def _open_login_gateway(page: Page) -> None:
    await page.get_by_role("button", name="Iniciar sesión").click()
    await page.wait_for_function(
        """
        () => location.href.includes('login.buenosaires.gob.ar')
            && Boolean(document.querySelector('#zocial-mail, #email'))
        """,
        timeout=60_000,
    )


async def _open_email_login(page: Page) -> None:
    username_selector = "#email"
    login_button_selector = "#zocial-mail"
    await page.wait_for_function(
        """
        () => Boolean(document.querySelector('#zocial-mail, #email'))
        """,
        timeout=30_000,
    )
    if await page.locator(username_selector).count() > 0:
        return
    login_button = page.locator(login_button_selector)
    if await login_button.count() > 0:
        await login_button.click()
        await page.wait_for_function(
            """
            () => Boolean(document.querySelector('#email'))
            """,
            timeout=30_000,
        )
        return
    await page.wait_for_timeout(2_000)
    if await page.locator(username_selector).count() > 0:
        return
    raise RuntimeError("No se pudo abrir el login con CUIL o email.")


async def _submit_login(page: Page) -> None:
    await page.get_by_role("button", name="Iniciar sesión").click()
    try:
        await page.wait_for_function(
            "() => document.querySelector('select#cuit_representado') !== null || (document.getElementById('error-password') !== null && document.getElementById('error-password').textContent.trim() !== '')",
            timeout=15_000,
        )
    except Exception:
        pass
    if page.url.startswith("https://claveciudad.agip.gob.ar/") or "menu_principal.php" in page.url:
        return
    error_text = await page.evaluate(
        """
        () => {
            const el = document.getElementById('error-password');
            if (el && el.textContent.trim()) return el.textContent.trim();
            return null;
        }
        """
    )
    if error_text:
        raise RuntimeError(f"Login fallido: {error_text}")
    raise RuntimeError("Login fallido: credenciales invalidas o usuario no encontrado")


async def _select_contributor(page: Page, cuit_representado: str) -> None:
    cuit_digits = re.sub(r"\D", "", cuit_representado or "")
    if not cuit_digits:
        raise ValueError("CUIT representado invalido o vacio.")
    await page.locator("select#cuit_representado").first.wait_for(state="visible", timeout=30_000)
    await page.locator("select#cuit_representado").first.select_option(value=cuit_digits)
    await page.evaluate(
        """
        target => {
            const select = document.querySelector("select#cuit_representado");
            if (!select) {
                throw new Error("No se encontro select#cuit_representado");
            }
            select.value = target;
            select.dispatchEvent(new Event("input", { bubbles: true }));
            select.dispatchEvent(new Event("change", { bubbles: true }));
            select.dispatchEvent(new Event("blur", { bubbles: true }));
            if (typeof window.aplicaciones_rapido_ajax === "function") {
                window.aplicaciones_rapido_ajax();
            }
        }
        """,
        cuit_digits,
    )
    service_selector = _service_selector(cuit_digits)
    await page.wait_for_function(
        """
        ({ target, selector }) => {
            const select = document.querySelector("select#cuit_representado");
            const aplicaciones = document.querySelector("#aplicaciones");
            const service = document.querySelector(selector);
            if (aplicaciones && service) {
                aplicaciones.style.display = "block";
                aplicaciones.style.opacity = "1";
            }
            return !!select && select.value === target && !!service;
        }
        """,
        arg={"target": cuit_digits, "selector": service_selector},
        timeout=60_000,
    )
    actual_value = await page.locator("select#cuit_representado").first.input_value()
    if actual_value != cuit_digits:
        raise RuntimeError(
            f"No se pudo seleccionar el representado. esperado={cuit_digits} actual={actual_value}"
        )


async def _open_service(page: Page, cuit_representado: str) -> None:
    service_selector = _service_selector(cuit_representado)
    await page.wait_for_selector(service_selector, state="attached", timeout=60_000)
    await page.evaluate(
        """
        selector => {
            const aplicaciones = document.querySelector("#aplicaciones");
            const link = document.querySelector(selector);
            if (!link) {
                throw new Error("No se encontro el servicio Gestion-AR Agtes. de Recaudacion");
            }
            if (aplicaciones) {
                aplicaciones.style.display = "block";
                aplicaciones.style.opacity = "1";
            }
            link.click();
        }
        """,
        service_selector,
    )
    await page.wait_for_selector("#fechaDesdeCo", state="attached", timeout=60_000)


_RESULT_EXCEL_BUTTONS = (
    "#tablaRetenciones_botonExcel",
    "#tablaPercepciones_botonExcel",
    "#tablaRetencionesBancarias_botonExcel",
    "#tablaPercepcionesAduaneras_botonExcel",
    "#tablaRetencionesSIRTAC_botonExcel",
)


async def _wait_for_results_ready(page: Page) -> str:
    """Espera el resultado de la búsqueda en AGIP.

    Devuelve "con_datos" cuando al menos un rubro muestra sus botones de
    descarga, o "sin_datos" cuando AGIP informa que el período no tiene
    registros (modal "No hay datos para el periodo seleccionado").
    """
    try:
        handle = await page.wait_for_function(
            """
            (selectors) => {
                const visible = (el) => {
                    if (!el) {
                        return false;
                    }
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                if (selectors.some((selector) => visible(document.querySelector(selector)))) {
                    return "con_datos";
                }
                const modal = document.querySelector("#myModalBody");
                if (modal && visible(modal) && (modal.textContent || "").includes("No hay datos")) {
                    return "sin_datos";
                }
                return null;
            }
            """,
            arg=list(_RESULT_EXCEL_BUTTONS),
            timeout=60_000,
        )
    except Exception as exc:
        raise RuntimeError(
            "AGIP no mostró resultados ni aviso de período sin datos. "
            "Es posible que la búsqueda no haya terminado de cargar."
        ) from exc
    estado = await handle.json_value()
    if estado not in ("con_datos", "sin_datos"):
        raise RuntimeError(f"Estado de resultados inesperado: {estado!r}.")
    return estado


async def _download_file(page: Page, trigger_selector: str, target_path: Path) -> bool:
    try:
        await page.wait_for_selector(trigger_selector, state="visible", timeout=30_000)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        async with page.expect_download(timeout=60_000) as download_info:
            await page.locator(trigger_selector).click(force=True)
        download = await download_info.value
        await download.save_as(str(target_path))
        return True
    except Exception:
        return False


SECTIONS = [
    {
        "tab_text": "Retenciones",
        "files": [
            ("#tablaRetenciones_botonExcel", "RET", "xls"),
            ("#tablaRetenciones_botonTxt", "RET", "txt"),
            ("#tablaRetenciones_botonTxtSifere", "RET SIFERE", "txt"),
        ],
    },
    {
        "tab_text": "Percepciones",
        "files": [
            ("#tablaPercepciones_botonExcel", "PER", "xls"),
            ("#tablaPercepciones_botonTxt", "PER", "txt"),
            ("#tablaPercepciones_botonTxtSifere", "PER SIFERE", "txt"),
        ],
    },
    {
        "tab_text": "Retenciones Bancarias",
        "files": [
            ("#tablaRetencionesBancarias_botonExcel", "RET bancarias", "xls"),
            ("#tablaRetencionesBancarias_botonTxt", "RET bancarias", "txt"),
            ("#tablaRetencionesBancarias_botonTxtSifere", "RET bancarias SIFERE", "txt"),
        ],
    },
    {
        "tab_text": "Percepciones Aduaneras",
        "files": [
            ("#tablaPercepcionesAduaneras_botonExcel", "PER ADUANERAS", "xls"),
            ("#tablaPercepcionesAduaneras_botonTxt", "PER ADUANERAS", "txt"),
            ("#tablaPercepcionesAduaneras_botonTxtSifere", "PER ADUANERAS SIFERE", "txt"),
        ],
    },
    {
        "tab_text": "Retenciones SIRTAC",
        "files": [
            ("#tablaRetencionesSIRTAC_botonExcel", "RET SIRTAC", "xls"),
            ("#tablaRetencionesSIRTAC_botonTxt", "RET SIRTAC", "txt"),
            ("#tablaRetencionesSIRTAC_botonTxtSifere", "RET SIRTAC SIFERE", "txt"),
        ],
    },
]


@con_pantalla_virtual
async def bot_retper_iibb_agip_xvfb(
    usuario: str,
    clave: str,
    cuit_representado: str,
    denominacion: str,
    desde: str,
    hasta: str,

    proxy_request: bool = False,
) -> Tuple[Dict[str, Any], List[str], bool]:
    """Igual que el bot AGIP headless, pero con interfaz sobre pantalla virtual.

    No expone `headless`: siempre lanza Chromium con `headless=False` dentro de
    un Xvfb efimero (gestionado por `con_pantalla_virtual`).
    """
    results: Dict[str, Any] = {"archivos": []}
    error_logs: List[str] = []
    success = True

    cuit_digits = re.sub(r"\D", "", cuit_representado or "")
    if len(cuit_digits) != 11:
        return results, ["CUIT invalido para Ret/Per IIBB AGIP"], False

    if not (usuario or "").strip():
        return results, ["El usuario es obligatorio"], False

    if not (clave or "").strip():
        return results, ["La clave es obligatoria"], False

    if not (denominacion or "").strip():
        return results, ["La denominacion es obligatoria"], False

    period_desde = _period_to_yyyymm(desde)
    period_hasta = _period_to_yyyymm(hasta)
    period_desde_mmyyyy = _yyyymm_to_mmyyyy(period_desde)
    period_hasta_mmyyyy = _yyyymm_to_mmyyyy(period_hasta)

    proxy = get_proxy_config() if proxy_request else None

    browser: Optional[Browser] = None
    context: Optional[BrowserContext] = None

    with tempfile.TemporaryDirectory(prefix="retper_iibb_agip_") as temp_dir:
        temp_path = Path(temp_dir)

        try:
            async with limit_browser(), async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=False,
                    proxy=proxy,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                    ],
                )
                context = await browser.new_context(
                    locale="es-AR",
                    timezone_id="America/Argentina/Buenos_Aires",
                    accept_downloads=True,
                )
                page = await context.new_page()
                page.set_default_timeout(60_000)

                await page.goto("https://claveciudad.agip.gob.ar/")
                await _open_login_gateway(page)
                await _open_email_login(page)
                await page.locator("#email").fill(usuario)
                await page.locator("#password-text-field").fill(clave)
                await _submit_login(page)
                await _select_contributor(page, cuit_digits)
                await _open_service(page, cuit_digits)
                await _set_period_filters(page, period_desde_mmyyyy, period_hasta_mmyyyy)
                await page.get_by_role("button", name="Buscar").click()
                estado_resultados = await _wait_for_results_ready(page)
                if estado_resultados == "sin_datos":
                    error_logs.append(
                        "AGIP informó que no hay datos para el período "
                        f"{period_desde}-{period_hasta}."
                    )
                    return results, error_logs, False

                for section in SECTIONS:
                    tab_link = page.locator("#divConsultado").get_by_text(
                        section["tab_text"], exact=True
                    )
                    tab_visible = False
                    try:
                        tab_visible = (
                            await tab_link.count() > 0
                            and await tab_link.first.is_visible()
                        )
                    except Exception:
                        continue

                    if not tab_visible:
                        continue

                    await tab_link.first.click()
                    await page.wait_for_timeout(1_000)

                    for selector, tipo, extension in section["files"]:
                        filename = _build_download_name(
                            cuit_representado=cuit_digits,
                            tipo=tipo,
                            periodo_desde=period_desde,
                            periodo_hasta=period_hasta,
                            denominacion=denominacion,
                            extension=extension,
                        )
                        file_path = temp_path / filename
                        ok = await _download_file(page, selector, file_path)
                        if ok:
                            results["archivos"].append(
                                {
                                    "tipo": tipo,
                                    "archivo": filename,

                                }
                            )
                        else:
                            section_label = section["tab_text"]
                            error_logs.append(
                                f"No se pudo descargar {tipo}.{extension} "
                                f"de la seccion {section_label}"
                            )

        except Exception as exc:
            error_logs.append(f"Error en bot Ret/Per IIBB AGIP: {exc}")
            success = False
        finally:
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

        if not results["archivos"]:
            if not error_logs:
                error_logs.append("No se descargaron archivos de Ret/Per IIBB AGIP")
            return results, error_logs, False

    return results, error_logs, success
