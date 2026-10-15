import asyncio
import os
from pathlib import Path
from typing import List, Tuple

from playwright.async_api import async_playwright

import re

DOWNLOAD_DIR = Path("./descargas")


async def run(
    cuit: str,
    clave: str,
    anio: str,
    mes: str,
    headless: bool = True,
) -> Tuple[bool, List[str]]:
    error_logs: List[str] = []
    print(f"Iniciando bot ARBA - CUIT: {cuit} - Periodo: {anio}{mes}")

    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=headless)
            context = await browser.new_context(accept_downloads=True)
            page = await context.new_page()

            login_url = "https://sso.arba.gov.ar/Login/login?service=https%3A%2F%2Fdfe.arba.gov.ar%3A443%2FDomicilioElectronico%2FpreDescargarRetenciones.do"
            await page.goto(login_url)

            await page.get_by_role("textbox", name="Ingresá los 11 dígitos sin").fill(cuit)
            await page.get_by_role("textbox", name="Ingresá tu clave").fill(clave)
            await page.get_by_role("button", name="Ingresar").click()
            await page.wait_for_load_state("networkidle")

            rol_select = page.locator("select[name='rol']")
            if await rol_select.count() > 0:
                print("Seleccionando rol Contribuyente/Contribuyente de Ingresos Brutos")
                await rol_select.select_option("Contribuyente/Contribuyente de Ingresos Brutos")
                await page.get_by_role("button", name="Continuar").click()
                await page.wait_for_load_state("networkidle")

            if "sso.arba.gov.ar/Login/login" in page.url:
                error_text = "Credenciales inválidas"
                try:
                    error_elem = page.locator("font[color='red']")
                    if await error_elem.count() > 0:
                        error_text = (await error_elem.first.text_content()).strip()
                except Exception:
                    pass
                error_logs.append(error_text)
                await context.close()
                await browser.close()
                return False, error_logs

            await page.goto("https://dfe.arba.gov.ar/DomicilioElectronico/preDescargarRetenciones.do")
            await page.wait_for_load_state("networkidle")

            await page.locator("input[name=\"anio\"]").fill(anio)
            await page.locator("input[name=\"mes\"]").fill(mes)
            await page.get_by_role("button", name="Consultar").click()
            await page.wait_for_load_state("networkidle")

            await page.goto("https://dfe.arba.gov.ar/DomicilioElectronico/preDescargarRetenciones.do")
            await page.wait_for_load_state("networkidle")

            await page.locator("input[name=\"anio\"]").fill(anio)
            await page.locator("input[name=\"mes\"]").fill(mes)
            await page.get_by_role("button", name="Consultar").click()
            await page.wait_for_load_state("networkidle")

            page_content = await page.content()
            if "No Existen archivos para el periodo solicitado" in page_content:
                error_text = "No Existen archivos para el periodo solicitado"
                print(f"Sin archivos: {error_text}")
                error_logs.append(error_text)
                await context.close()
                await browser.close()
                return False, error_logs

            async with page.expect_download() as download_info:
                await page.get_by_role("button", name="Descargar").click()
            download = await download_info.value

            DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
            download_path = DOWNLOAD_DIR / (download.suggested_filename or f"retenciones_{anio}{mes}.zip")
            await download.save_as(str(download_path))
            print(f"Archivo descargado: {download_path}")

            print("Bot ARBA completado exitosamente")
            await context.close()
            await browser.close()
            return True, error_logs

    except Exception as e:
        print(f"Error general: {e}")
        error_logs.append(str(e))
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

    success, errors = await run(cuit, clave, anio, mes, headless)
    print(f"Success: {success}")
    if errors:
        print(f"Errors: {errors}")


if __name__ == "__main__":
    asyncio.run(main())
