import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def run(
    playwright: Playwright,
    cuit: str,
    clave: str,
    headless: bool = True
    ) -> None:
    browser = await playwright.chromium.launch(headless=headless)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://sso.arba.gov.ar/Login/login?service=https%3A%2F%2Fdfe.arba.gov.ar%3A443%2FDomicilioElectronico%2FpreDescargarRetenciones.do")
    await page.get_by_role("textbox", name="Ingresá los 11 dígitos sin").click()
    await page.get_by_role("textbox", name="Ingresá los 11 dígitos sin").fill(cuit)
    await page.get_by_role("textbox", name="Ingresá tu clave").click()
    await page.get_by_role("textbox", name="Ingresá tu clave").fill(clave)
    await page.get_by_role("button", name="Ingresar").click()
    await page.goto("https://dfe.arba.gov.ar/DomicilioElectronico/preDescargarRetenciones.do")
    await page.locator("input[name=\"anio\"]").click()
    await page.locator("input[name=\"anio\"]").fill("2026")
    await page.locator("input[name=\"mes\"]").click()
    await page.locator("input[name=\"mes\"]").fill("01")
    await page.get_by_role("button", name="Consultar").click()
    await page.goto("https://dfe.arba.gov.ar/DomicilioElectronico/preDescargarRetenciones.do")
    await page.locator("input[name=\"anio\"]").click()
    await page.locator("input[name=\"anio\"]").fill("2026")
    await page.locator("input[name=\"mes\"]").click()
    await page.locator("input[name=\"mes\"]").fill("01")
    await page.get_by_role("button", name="Consultar").click()
    async with page.expect_download() as download_info:
        await page.get_by_role("button", name="Descargar").click()
    download = await download_info.value

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    import os
    from dotenv import load_dotenv
    
    load_dotenv()
    
    cuit = os.getenv("ARBA_CUIT")
    clave = os.getenv("ARBA_CLAVE")
    headless = os.getenv("ARBA_HEADLESS").lower() == "true"

    async with async_playwright() as playwright:
        await run(playwright, cuit, clave, headless)


asyncio.run(main())
