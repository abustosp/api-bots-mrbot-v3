import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def run(
    playwright: Playwright,
    cuit:str,
    clave:str,
    headless: bool = True,
    ) -> None:
    browser = await playwright.chromium.launch(headless=headless)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
    await page.get_by_role("spinbutton").click()
    await page.get_by_role("spinbutton").fill(cuit)
    await page.get_by_role("button", name="Siguiente").click()
    await page.get_by_role("textbox", name="TU CLAVE").click()
    await page.get_by_role("textbox", name="TU CLAVE").fill(clave)
    await page.get_by_role("button", name="Ingresar").click()
    await page.get_by_role("link", name="Ver todos").click()
    async with page.expect_popup() as page1_info:
        await page.get_by_role("button", name="MONOTRIBUTO Adhesión y/o").click()
    page1 = await page1_info.value
    await page1.get_by_role("link", name="Inicio").click()
    await page1.get_by_text("Facturación electrónica | 01/07/2025 al 02/06/2026 Ver categorías Superaste el").click()
    await page1.locator("#spanFacturometroMonto").click()
    await page1.locator("#spanFacturometroCategoriaTope").click()
    await page1.locator("#spanFacturometroCategoria2").click()

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        import os
        from dotenv import load_dotenv
        
        load_dotenv()
        cuit = os.getenv("FACTUROMETRO_CUIT")
        clave = os.getenv("FACTUROMETRO_CLAVE")
        headless = os.getenv("FACTUROMETRO_HEADLESS", "False").lower() == "true"
        await run(playwright, cuit, clave, headless=headless)


asyncio.run(main())
