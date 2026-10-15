import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def srt(
    playwright: Playwright,
    cuit_representante: str,
    clave_representante: str,
    cuit_empresa: str,
    headless: bool = False
    ) -> None:
    browser = await playwright.chromium.launch(headless=headless)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml?action=SYSTEM")
    await page.get_by_role("spinbutton").click()
    await page.get_by_role("spinbutton").fill(cuit_representante)
    await page.get_by_role("spinbutton").press("Enter")
    await page.get_by_role("textbox", name="TU CLAVE").fill(clave_representante)
    await page.get_by_role("button", name="Ingresar").click()
    await page.get_by_role("link", name="Ver todos").click()
    async with page.expect_popup() as page1_info:
        await page.get_by_role("button", name="E-SERVICIOS SRT Sistema de").click()
    page1 = await page1_info.value
    await page1.locator("[id=\"11\"]").click()
    await page1.get_by_role("spinbutton", name="Ingrese CUIT/CUIL").click()
    await page1.get_by_role("spinbutton", name="Ingrese CUIT/CUIL").fill(cuit_empresa)
    # CAPTCHA
    await page1.locator("iframe[name=\"a-6o3vb5lc7zos\"]").content_frame.locator(".rc-anchor-center-item").first.click()
    await page1.locator("iframe[name=\"c-6o3vb5lc7zos\"]").content_frame.locator("[id=\"14\"]").click()
    await page1.locator("iframe[name=\"c-6o3vb5lc7zos\"]").content_frame.locator("[id=\"14\"]").click()
    
    #Consultar
    await page1.get_by_text("Consultar").click()
    
    #obtener los datos de la tabla
    await page1.locator("#ifrmResultadoPorCuit").content_frame.get_by_text("- BUSTOS Y HOPE SOCIEDAD DE HECHO").click()
    await page1.locator("#ifrmResultadoPorCuit").content_frame.get_by_role("cell", name="ART vigente").click()
    await page1.locator("#ifrmResultadoPorCuit").content_frame.locator("#DatosResultados div").click()

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        import os
        from dotenv import load_dotenv
        load_dotenv()
        
        cuit_representante = os.getenv("SRT_CUIT_REPRESENTANTE")
        clave_representante = os.getenv("SRT_CLAVE_REPRESENTANTE")
        cuit_empresa = os.getenv("SRT_CUIT_REPRESENTADO")
        headless = os.getenv("SRT_HEADLESS", "False").lower() == "true"
        
        await srt(playwright, cuit_representante, clave_representante, cuit_empresa, headless)


asyncio.run(main())
