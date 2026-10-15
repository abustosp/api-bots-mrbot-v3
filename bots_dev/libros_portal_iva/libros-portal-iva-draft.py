import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def run(
    playwright: Playwright,
    cuit_representante: str,
    clave: str,
    cuit_representado: str,
    head: bool,
    ) -> None:
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
    await page.get_by_role("spinbutton").click()
    await page.get_by_role("spinbutton").fill(cuit_representado)
    await page.get_by_role("button", name="Siguiente").click()
    await page.get_by_role("textbox", name="TU CLAVE").click()
    await page.get_by_role("textbox", name="TU CLAVE").fill(clave)
    await page.get_by_role("textbox", name="TU CLAVE").press("Enter")
    await page.get_by_role("link", name="Ver todos").click()
    async with page.expect_popup() as page1_info:
        await page.get_by_role("button", name="PORTAL IVA Presentación del").click()
    page1 = await page1_info.value
    await page1.get_by_title("cambio relación").click()
    await page1.get_by_title("Representar a...").click()
    await page1.get_by_role("button", name="Sin texto (iva.home.btn.declaraciones.anteriores.alt)").click()
    await page1.get_by_role("button", name="Sin texto (iva.home.btn.declaraciones.anteriores.LibroIVA.alt)").click()
    await page1.get_by_title("Ver").first.click()
    async with page1.expect_download() as download_info:
        async with page1.expect_popup() as page2_info:
            await page1.locator("#btnDescargarVistaPrevia").click()
        page2 = await page2_info.value
    download = await download_info.value
    await page2.close()
    async with page1.expect_download() as download1_info:
        await page1.locator("#btnDescargarAcuse").click()
    download1 = await download1_info.value
    async with page1.expect_download() as download2_info:
        await page1.locator("#btnDescargarPresentacion").click()
    download2 = await download2_info.value
    await page1.get_by_role("link", name="Datos Iniciales").click()
    await page1.get_by_role("link", name="Inicio").click()
    await page1.get_by_role("button", name="Sin texto (iva.home.btn.declaraciones.anteriores.alt)").click()
    await page1.get_by_role("button", name="Sin texto (iva.home.btn.declaraciones.anteriores.portal.alt)").click()
    await page1.get_by_title("Descargar formulario").first.click()
    await page1.locator("[id=\"fa fa-print\"]").click()

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    from dotenv import load_dotenv
    import os
    load_dotenv()
    
    cuit_representante = os.getenv("PORTAL_IVA_LIBROS_CUIT_REPRESENTANTE")
    cuit_representado = os.getenv("PORTAL_IVA_LIBROS_CUIT_REPRESENTADO")
    clave = os.getenv("PORTAL_IVA_LIBROS_CLAVE")
    headless = os.getenv("PORTAL_IVA_LIBROS_HEADLESS", "true").lower() == "true"
    async with async_playwright() as playwright:
        await run(playwright, cuit_representante, clave, cuit_representado, headless)


asyncio.run(main())
