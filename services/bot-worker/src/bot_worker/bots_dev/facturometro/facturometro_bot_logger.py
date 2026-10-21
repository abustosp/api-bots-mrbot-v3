import asyncio
import json
import os
import re
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, Page

from shared.arca_login import (
    login_arca,
    open_arca_service,
    STEALTH_LAUNCH_ARGS,
    STEALTH_CONTEXT_DEFAULTS,
    STEALTH_INIT_SCRIPT,
)
from shared.proxy import get_proxy_config


BOT_NAME = "facturometro"


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = BOT_NAME) -> None:
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


async def bot_facturometro_logger(
    cuit: str,
    clave: str,
    representado_cuit: str = "",
    headless: bool = True,
) -> dict:
    results = {"monto": None, "tope": None, "categoria": None}
    error_logs: list[str] = []

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot Facturometro Logger")
    debug.log(f"CUIT: {cuit}")

    proxy = get_proxy_config()
    har_path = debug.har_dir / f"{BOT_NAME}.har"

    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=headless,
                proxy=proxy,
                args=STEALTH_LAUNCH_ARGS,
                ignore_default_args=["--enable-automation"],
            )
            context_kwargs = dict(STEALTH_CONTEXT_DEFAULTS)
            context_kwargs["record_har_path"] = har_path
            context_kwargs["record_har_content"] = "embed"
            context = await browser.new_context(**context_kwargs)
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            page = await context.new_page()

            await debug.capture(page, "inicio")

            login_result = await login_arca(
                page,
                cuit,
                clave,
                error_logs=error_logs,
                click_ver_todos=False,
            )
            await debug.capture(page, "post_login")
            if not login_result.success:
                debug.log(f"Login failed: {error_logs}")
                await context.close()
                await browser.close()
                return results

            if login_result.captcha_detected:
                debug.log("Captcha detectado")

            if login_result.password_change_required:
                debug.log("Se requiere cambiar la clave fiscal")
                error_logs.append("Se requiere cambiar la clave fiscal")
                await context.close()
                await browser.close()
                return results

            page1 = await open_arca_service(
                page,
                service_name="MONOTRIBUTO",
                service_matchers=(re.compile(r"MONOTRIBUTO.*", re.I),),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
                service_unavailable_message="No se encuentra habilitado el servicio de MONOTRIBUTO",
            )
            if page1 is None:
                debug.log("No se pudo abrir servicio MONOTRIBUTO")
                await context.close()
                await browser.close()
                return results
            await page1.wait_for_load_state("domcontentloaded")
            await debug.capture(page1, "servicio_abierto")

            if representado_cuit:
                try:
                    representado_cuit_2 = str(representado_cuit)
                    raw = representado_cuit_2
                    if len(representado_cuit_2) == 11:
                        representado_cuit_2 = f"{representado_cuit_2[0:2]}-{representado_cuit_2[2:10]}-{representado_cuit_2[10:11]}"

                    mono_link = page1.locator(f'a.usr[usr="{raw}"]')
                    if await mono_link.count() > 0:
                        await mono_link.click(timeout=3000)
                        await page1.wait_for_load_state("domcontentloaded")
                        await debug.capture(page1, "representado_seleccionado")
                        debug.log(f"Representado seleccionado (monotributo): {representado_cuit_2}")
                    else:
                        await page1.locator(f'small.pull-right:has-text("{representado_cuit_2}")').click(timeout=3000)
                        await debug.capture(page1, "representado_seleccionado")
                        debug.log(f"Representado seleccionado: {representado_cuit_2}")
                except Exception:
                    debug.log("No se encontro selector de representado o ya estaba seleccionado")
                    try:
                        representado_cuit_text = await page1.locator("span.nombre-propio.nombre-activo.pull-righ").text_content(timeout=3000)
                        if str(representado_cuit) not in representado_cuit_text.replace("-", ""):
                            error_logs.append(f"No se pudo seleccionar el representado correcto {representado_cuit}")
                            debug.log(f"Error seleccionando representado: activo={representado_cuit_text}")
                    except Exception:
                        pass
            else:
                try:
                    lnk = page1.locator("#lnkRepresentando")
                    if await lnk.count() > 0:
                        await lnk.click(timeout=3000)
                        await page1.wait_for_load_state("domcontentloaded")
                        await debug.capture(page1, "representado_confirmado")
                        debug.log("Se confirmo el representado propio en SelecRepresentado")
                except Exception:
                    pass

            monto = None
            tope = None
            categoria = None
            mensaje_alternativo = None

            try:
                await page1.get_by_role("link", name="Inicio").click(timeout=5000)
                await page1.wait_for_load_state("domcontentloaded")
            except Exception:
                pass
            await debug.capture(page1, "inicio_popup")

            div_count = await page1.locator("#divFacturometro").count()
            debug.log(f"#divFacturometro presente en Inicio: {div_count}")

            if div_count > 0:
                try:
                    await page1.get_by_text(re.compile(r"Facturación electrónica.*", re.I)).first.click(timeout=5000)
                    await page1.wait_for_load_state("domcontentloaded")
                    await debug.capture(page1, "navegar_facturometro")
                except Exception as exc:
                    debug.log(f"No se pudo abrir panel Facturacion electronica: {exc}")

                try:
                    await page1.wait_for_function(
                        """() => {
                            const g = (id) => { const el = document.getElementById(id); return el ? (el.innerText || '').trim() : ''; };
                            return g('spanFacturometroMonto').length > 0
                                && g('spanFacturometroCategoriaTope').length > 0
                                && g('spanFacturometroCategoria2').length > 0;
                        }""",
                        timeout=15_000,
                    )
                    monto = (await page1.locator("#spanFacturometroMonto").inner_text(timeout=3000)).strip()
                    tope = (await page1.locator("#spanFacturometroCategoriaTope").inner_text(timeout=3000)).strip()
                    categoria = (await page1.locator("#spanFacturometroCategoria2").inner_text(timeout=3000)).strip()
                    debug.log(f"Facturometro cargado via spans: monto={monto} tope={tope} categoria={categoria}")
                except Exception:
                    debug.log("Spans de facturometro vacios o ausentes")
            else:
                debug.log("#divFacturometro no existe en Inicio - intentando AJAX CalcularFacturacion")

            if monto is None:
                try:
                    ajax_result = await page1.evaluate("""async () => {
                        try {
                            const resp = await fetch('inicio.aspx/CalcularFacturacion', {
                                method: 'POST',
                                headers: {'Content-Type': 'application/json; charset=utf-8'},
                                body: '{}'
                            });
                            const j = await resp.json();
                            return {
                                exito: j.d.exito,
                                visible: j.d.visible,
                                pendiente: j.d.pendiente,
                                valor: j.d.valor,
                                valorTope: j.d.valorTope,
                                categoria: j.d.categoria,
                                fechas: j.d.fechas,
                                alertaDetalle: j.d.alertaDetalle,
                            };
                        } catch(e) { return {error: e.message}; }
                    }""")
                    debug.log(f"AJAX CalcularFacturacion: {ajax_result}")

                    if ajax_result and ajax_result.get("visible") and ajax_result.get("valor"):
                        monto = str(ajax_result.get("valor") or "").strip()
                        tope = str(ajax_result.get("valorTope") or "").strip()
                        categoria = str(ajax_result.get("categoria") or "").strip()
                        debug.log(f"Facturometro cargado via AJAX: monto={monto} tope={tope} categoria={categoria}")
                    elif ajax_result and not ajax_result.get("visible"):
                        mensaje_alternativo = ajax_result.get("alertaDetalle") or "Facturómetro no visible para este contribuyente"
                        debug.log(f"Facturometro no visible: {mensaje_alternativo}")
                    elif ajax_result and ajax_result.get("pendiente"):
                        mensaje_alternativo = "Facturómetro pendiente de cálculo en AFIP"
                        debug.log(mensaje_alternativo)
                    elif ajax_result and ajax_result.get("error"):
                        mensaje_alternativo = f"Error AJAX: {ajax_result['error']}"
                        debug.log(mensaje_alternativo)
                except Exception as exc:
                    debug.log(f"Error en AJAX CalcularFacturacion: {exc}")

            if monto is None:
                if mensaje_alternativo:
                    debug.log(f"Facturometro no disponible: {mensaje_alternativo}")
                else:
                    debug.log("Facturometro: datos no disponibles")
                await context.close()
                await browser.close()
                return results

            debug.log(f"Monto facturado: {monto}")
            debug.log(f"Tope facturacion: {tope}")
            debug.log(f"Categoria: {categoria}")

            results["monto"] = monto
            results["tope"] = tope
            results["categoria"] = categoria

            data = {
                "monto_facturado": monto,
                "tope_facturacion": tope,
                "categoria": categoria,
                "timestamp": datetime.now().isoformat(),
            }
            data_path = debug.descargas_dir / "facturometro.json"
            data_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            debug.log(f"Datos guardados en {data_path}")

            try:
                section = page1.locator("#divFacturometro")
                if await section.count() > 0:
                    await section.screenshot(
                        path=str(debug.descargas_dir / "facturometro_seccion.png")
                    )
                    debug.log("facturometro_screenshot=facturometro_seccion.png")
                else:
                    debug.log("No se encontro #divFacturometro para screenshot")
            except Exception as e:
                debug.log(f"No se pudo tomar screenshot de la seccion facturometro: {e}")

            await debug.capture(page1, "facturometro_datos")

            await context.close()
            await browser.close()
    except Exception as exc:
        error_logs.append(f"Error en bot facturometro: {exc}")
        debug.log(f"Error: {exc}")

    return results


async def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    cuit = os.getenv("FACTUROMETRO_CUIT", "")
    representado_cuit = os.getenv("FACTUROMETRO_REPRESENTADO_CUIT", "")
    clave = os.getenv("FACTUROMETRO_CLAVE", "")
    headless = os.getenv("FACTUROMETRO_HEADLESS", "False").lower() == "true"

    if cuit and clave:
        res = await bot_facturometro_logger(cuit, clave, representado_cuit, headless)
        print(res)
    else:
        print("Faltan variables de entorno FACTUROMETRO_CUIT y FACTUROMETRO_CLAVE")


if __name__ == "__main__":
    asyncio.run(main())
