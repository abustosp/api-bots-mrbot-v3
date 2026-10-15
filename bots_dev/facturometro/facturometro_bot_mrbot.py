import os
import tempfile
import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from playwright.async_api import async_playwright

from shared.arca_login import (
    login_arca,
    open_arca_service,
    STEALTH_LAUNCH_ARGS,
    STEALTH_CONTEXT_DEFAULTS,
    STEALTH_INIT_SCRIPT,
)
from app.utils.proxies import get_proxy_config
from app.utils.browser_limiter import limit_browser


async def bot_facturometro(
    cuit: str,
    clave: str,
    representado_cuit: str,
    headless: bool = True,

) -> Tuple[dict, List[str], bool]:
    results = {"monto": None, "tope": None, "categoria": None, "screenshot_url": None}
    error_logs: List[str] = []

    print(f"Iniciando bot Facturometro - CUIT: {cuit}")

    proxy = get_proxy_config()

    tmp_dir = tempfile.TemporaryDirectory(prefix="facturometro_")
    try:
        async with limit_browser(), async_playwright() as playwright:
            browser = await playwright.chromium.launch(
                headless=headless,
                proxy=proxy,
                args=STEALTH_LAUNCH_ARGS,
                ignore_default_args=["--enable-automation"],
            )
            context = await browser.new_context(**STEALTH_CONTEXT_DEFAULTS)
            await context.add_init_script(STEALTH_INIT_SCRIPT)
            page = await context.new_page()

            login_result = await login_arca(
                page,
                cuit,
                clave,
                error_logs=error_logs,
                click_ver_todos=False,
            )
            if not login_result.success:
                print(f"Login failed: {error_logs}")
                await context.close()
                await browser.close()
                return results, error_logs, False

            if login_result.captcha_detected:
                print("Captcha detectado")

            if login_result.password_change_required:
                print("Se requiere cambiar la clave fiscal")
                await context.close()
                await browser.close()
                return results, error_logs, False

            page1 = await open_arca_service(
                page,
                service_name="MONOTRIBUTO",
                service_matchers=(re.compile(r"MONOTRIBUTO.*", re.I),),
                error_logs=error_logs,
                ver_todos_error_message="No se pudo abrir 'Ver todos' en ARCA",
                service_unavailable_message="No se encuentra habilitado el servicio de MONOTRIBUTO",
            )
            if page1 is None:
                print("No se pudo abrir servicio MONOTRIBUTO")
                await context.close()
                await browser.close()
                return results, error_logs, False

            await page1.wait_for_load_state("domcontentloaded")

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
                    else:
                        await page1.locator(f'small.pull-right:has-text("{representado_cuit_2}")').click(timeout=3000)
                except Exception:
                    try:
                        representado_cuit_text = await page1.locator("span.nombre-propio.nombre-activo.pull-right").text_content()
                        if str(representado_cuit) not in representado_cuit_text.replace("-", ""):
                            error_logs.append(f"No se pudo seleccionar el representado correcto {representado_cuit}")
                    except Exception:
                        pass
            else:
                try:
                    lnk = page1.locator("#lnkRepresentando")
                    if await lnk.count() > 0:
                        await lnk.click(timeout=3000)
                        await page1.wait_for_load_state("domcontentloaded")
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

            div_count = await page1.locator("#divFacturometro").count()

            if div_count > 0:
                try:
                    await page1.get_by_text(re.compile(r"Facturación electrónica.*", re.I)).first.click(timeout=5000)
                    await page1.wait_for_load_state("domcontentloaded")
                except Exception:
                    pass

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
                except Exception:
                    pass

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
                                visible: j.d.visible,
                                pendiente: j.d.pendiente,
                                valor: j.d.valor,
                                valorTope: j.d.valorTope,
                                categoria: j.d.categoria,
                                alertaDetalle: j.d.alertaDetalle,
                            };
                        } catch(e) { return {error: e.message}; }
                    }""")

                    if ajax_result and ajax_result.get("visible") and ajax_result.get("valor"):
                        monto = str(ajax_result.get("valor") or "").strip()
                        tope = str(ajax_result.get("valorTope") or "").strip()
                        categoria = str(ajax_result.get("categoria") or "").strip()
                    elif ajax_result and not ajax_result.get("visible"):
                        mensaje_alternativo = ajax_result.get("alertaDetalle") or "Facturómetro no visible para este contribuyente"
                    elif ajax_result and ajax_result.get("pendiente"):
                        mensaje_alternativo = "Facturómetro pendiente de cálculo en AFIP"
                    elif ajax_result and ajax_result.get("error"):
                        mensaje_alternativo = f"Error AJAX: {ajax_result['error']}"
                except Exception:
                    pass

            if monto is None:
                if mensaje_alternativo:
                    error_logs.append(f"Facturómetro no disponible: {mensaje_alternativo}")
                    print(f"Facturómetro no disponible: {mensaje_alternativo}")
                else:
                    error_logs.append("Facturómetro: datos no disponibles en esta página")
                    print("Facturómetro: datos no disponibles en esta pagina")
                await context.close()
                await browser.close()
                return results, error_logs, False

            print(f"Monto facturado: {monto}")
            print(f"Tope facturacion: {tope}")
            print(f"Categoria: {categoria}")

            results["monto"] = monto
            results["tope"] = tope
            results["categoria"] = categoria

            screenshot_path = None
            try:
                section = page1.locator("#divFacturometro")
                if await section.count() > 0:
                    screenshot_path = Path(tmp_dir.name) / "facturometro_seccion.png"
                    await section.screenshot(path=str(screenshot_path))
                    print(f"Captura guardada en {screenshot_path}")
                else:
                    print("No se encontro #divFacturometro para screenshot")
            except Exception as e:
                error_logs.append(f"No se pudo tomar screenshot: {e}")
                print(f"No se pudo tomar screenshot: {e}")

            await context.close()
            await browser.close()

            print("Bot Facturometro completado exitosamente")
            return results, error_logs, True

    except Exception as exc:
        error_logs.append(f"Error en bot facturometro: {exc}")
        print(f"Error: {exc}")
        return results, error_logs, False
    finally:
        try:
            tmp_dir.cleanup()
        except Exception:
            pass
