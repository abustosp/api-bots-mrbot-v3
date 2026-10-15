import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.async_api import Page, async_playwright

from shared.arca_login import start_arca_session, open_arca_service

BOT_NAME = "certificado_mipyme"


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

    async def capture(self, page: Optional[Page], step: str) -> None:
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


async def _try_click_expand_combobox(page: Page) -> None:
    for label in ("expand combobox", "Abrir"):
        try:
            btn = page.get_by_role("button", name=label)
            if await btn.count() > 0:
                await btn.first.click()
                await page.wait_for_timeout(500)
                return
        except Exception:
            continue


async def _select_representado(page: Page, cuit_representado: str, debug: Optional[DebugCapture] = None) -> bool:
    opciones_encontradas: list[str] = []
    try:
        combobox = page.get_by_role("combobox", name="Ingrese al menos 3 caracteres")
        await combobox.wait_for(state="visible", timeout=10000)
        await combobox.click()
        if debug: debug.log("Combobox clicked")

        await _try_click_expand_combobox(page)
        await page.wait_for_timeout(1500)
        if debug: debug.log("Expand combobox attempted")

        options = page.locator('[role="option"], li[role="option"], .MuiAutocomplete-option')
        option_found: bool = False
        for attempt in range(5):
            count = await options.count()
            if count > 0:
                if debug: debug.log(f"Found {count} options in dropdown")
                for i in range(count):
                    text = await options.nth(i).inner_text()
                    opciones_encontradas.append(text)
                    if debug: debug.log(f"Option {i}: {text}")
                    if not option_found and cuit_representado in text:
                        await options.nth(i).click()
                        option_found = True
                        if debug: debug.log(f"Clicked option {i} matching {cuit_representado}")
                if not option_found:
                    if debug: debug.log("No matching option found, clicking first")
                    await options.first.click()
                    option_found = True
                return True
            await page.wait_for_timeout(500)

        if debug: debug.log("No options after expand, trying fill")
        await combobox.fill(cuit_representado)
        await page.wait_for_timeout(1500)

        for attempt in range(5):
            count = await options.count()
            if count > 0:
                if debug: debug.log(f"Found {count} options after fill")
                for i in range(count):
                    text = await options.nth(i).inner_text()
                    opciones_encontradas.append(text)
                    if debug: debug.log(f"Option {i}: {text}")
                    if not option_found and cuit_representado in text:
                        await options.nth(i).click()
                        option_found = True
                        if debug: debug.log(f"Clicked option {i} matching {cuit_representado}")
                if not option_found:
                    if debug: debug.log("No matching option found, clicking first")
                    await options.first.click()
                    option_found = True
                return True
            await page.wait_for_timeout(500)

        return False

    except Exception as exc:
        if debug: debug.log(f"Error selecting representado: {exc}")
        return False
    finally:
        if debug: debug.log(f"Opciones encontradas: {opciones_encontradas}")


