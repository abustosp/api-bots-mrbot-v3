import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def run(
    playwright: Playwright,
    cuit: str,
    clave: str,
    archivo: str,
) -> None:
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
    await page.get_by_role("spinbutton").fill(cuit)
    await page.get_by_role("button", name="Siguiente").click()
    await page.get_by_role("textbox", name="TU CLAVE").fill(clave)
    await page.get_by_role("button", name="Ingresar").click()
    await page.get_by_role("link", name="Ver todos").click()
    async with page.expect_popup() as page2_info:
        await page.get_by_role("button", name="PRESENTACIÓN DE DDJJ Y PAGOS").click()
    page2 = await page2_info.value
    await page2.goto("https://seti.afip.gob.ar/setiweb/#/inicio")
    await page2.get_by_role("button", name="Close").click()
    await page2.get_by_text("Pagos", exact=True).click()
    await page2.get_by_text("close").click()
    await page2.get_by_role("button", name="Generar VEP").click()
    await page2.get_by_text("description Desde archivo").click()
    await page2.get_by_role("button", name="Close").click()
    await page2.get_by_role("button", name="Buscá en tus archivos").click()
    await page2.get_by_role("button", name="Buscá en tus archivos").set_input_files(archivo)
    await page2.get_by_role("button", name="Generar VEP").click()
    await page2.get_by_text("close").click()
    await page2.get_by_role("button", name="Seleccioná medio de pago").click()
    await page2.locator("[id=\"1005\"]").click()
    await page2.get_by_role("button", name="Aceptar").click()
    async with page2.expect_download() as download_info:
        await page2.get_by_role("button", name="Descargar VEP").click()
    download = await download_info.value

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        await run(playwright)


asyncio.run(main())
