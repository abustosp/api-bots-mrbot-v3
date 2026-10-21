import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Optional

from dotenv import load_dotenv
from playwright.async_api import Page, Playwright, async_playwright

BOT_NAME = "agip"


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def _safe_filename_part(value: str, default: str) -> str:
    text = re.sub(r"\s+", " ", (value or "").strip())
    text = re.sub(r'[\\/:*?"<>|]+', "", text)
    text = text.strip()
    return text or default


def _period_to_yyyymm(periodo: str) -> str:
    digits = re.sub(r"\D", "", periodo or "")
    if len(digits) < 6:
        raise ValueError(f"Periodo invalido: {periodo!r}. Debe ser AAAAMM.")
    return digits[:6]


def _yyyymm_to_mmyyyy(periodo_yyyymm: str) -> str:
    return f"{periodo_yyyymm[4:6]}/{periodo_yyyymm[:4]}"


def _parse_period_mmyyyy(periodo_mmyyyy: str) -> tuple[int, int]:
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



class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "agip") -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"{prefix}_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.descargas_dir = self.run_dir / "descargas"
        self.html_dir = self.run_dir / "html"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.har_dir = self.run_dir / "har"

        for path in (
            self.logs_dir,
            self.descargas_dir,
            self.html_dir,
            self.screenshots_dir,
            self.har_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            file_handler = logging.FileHandler(self.logs_dir / f"{prefix}.log")
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
            self.logger.propagate = False

        self.step = 0
        self.log(f"run_dir={self.run_dir}")

    def log(self, message: str) -> None:
        self.logger.info("%s", message)

    async def capture(self, page: Optional[Page], step_name: str, result: str) -> None:
        if page is None:
            self.log(f"capture_skipped step={step_name} page=None result={result}")
            return

        self.step += 1
        file_prefix = f"{self.step:03d}_{_safe_step_name(step_name)}"
        screenshot_path = self.screenshots_dir / f"{file_prefix}.png"
        html_path = self.html_dir / f"{file_prefix}.html"

        self.log(
            f"step={self.step} name={step_name} result={result} url={getattr(page, 'url', '')}"
        )

        try:
            await page.screenshot(path=str(screenshot_path), full_page=True, timeout=30_000)
            self.log(f"screenshot={screenshot_path}")
        except Exception as exc:
            self.log(f"screenshot_failed step={step_name} error={exc}")

        try:
            html_path.write_text(await page.content(), encoding="utf-8")
            self.log(f"html={html_path}")
        except Exception as exc:
            self.log(f"html_failed step={step_name} error={exc}")


async def _run_step(
    debug: DebugCapture,
    page: Optional[Page],
    step_name: str,
    action: Callable[[], Awaitable[None]],
) -> None:
    debug.log(f"step_start={step_name}")
    try:
        await action()
        debug.log(f"step_end={step_name} status=OK")
        await debug.capture(page, step_name, "OK")
    except Exception as exc:
        debug.log(f"step_end={step_name} status=ERROR error={exc}")
        await debug.capture(page, f"{step_name}_error", f"ERROR: {exc}")
        raise


async def _download_with_log(
    page: Page,
    debug: DebugCapture,
    step_name: str,
    trigger_selector: str,
    target_path: Path,
) -> None:
    async def _action() -> None:
        await page.wait_for_selector(trigger_selector, state="visible", timeout=30_000)
        target_path.parent.mkdir(parents=True, exist_ok=True)
        async with page.expect_download(timeout=60_000) as download_info:
            await page.locator(trigger_selector).click()
        download = await download_info.value
        await download.save_as(str(target_path))

        debug.log(f"download_saved step={step_name} path={target_path}")

    await _run_step(debug, page, step_name, _action)


async def _set_period_textbox(
    page: Page,
    debug: DebugCapture,
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
            const methods = [];
            const errors = [];
            const dateValue = new Date(year, month - 1, 1);

            el.removeAttribute("readonly");
            el.removeAttribute("disabled");

            if (typeof $el.datepicker === "function") {
                try {
                    $el.datepicker("update", value);
                    methods.push("datepicker_update");
                } catch (error) {
                    errors.push(`update=${String(error)}`);
                }

                try {
                    $el.datepicker("setDate", dateValue);
                    methods.push("datepicker_setDate");
                } catch (error) {
                    errors.push(`setDate=${String(error)}`);
                }

                try {
                    $el.datepicker("hide");
                    methods.push("datepicker_hide");
                } catch (error) {
                    errors.push(`hide=${String(error)}`);
                }
            } else {
                errors.push("datepicker_missing");
            }

            el.value = value;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            el.dispatchEvent(new Event("blur", { bubbles: true }));

            return {
                actual: el.value || null,
                method: methods.join("+") || "dom_value_only",
                errors,
            };
        }
        """,
        {
            "selector": input_selector,
            "value": periodo_mmyyyy,
            "month": month,
            "year": year,
        },
    )
    debug.log(
        f"periodo_{field_name}_set expected={periodo_mmyyyy} "
        f"actual={result.get('actual')} method={result.get('method')} "
        f"errors={result.get('errors')}"
    )
    if result.get("actual") != periodo_mmyyyy:
        raise RuntimeError(
            f"No se pudo setear {field_name}. esperado={periodo_mmyyyy} actual={result.get('actual')}"
        )


async def _set_period_filters(
    page: Page,
    debug: DebugCapture,
    desde_mmyyyy: str,
    hasta_mmyyyy: str,
) -> None:
    await _set_period_textbox(page, debug, "#fechaDesdeCo", desde_mmyyyy, "desde")
    await _set_period_textbox(page, debug, "#fechaHastaCo", hasta_mmyyyy, "hasta")

    values = await page.evaluate(
        """
        () => ({
            desde: document.querySelector("#fechaDesdeCo")?.value ?? null,
            hasta: document.querySelector("#fechaHastaCo")?.value ?? null,
        })
        """
    )
    if values.get("desde") != desde_mmyyyy:
        await _set_period_textbox(page, debug, "#fechaDesdeCo", desde_mmyyyy, "desde_retry")
    if values.get("hasta") != hasta_mmyyyy:
        await _set_period_textbox(page, debug, "#fechaHastaCo", hasta_mmyyyy, "hasta_retry")

    final_values = await page.evaluate(
        """
        () => ({
            desde: document.querySelector("#fechaDesdeCo")?.value ?? null,
            hasta: document.querySelector("#fechaHastaCo")?.value ?? null,
        })
        """
    )
    debug.log(
        f"periodos_finales desde={final_values.get('desde')} hasta={final_values.get('hasta')}"
    )
    if final_values.get("desde") != desde_mmyyyy or final_values.get("hasta") != hasta_mmyyyy:
        raise RuntimeError(
            "AGIP no mantuvo los filtros del periodo antes de buscar. "
            f"desde={final_values.get('desde')} hasta={final_values.get('hasta')}"
        )


async def _open_login_gateway(page: Page, debug: DebugCapture) -> None:
    await page.get_by_role("button", name="Iniciar sesión").click()
    await page.wait_for_function(
        """
        () => location.href.includes('login.buenosaires.gob.ar')
            && Boolean(document.querySelector('#zocial-mail, #email'))
        """,
        timeout=60_000,
    )
    debug.log("login_gateway_ready=true")


async def _open_email_login(page: Page, debug: DebugCapture) -> None:
    username_selector = "#email"
    login_button_selector = "#zocial-mail"

    await page.wait_for_function(
        """
        () => Boolean(document.querySelector('#zocial-mail, #email'))
        """,
        timeout=30_000,
    )

    if await page.locator(username_selector).count() > 0:
        debug.log("login_cuil_email variant=form_directo")
        return

    login_button = page.locator(login_button_selector)
    if await login_button.count() > 0:
        debug.log("login_cuil_email variant=boton_intermedio")
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
        debug.log("login_cuil_email variant=form_post_load")
        return

    raise RuntimeError("No se pudo abrir el login con CUIL o email.")


async def _submit_login(page: Page, debug: DebugCapture) -> None:
    await page.get_by_role("button", name="Iniciar sesión").click()
    try:
        await page.wait_for_function(
            "() => document.querySelector('select#cuit_representado') !== null || (document.getElementById('error-password') !== null && document.getElementById('error-password').textContent.trim() !== '')",
            timeout=15_000,
        )
    except Exception:
        pass
    if page.url.startswith("https://claveciudad.agip.gob.ar/") or "menu_principal.php" in page.url:
        debug.log("login_exitoso=true")
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


async def _select_contributor(page: Page, debug: DebugCapture, cuit_representado: str) -> None:
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
    debug.log(f"representado_seleccionado expected={cuit_digits} actual={actual_value}")
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


async def bot_agip(
    playwright: Playwright,
    usuario: str,
    clave: str,
    cuit_representado: str,
    denominacion: str,
    desde: str,
    hasta: str,
    headless: bool = True,
) -> None:
    debug = DebugCapture(prefix=BOT_NAME)
    period_desde = _period_to_yyyymm(desde)
    period_hasta = _period_to_yyyymm(hasta)
    period_desde_mmyyyy = _yyyymm_to_mmyyyy(period_desde)
    period_hasta_mmyyyy = _yyyymm_to_mmyyyy(period_hasta)
    har_path = debug.har_dir / f"{BOT_NAME}.har"

    browser = None
    context = None
    page: Optional[Page] = None

    try:
        debug.log("Iniciando bot AGIP")
        browser = await playwright.chromium.launch(
            headless=headless,
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
            record_har_path=str(har_path),
            record_har_content="embed",
        )
        page = await context.new_page()
        page.set_default_timeout(60_000)
        await debug.capture(page, "inicio", "OK")

        await _run_step(debug, page, "abrir_portal_agip", lambda: page.goto("https://claveciudad.agip.gob.ar/"))
        await _run_step(
            debug,
            page,
            "click_iniciar_sesion",
            lambda: _open_login_gateway(page, debug),
        )
        await _run_step(debug, page, "abrir_login_cuil_email", lambda: _open_email_login(page, debug))
        await _run_step(
            debug,
            page,
            "completar_usuario",
            lambda: page.locator("#email").fill(usuario),
        )
        await _run_step(
            debug,
            page,
            "completar_contrasena",
            lambda: page.locator("#password-text-field").fill(clave),
        )
        await _run_step(
            debug,
            page,
            "submit_login",
            lambda: _submit_login(page, debug),
        )
        await _run_step(
            debug,
            page,
            "seleccionar_cuit_representado",
            lambda: _select_contributor(page, debug, cuit_representado),
        )
        await _run_step(
            debug,
            page,
            "abrir_servicio_gestion_ar_agtes",
            lambda: _open_service(page, cuit_representado),
        )
        await _run_step(
            debug,
            page,
            "cargar_periodos",
            lambda: _set_period_filters(page, debug, period_desde_mmyyyy, period_hasta_mmyyyy),
        )
        await _run_step(
            debug,
            page,
            "buscar_resultados",
            lambda: page.get_by_role("button", name="Buscar").click(),
        )
        estado_resultados: dict = {}

        async def _esperar_resultados() -> None:
            estado_resultados["estado"] = await _wait_for_results_ready(page)

        await _run_step(debug, page, "esperar_resultados", _esperar_resultados)

        sections = [
            {
                "tab_text": "Retenciones",
                "tab_step": "ir_a_retenciones",
                "files": [
                    ("retenciones_excel", "#tablaRetenciones_botonExcel", "RET", "xls"),
                    ("retenciones_txt", "#tablaRetenciones_botonTxt", "RET", "txt"),
                    ("retenciones_txt_sifere", "#tablaRetenciones_botonTxtSifere", "RET SIFERE", "txt"),
                ],
            },
            {
                "tab_text": "Percepciones",
                "tab_step": "ir_a_percepciones",
                "files": [
                    ("percepciones_excel", "#tablaPercepciones_botonExcel", "PER", "xls"),
                    ("percepciones_txt", "#tablaPercepciones_botonTxt", "PER", "txt"),
                    ("percepciones_txt_sifere", "#tablaPercepciones_botonTxtSifere", "PER SIFERE", "txt"),
                ],
            },
            {
                "tab_text": "Retenciones Bancarias",
                "tab_step": "ir_a_retenciones_bancarias",
                "files": [
                    (
                        "retenciones_bancarias_excel",
                        "#tablaRetencionesBancarias_botonExcel",
                        "RET bancarias",
                        "xls",
                    ),
                    (
                        "retenciones_bancarias_txt",
                        "#tablaRetencionesBancarias_botonTxt",
                        "RET bancarias",
                        "txt",
                    ),
                    (
                        "retenciones_bancarias_txt_sifere",
                        "#tablaRetencionesBancarias_botonTxtSifere",
                        "RET bancarias SIFERE",
                        "txt",
                    ),
                ],
            },
            {
                "tab_text": "Percepciones Aduaneras",
                "tab_step": "ir_a_percepciones_aduaneras",
                "files": [
                    ("percepciones_aduaneras_excel", "#tablaPercepcionesAduaneras_botonExcel", "PER ADUANERAS", "xls"),
                    ("percepciones_aduaneras_txt", "#tablaPercepcionesAduaneras_botonTxt", "PER ADUANERAS", "txt"),
                    ("percepciones_aduaneras_txt_sifere", "#tablaPercepcionesAduaneras_botonTxtSifere", "PER ADUANERAS SIFERE", "txt"),
                ],
            },
            {
                "tab_text": "Retenciones SIRTAC",
                "tab_step": "ir_a_retenciones_sirtac",
                "files": [
                    ("retenciones_sirtac_excel", "#tablaRetencionesSIRTAC_botonExcel", "RET SIRTAC", "xls"),
                    ("retenciones_sirtac_txt", "#tablaRetencionesSIRTAC_botonTxt", "RET SIRTAC", "txt"),
                    ("retenciones_sirtac_txt_sifere", "#tablaRetencionesSIRTAC_botonTxtSifere", "RET SIRTAC SIFERE", "txt"),
                ],
            }
        ]

        if estado_resultados.get("estado") == "sin_datos":
            debug.log("sin_datos periodo_sin_registros descargas_omitidas=1")
            sections = []

        for section in sections:
            tab_link = page.locator("#divConsultado").get_by_text(section["tab_text"], exact=True)
            if await tab_link.count() == 0 or not await tab_link.first.is_visible():
                debug.log(f"skip_section section={section['tab_text']} motivo=tab_no_visible")
                continue
            await _run_step(
                debug,
                page,
                section["tab_step"],
                lambda section=section: tab_link.first.click(),
            )
            await page.wait_for_timeout(1_000)
            for step_name, selector, tipo, extension in section["files"]:
                await _download_with_log(
                    page=page,
                    debug=debug,
                    step_name=step_name,
                    trigger_selector=selector,
                    target_path=debug.descargas_dir
                    / _build_download_name(
                        cuit_representado=cuit_representado,
                        tipo=tipo,
                        periodo_desde=period_desde,
                        periodo_hasta=period_hasta,
                        denominacion=denominacion,
                        extension=extension,
                    ),
                )

        debug.log("Flujo completado OK")
    except Exception as exc:
        debug.log(f"error_general={exc}")
        await debug.capture(page, "error_general", f"ERROR: {exc}")
        raise
    finally:
        if context is not None:
            await context.close()
        if browser is not None:
            await browser.close()
        if har_path.exists():
            debug.log(f"har_saved path={har_path} size={har_path.stat().st_size}")
        else:
            debug.log(f"har_missing path={har_path}")


async def main() -> None:
    load_dotenv()

    usuario = os.getenv("AGIP_USUARIO")
    clave = os.getenv("AGIP_CLAVE")
    cuit_representado = os.getenv("AGIP_CUIT_REPRESENTADO")
    denominacion = os.getenv("AGIP_DENOMINACION")
    desde = os.getenv("AGIP_DESDE")
    hasta = os.getenv("AGIP_HASTA")
    headless = os.getenv("AGIP_HEADLESS", "True").lower() == "true"

    required = {
        "AGIP_USUARIO": usuario,
        "AGIP_CLAVE": clave,
        "AGIP_CUIT_REPRESENTADO": cuit_representado,
        "AGIP_DENOMINACION": denominacion,
        "AGIP_DESDE": desde,
        "AGIP_HASTA": hasta,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError(f"Faltan variables de entorno obligatorias: {', '.join(missing)}")

    async with async_playwright() as playwright:
        await bot_agip(
            playwright=playwright,
            usuario=usuario or "",
            clave=clave or "",
            cuit_representado=cuit_representado or "",
            denominacion=denominacion or "",
            desde=desde or "",
            hasta=hasta or "",
            headless=headless,
        )


if __name__ == "__main__":
    asyncio.run(main())
