import asyncio
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from playwright.async_api import async_playwright

BOT_NAME = "arba"


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


async def run(
    cuit: str,
    clave: str,
    anio: str,
    mes: str,
    headless: bool = True,
) -> Tuple[bool, List[str]]:
    error_logs: List[str] = []
    debug = DebugCapture(prefix=BOT_NAME)
    debug.log(f"Iniciando bot ARBA Logger")
    debug.log(f"CUIT: {cuit}, Periodo: {anio}{mes}")

    try:
        async with async_playwright() as pw:
            har_path = debug.har_dir / "arba.har"
            browser = await pw.chromium.launch(headless=headless)
            context = await browser.new_context(
                accept_downloads=True,
                record_har_path=str(har_path),
                record_har_content="embed",
            )
            page = await context.new_page()
            await debug.capture(page, "inicio")

            # Step 1 - Login page
            debug.log("Paso 1: Navegando al login de ARBA")
            login_url = "https://sso.arba.gov.ar/Login/login?service=https%3A%2F%2Fdfe.arba.gov.ar%3A443%2FDomicilioElectronico%2FpreDescargarRetenciones.do"
            await page.goto(login_url)
            await debug.capture(page, "login_page")

            # Step 2 - CUIT
            debug.log("Paso 2: Ingresando CUIT")
            await page.get_by_role("textbox", name="Ingresá los 11 dígitos sin").click()
            await page.get_by_role("textbox", name="Ingresá los 11 dígitos sin").fill(cuit)
            await debug.capture(page, "cuit_ingresado")

            # Step 3 - Clave
            debug.log("Paso 3: Ingresando clave")
            await page.get_by_role("textbox", name="Ingresá tu clave").click()
            await page.get_by_role("textbox", name="Ingresá tu clave").fill(clave)
            await debug.capture(page, "clave_ingresada")

            # Step 4 - Click Ingresar
            debug.log("Paso 4: Click en Ingresar")
            await page.get_by_role("button", name="Ingresar").click()
            await page.wait_for_load_state("networkidle")

            # Step 4b-4d: Check for role selection (some CUITs require selecting a role after login)
            rol_select = page.locator("select[name='rol']")
            if await rol_select.count() > 0:
                debug.log("Paso 4b: Detectada pantalla de selección de rol")
                await debug.capture(page, "rol_selection")
                debug.log("Paso 4c: Seleccionando Contribuyente/Contribuyente de Ingresos Brutos")
                await rol_select.select_option("Contribuyente/Contribuyente de Ingresos Brutos")
                await debug.capture(page, "rol_option_selected")
                debug.log("Paso 4d: Click en Continuar")
                await page.get_by_role("button", name="Continuar").click()
                await page.wait_for_load_state("networkidle")
                await debug.capture(page, "post_rol_continuar")
            else:
                debug.log("Sin pantalla de selección de rol, continuando...")

            if "sso.arba.gov.ar/Login/login" in page.url:
                error_text = "Credenciales inválidas"
                try:
                    error_elem = page.locator("font[color='red']")
                    if await error_elem.count() > 0:
                        error_text = (await error_elem.first.text_content()).strip()
                except Exception:
                    pass
                debug.log(f"Error de login: {error_text}")
                error_logs.append(error_text)
                await debug.capture(page, "login_error")
                await context.close()
                await browser.close()
                return False, error_logs

            await debug.capture(page, "post_ingresar")

            # Step 5 - Navigate to retenciones page
            debug.log("Paso 5: Navegando a página de retenciones")
            await page.goto("https://dfe.arba.gov.ar/DomicilioElectronico/preDescargarRetenciones.do")
            await page.wait_for_load_state("networkidle")
            await debug.capture(page, "retenciones_page")

            # Step 6 - Fill anio + mes (first time)
            debug.log("Paso 6: Ingresando año y mes (1ra consulta)")
            await page.locator("input[name=\"anio\"]").click()
            await page.locator("input[name=\"anio\"]").fill(anio)
            await page.locator("input[name=\"mes\"]").click()
            await page.locator("input[name=\"mes\"]").fill(mes)
            await debug.capture(page, "periodo_ingresado_1")

            # Step 7 - Click Consultar (first time)
            debug.log("Paso 7: Click en Consultar (1ra vez)")
            await page.get_by_role("button", name="Consultar").click()
            await page.wait_for_load_state("networkidle")
            await debug.capture(page, "consulta_1")

            # Step 8 - Reload retenciones page
            debug.log("Paso 8: Recargando página de retenciones")
            await page.goto("https://dfe.arba.gov.ar/DomicilioElectronico/preDescargarRetenciones.do")
            await page.wait_for_load_state("networkidle")
            await debug.capture(page, "retenciones_reload")

            # Step 9 - Fill anio + mes (second time)
            debug.log("Paso 9: Ingresando año y mes (2da consulta)")
            await page.locator("input[name=\"anio\"]").click()
            await page.locator("input[name=\"anio\"]").fill(anio)
            await page.locator("input[name=\"mes\"]").click()
            await page.locator("input[name=\"mes\"]").fill(mes)
            await debug.capture(page, "periodo_ingresado_2")

            # Step 10 - Click Consultar (second time)
            debug.log("Paso 10: Click en Consultar (2da vez)")
            await page.get_by_role("button", name="Consultar").click()
            await page.wait_for_load_state("networkidle")
            await debug.capture(page, "consulta_2")

            # Step 10b - Check if files exist
            debug.log("Paso 10b: Verificando si hay archivos para descargar")
            page_content = await page.content()
            if "No Existen archivos para el periodo solicitado" in page_content:
                error_text = "No Existen archivos para el periodo solicitado"
                debug.log(f"Sin archivos: {error_text}")
                error_logs.append(error_text)
                await debug.capture(page, "sin_archivos")
                await context.close()
                await browser.close()
                return False, error_logs

            # Step 11 - Download
            debug.log("Paso 11: Descargando archivo")
            async with page.expect_download() as download_info:
                await page.get_by_role("button", name="Descargar").click()
            download = await download_info.value

            download_path = debug.descargas_dir / (download.suggested_filename or f"retenciones_{anio}{mes}.zip")
            await download.save_as(str(download_path))
            debug.log(f"Archivo descargado: {download_path}")

            debug.log("Bot ARBA completado exitosamente")
            await context.close()
            await browser.close()
            return True, error_logs

    except Exception as e:
        debug.log(f"Error general: {e}")
        error_logs.append(str(e))
        try:
            await debug.capture(page, "error_general")
        except Exception:
            pass
        return False, error_logs


async def main() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass

    cuit = os.getenv("ARBA_CUIT", "")
    clave = os.getenv("ARBA_CLAVE", "")
    anio = os.getenv("ARBA_ANIO", "2026")
    mes = os.getenv("ARBA_MES", "01")
    headless = os.getenv("ARBA_HEADLESS", "True").lower() == "true"

    if not cuit or not clave:
        print("ERROR: Faltan ARBA_CUIT y/o ARBA_CLAVE en .env")
        return

    print(f"Ejecutando bot ARBA Logger - CUIT: {cuit} - Periodo: {anio}{mes}")
    success, errors = await run(cuit, clave, anio, mes, headless)
    print(f"Success: {success}")
    if errors:
        print(f"Errors: {errors}")


if __name__ == "__main__":
    asyncio.run(main())
