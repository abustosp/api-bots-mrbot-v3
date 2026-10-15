import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def run(
    playwright: Playwright,
    cuit_representante: str,
    cuit_representado: str,
    clave: str,
    headless: bool = True
) -> None:
    browser = await playwright.chromium.launch(headless=headless)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://legajounico.produccion.gob.ar/")
    await page.get_by_role("checkbox", name="He leído y acepto los té").check()
    await page.get_by_role("link", name="Ingresar").click()
    await page.get_by_role("spinbutton").click()
    await page.get_by_role("spinbutton").fill(cuit_representante)
    await page.get_by_role("button", name="Siguiente").click()
    await page.get_by_role("textbox", name="TU CLAVE").click()
    await page.get_by_role("textbox", name="TU CLAVE").fill(clave)
    await page.get_by_role("button", name="Ingresar").click()
    await page.get_by_role("combobox", name="Ingrese al menos 3 caracteres").click()
    await page.get_by_role("button", name="expand combobox").click()
    # selección de representado
    await page.get_by_text("- EL VASCO SA").click()
    await page.get_by_role("button", name="Seleccionar").click()
    async with page.expect_download() as download_info:
        await page.get_by_role("link", name=" Descargar Certificado MiPyME").click()
    download = await download_info.value

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        from dotenv import load_dotenv
        import os
        
        load_dotenv()
        CERTIFICADO_MIPYME_CUIT_REPRESENTANTE = os.getenv("CERTIFICADO_MIPYME_CUIT_REPRESENTANTE")
        CERTIFICADO_MIPYME_CLAVE = os.getenv("CERTIFICADO_MIPYME_CLAVE") 
        CERTIFICADO_MIPYME_CUIT_REPRESENTADO = os.getenv("CERTIFICADO_MIPYME_CUIT_REPRESENTADO")
        CERTIFICADO_MIPYME_HEADLESS = os.getenv("CERTIFICADO_MIPYME_HEADLESS").lower() == "true"
        
        await run(playwright)


asyncio.run(main())
