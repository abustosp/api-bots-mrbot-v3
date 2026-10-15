import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def hacienda(
    playwright: Playwright,
    cuit_representante: str,
    cuit_representado: str,
    clave: str,
    denominacion: str,
    desde: str,
    hasta: str,
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
        await page.get_by_role("button", name="COMPROBANTES EN LÍNEA Sistema").click()
    page1 = await page1_info.value
    await page1.get_by_role("button", name=denominacion).click()
    async with page1.expect_popup() as page2_info:
        await page1.get_by_role("button", name="Hacienda y Carne - Liquidació").click()
    page2 = await page2_info.value
    await page2.close()
    async with page1.expect_popup() as page3_info:
        await page1.get_by_role("button", name="Hacienda y Carne - Liquidació").click()
    page3 = await page3_info.value
    await page3.get_by_role("button", name=denominacion).click()
    await page3.get_by_role("button").click()
    await page3.get_by_role("link", name="Consulta y ajuste de Liquidaciones - Por Emisor").click()
    await page3.get_by_role("textbox", name="Fecha comprobante desde").click()
    await page3.get_by_role("textbox", name="Fecha comprobante desde").click()
    await page3.get_by_text("Fecha comprobante desde febrero 2026lumamijuvisá").click()
    await page3.get_by_role("textbox", name="Fecha comprobante desde").fill(desde)
    await page3.get_by_role("textbox", name="Fecha comprobante hasta").click()
    await page3.get_by_role("textbox", name="Fecha comprobante hasta").fill(hasta)
    await page3.get_by_text("Buscar").click()
    await page3.get_by_role("button").click()
    await page3.get_by_role("link", name="Consulta y ajuste de Liquidaciones - Por Receptor").click()
    await page3.get_by_role("textbox", name="Fecha comprobante desde").click()
    await page3.get_by_role("textbox", name="Fecha comprobante desde").fill(desde)
    await page3.get_by_role("textbox", name="Fecha comprobante hasta").click()
    await page3.get_by_role("textbox", name="Fecha comprobante hasta").fill(hasta)
    await page3.get_by_text("Buscar").click()

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        from dotenv import load_dotenv
        import os
        load_dotenv()
        
        await hacienda(
            playwright,
            os.getenv("CUIT_REPRESENTANTE"),
            os.getenv("CUIT_REPRESENTADO"),
            os.getenv("CLAVE"),
            os.getenv("DENOMINACION"),
            os.getenv("DESDE"),
            os.getenv("HASTA"),
        )


asyncio.run(main())