async def bot_certificado_mipyme_logger(
    cuit_representante: str,
    clave: str,
    cuit_representado: str,
    headless: bool = True,
) -> tuple[dict, list[str], bool]:
    results: dict = {"archivos": []}
    error_logs: list[str] = []
    success = False
    browser = None
    context = None

    if not all([cuit_representante, clave, cuit_representado]):
        error_logs.append("Faltan parametros requeridos: CUIT representante, clave y CUIT representado.")
        return results, error_logs, False

    debug = DebugCapture(prefix=BOT_NAME)
    debug.log("Iniciando bot Certificado MiPyME Logger")

    har_path = debug.har_dir / f"{BOT_NAME}.har"

    try:
        async with async_playwright() as pw:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]

            context_kwargs = {
                "locale": "es-AR",
                "timezone_id": "America/Argentina/Buenos_Aires",
                "accept_downloads": True,
                "extra_http_headers": {"Accept-Language": "es-AR,es;q=0.9"},
                "record_har_path": har_path,
                "record_har_content": "embed",
            }

            debug.log("Paso 1: Iniciando sesion en ARCA")
            arca_session = await start_arca_session(
                pw,
                cuit_representante=cuit_representante,
                clave_representante=clave,
                error_logs=error_logs,
                headless=headless,
                launch_args=launch_args,
                context_kwargs=context_kwargs,
            )
            if arca_session is None:
                debug.log("No se pudo iniciar sesion en ARCA")
                return results, error_logs, False

            browser = arca_session.browser
            context = arca_session.context
            page = arca_session.page

            captura_page = page

            await debug.capture(captura_page, "login_arca_ok")

            debug.log("Paso 2: Abriendo servicio LUFE")
            page1 = await open_arca_service(
                captura_page,
                service_name="LUFE",
                service_matchers=(re.compile(r"LUFE", re.I),),
                error_logs=error_logs,
                service_unavailable_message="No se encuentra habilitado el servicio de LUFE",
            )
            if page1 is None:
                debug.log("No se pudo abrir el servicio LUFE")
                return results, error_logs, False
            if page1 is not captura_page and not captura_page.is_closed():
                await captura_page.close()

            await page1.wait_for_load_state("networkidle")
            await debug.capture(page1, "abrir_lufe")

            debug.log("Paso 3: Seleccionando representado")
            representado_ok = await _select_representado(page1, cuit_representado, debug)
            if not representado_ok:
                error_msg = "No se pudo seleccionar el representado en el combo"
                debug.log(error_msg)
                await debug.capture(page1, "error_seleccion_representado")
                error_logs.append(error_msg)
                return results, error_logs, False

            try:
                seleccionar_btn = page1.get_by_role("button", name="Seleccionar")
                await seleccionar_btn.wait_for(state="visible", timeout=5000)
                await seleccionar_btn.click()
                debug.log("Click en Seleccionar")
            except Exception as exc:
                debug.log(f"Error al clickear Seleccionar: {exc}")

            await page1.wait_for_load_state("networkidle")
            await asyncio.sleep(2)
            await debug.capture(page1, "seleccionar_representado")

            page_content = await page1.content()
            if "No se encuentra habilitado" in page_content:
                error_msg = "No se encuentra habilitado el servicio de Certificado MiPyME"
                debug.log(error_msg)
                await debug.capture(page1, "servicio_no_habilitado")
                error_logs.append(error_msg)
                return results, error_logs, False

            debug.log("Paso 4: Descargando certificado")
            download_link = page1.get_by_role("link", name=re.compile(r"Descargar Certificado MiPyME", re.I))
            await download_link.wait_for(state="visible", timeout=15000)
            await debug.capture(page1, "antes_descarga")

            async with page1.expect_download(timeout=30000) as download_info:
                await download_link.click()
            download = await download_info.value

            filename = download.suggested_filename or f"certificado_mipyme_{cuit_representado}.pdf"
            file_path = debug.descargas_dir / filename
            await download.save_as(file_path)
            debug.log(f"Certificado descargado: {file_path}")
            await debug.capture(page1, "descarga_completada")

            results["archivos"] = [filename]
            results["output_dir"] = str(debug.descargas_dir)
            success = True

    except Exception as exc:
        error_msg = f"Error en bot Certificado MiPyME: {exc}"
        if debug:
            debug.log(error_msg)
        error_logs.append(error_msg)
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

    return results, error_logs, success


async def main():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    cuit_representante = os.getenv("CERTIFICADO_MIPYME_CUIT_REPRESENTANTE", "")
    clave = os.getenv("CERTIFICADO_MIPYME_CLAVE", "")
    cuit_representado = os.getenv("CERTIFICADO_MIPYME_CUIT_REPRESENTADO", "")
    headless_str = os.getenv("CERTIFICADO_MIPYME_HEADLESS", "False")
    headless = headless_str.lower() == "true"

    if not all([cuit_representante, clave, cuit_representado]):
        print("ERROR: Faltan variables de entorno. Verificar .env")
        print("  CERTIFICADO_MIPYME_CUIT_REPRESENTANTE")
        print("  CERTIFICADO_MIPYME_CLAVE")
        print("  CERTIFICADO_MIPYME_CUIT_REPRESENTADO")
        return

    results, errors, ok = await bot_certificado_mipyme_logger(
        cuit_representante=cuit_representante,
        clave=clave,
        cuit_representado=cuit_representado,
        headless=headless,
    )

    print(f"\n{'='*40}")
    print(f"Resultado: {'OK' if ok else 'ERROR'}")
    print(f"Archivos: {results.get('archivos', [])}")
    print(f"Output dir: {results.get('output_dir', 'N/A')}")
    if errors:
        print(f"Errores:")
        for e in errors:
            print(f"  - {e}")
    print(f"{'='*40}")


if __name__ == "__main__":
    asyncio.run(main())
