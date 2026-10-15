import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def controladores_fiscales(
    playwright: Playwright,
    cuit_representante: str,
    clave: str
) -> None:
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
    await page.get_by_role("spinbutton").click()
    await page.get_by_role("spinbutton").fill(cuit_representante)
    await page.get_by_role("button", name="Siguiente").click()
    await page.get_by_role("textbox", name="TU CLAVE").click()
    await page.get_by_role("textbox", name="TU CLAVE").fill(clave)
    await page.get_by_role("button", name="Ingresar").click()
    await page.get_by_role("link", name="Ver todos").click()
    async with page.expect_popup() as page1_info:
        await page.get_by_role("button", name="PRESENTACIÓN DDJJ Y PAGOS -").click()
    page1 = await page1_info.value
    await page1.get_by_role("button", name="Buscá en tus archivos").click()
    await page1.get_by_role("button", name="Buscá en tus archivos").set_input_files("F801")
    await page1.get_by_role("button", name="Buscá en tus archivos").click()
    await page1.get_by_role("button", name="Buscá en tus archivos").set_input_files("F8010.30692621049.HSHSAB0000025848.20260430.537049BB-4E58-48B9-B146-B53AD3DB5681.pem")
    await page1.get_by_role("button", name="Continuar").click()
    await page1.goto("https://seti.afip.gob.ar/setiweb/#/presentacion/confirmacion")
    await page1.get_by_text("La DJ seleccionada ya ha sido").click()
    await page1.get_by_role("button", name="Volver").click()
    await page1.get_by_role("button", name="Buscá en tus archivos").click()
    await page1.get_by_role("button", name="Buscá en tus archivos").set_input_files("F8011.30692621049.HSHSAB0000027203.20260430.9349CC7D-4E67-4A7B-AAC3-14856B17BB47.pem")
    await page1.get_by_role("button", name="Continuar").click()
    await page1.get_by_text("La DJ seleccionada ya ha sido").click()

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        import os
        import dotenv
        dotenv.load_dotenv()
        
        cuit_representante = os.getenv("CONTROLADORES_FISCALES_CUIT_REPRESENTANTE")
        clave = os.getenv("CONTROLADORES_FISCALES_CLAVE")
        
        await controladores_fiscales(playwright)


asyncio.run(main())
