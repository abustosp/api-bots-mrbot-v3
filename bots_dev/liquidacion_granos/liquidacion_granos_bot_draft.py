import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def liquidacion_granos(
    playwright: Playwright,
    cuit_representado: str,
    cuit_representante: str,
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
        await page.get_by_role("button", name="LIQUIDACIÓN PRIMARIA DE").click()
    page1 = await page1_info.value
    await page1.get_by_role("button", name=denominacion).click()
    await page1.get_by_role("button", name="Liquidación Primaria de Granos").click()
    await page1.get_by_role("button", name="Consulta de Liquidaciones").click()
    await page1.locator("input[name=\"fechaStr\"]").click()
    await page1.locator("input[name=\"fechaStr\"]").fill(desde)
    await page1.locator("input[name=\"fechaHastaStr\"]").click()
    await page1.locator("input[name=\"fechaHastaStr\"]").fill(hasta)
    await page1.get_by_role("button", name="Consultar Por Criterio").click()
    async with page1.expect_download() as download_info:
        await page1.get_by_role("row", name="03/08/2015 20937390581 330104384092 WEB Compraventa de granos Activo Ver", exact=True).get_by_role("link").click()
    download = await download_info.value
    async with page1.expect_download() as download1_info:
        await page1.get_by_role("link", name="Ver").nth(1).click()
    download1 = await download1_info.value
    await page1.get_by_role("button", name="Menú principal").click()
    await page1.get_by_role("button", name="Liquidación Primaria de Granos").click()
    await page1.get_by_role("button", name="Consulta Liquidaciones Recibidas").click()
    await page1.locator("input[name=\"fechaStr\"]").click()
    await page1.locator("input[name=\"fechaStr\"]").fill(desde)
    await page1.locator("input[name=\"fechaHastaStr\"]").click()
    await page1.locator("input[name=\"fechaHastaStr\"]").fill(hasta)
    await page1.get_by_role("button", name="Consultar Por Criterio").click()
    async with page1.expect_download() as download2_info:
        await page1.get_by_role("row", name="29/08/2024 330126531892 30709892750 WS Compraventa de granos COOPERATIVA AGROPECUARIA GOYA LIMITADA Activo COMPRADOR Ver Liquidación", exact=True).get_by_role("link").click()
    download2 = await download2_info.value
    async with page1.expect_download() as download3_info:
        await page1.get_by_role("row", name="16/09/2024 330126647990 30709892750 WS Compraventa de granos COOPERATIVA AGROPECUARIA GOYA LIMITADA Activo COMPRADOR Ver Liquidación", exact=True).get_by_role("link").click()
    download3 = await download3_info.value
    await page1.get_by_role("button", name="Menú principal").click()
    await page1.get_by_role("button", name="Liquidación Secundaria de").click()
    await page1.get_by_role("button", name="Consulta de Liquidaciones Emitidas").click()
    await page1.locator("input[name=\"fechaStr\"]").click()
    await page1.locator("input[name=\"fechaStr\"]").fill(desde)
    await page1.locator("input[name=\"fechaHastaStr\"]").click()
    await page1.locator("input[name=\"fechaHastaStr\"]").press("ArrowRight")
    await page1.locator("input[name=\"fechaHastaStr\"]").press("ArrowRight")
    await page1.locator("input[name=\"fechaHastaStr\"]").press("ArrowRight")
    await page1.locator("input[name=\"fechaHastaStr\"]").press("ArrowRight")
    await page1.locator("input[name=\"fechaHastaStr\"]").press("ArrowRight")
    await page1.locator("input[name=\"fechaHastaStr\"]").press("ArrowRight")
    await page1.locator("input[name=\"fechaHastaStr\"]").press("ArrowRight")
    await page1.locator("input[name=\"fechaHastaStr\"]").fill(hasta)
    await page1.get_by_role("button", name="Consultar Por Criterio").click()
    await page1.get_by_role("button", name="Menú principal").click()
    await page1.get_by_role("button", name="Liquidación Secundaria de").click()
    await page1.get_by_role("button", name="Consulta de Liquidaciones Recibidas").click()
    await page1.locator("input[name=\"fechaStr\"]").click()
    await page1.locator("input[name=\"fechaStr\"]").fill(desde)
    await page1.locator("input[name=\"fechaHastaStr\"]").click()
    await page1.locator("input[name=\"fechaHastaStr\"]").fill(hasta)
    await page1.get_by_role("button", name="Consultar Por Criterio").click()
    await page1.get_by_role("button", name="Menú principal").click()

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        from dotenv import load_dotenv
        import os
        load_dotenv()
        cuit_representado = os.getenv("CUIT_REPRESENTADO")
        cuit_representante = os.getenv("CUIT_REPRESENTANTE")
        clave = os.getenv("CLAVE")
        denominacion = os.getenv("DENOMINACION")
        desde = os.getenv("DESDE")
        hasta = os.getenv("HASTA")
        
        await liquidacion_granos(playwright, cuit_representado, cuit_representante, clave, denominacion, desde, hasta)


asyncio.run(main())
