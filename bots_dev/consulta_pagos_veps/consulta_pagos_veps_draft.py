import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect
import os
from dotenv import load_dotenv

async def consulta_pagos_vep(
    playwright: Playwright,
    representante: str,
    representado: str,
    clave: str
) -> None:
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
    await page.get_by_role("spinbutton").click()
    await page.get_by_role("spinbutton").fill(representante)
    await page.get_by_role("button", name="Siguiente").click()
    await page.get_by_role("textbox", name="TU CLAVE").click()
    await page.get_by_role("textbox", name="TU CLAVE").fill(clave)
    await page.get_by_role("button", name="Ingresar").click()
    await page.get_by_role("link", name="Ver todos").click()
    async with page.expect_popup() as page1_info:
        await page.get_by_role("button", name="PRESENTACIÓN DE DDJJ Y PAGOS").click()
    page1 = await page1_info.value
    await page1.goto("https://seti.afip.gob.ar/setiweb/#/inicio")
    await page1.get_by_role("button", name="Entendido").click()
    await page1.get_by_text("paymentsPagosGenerá y consult").click()
    await page1.get_by_text("close").click()
    await page1.get_by_role("link", name="Consulta de VEP").click()
    await page1.get_by_role("combobox", name="Cuit del Contribuyente.").click()
    await page1.get_by_role("option", name=representado).click()
    await page1.get_by_role("combobox", name="12").click()
    await page1.get_by_role("option", name="72").click()
    await page1.get_by_role("button", name="Aplicar").click()
    await page1.get_by_role("button", name="download").click()
    async with page1.expect_download() as download_info:
        await page1.get_by_title("Exportar a CSV").click()
    download = await download_info.value

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        load_dotenv()
        representante = os.getenv("CONSULTA_PAGOS_VEP_CUIT_REPRESENTANTE")
        representado = os.getenv("CONSULTA_PAGOS_VEP_CUIT_REPRESENTADO")
        clave = os.getenv("CONSULTA_PAGOS_VEP_CLAVE")
        await consulta_pagos_vep(playwright, representante, representado, clave)


asyncio.run(main())
