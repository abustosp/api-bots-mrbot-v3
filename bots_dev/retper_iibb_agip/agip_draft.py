import asyncio
import re
import playwright
from playwright.async_api import Playwright, async_playwright, expect


async def bot_agip(
    playwright: Playwright,
    usuario: str,
    clave: str,
    cuit_representado: str,
    denominacion: str,
    desde: str,
    hasta: str,
    headless: bool = True
    ) -> None:
    
    browser = await playwright.chromium.launch(headless=headless)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://claveciudad.agip.gob.ar/")
    await page.get_by_role("button", name="Iniciar sesión").click()
    await page.get_by_role("button", name="Ingresar con CUIL o email").click()
    await page.get_by_role("textbox", name="CUIL o correo electronico *").click()
    await page.get_by_role("textbox", name="CUIL o correo electronico *").fill(usuario)
    await page.get_by_role("textbox", name="Contraseña").click()
    await page.locator("#password-text-field").fill(clave)
    await page.get_by_role("button", name="Iniciar sesión").click()
    await page.get_by_role("combobox").select_option(cuit_representado)
    await page.get_by_role("link", name="Gestion-AR Agtes. de").click()
    # selección desde
    await page.locator("#fechaDesdeCo").click()
    await page.get_by_text("Ene").click()
    # selección hasta
    await page.locator("#fechaHastaCo").click()
    await page.get_by_text("Mar", exact=True).click()
    # aplicación de filtros
    await page.get_by_role("button", name="Buscar").click()
    await page.locator("#divConsultado").get_by_text("Retenciones", exact=True).click()
    async with page.expect_download() as download_info:
        await page.locator("#tablaRetenciones_botonExcel").click()
    download = await download_info.value
    async with page.expect_download() as download1_info:
        await page.locator("#tablaRetenciones_botonTxt").click()
    download1 = await download1_info.value
    async with page.expect_download() as download2_info:
        await page.locator("#tablaRetenciones_botonTxtSifere").click()
    download2 = await download2_info.value
    await page.locator("#divConsultado").get_by_text("Percepciones", exact=True).click()
    async with page.expect_download() as download3_info:
        await page.locator("#tablaPercepciones_botonExcel").click()
    download3 = await download3_info.value
    async with page.expect_download() as download4_info:
        await page.locator("#tablaPercepciones_botonTxt").click()
    download4 = await download4_info.value
    async with page.expect_download() as download7_info:
        await page.locator("#tablaPercepciones_botonTxtSifere").click()
    download7 = await download7_info.value
    await page.locator("#divConsultado").get_by_text("Retenciones Bancarias").click()
    async with page.expect_download() as download8_info:
        await page.locator("#tablaRetencionesBancarias_botonExcel").click()
    download8 = await download8_info.value
    async with page.expect_download() as download9_info:
        await page.locator("#tablaRetencionesBancarias_botonTxt").click()
    download9 = await download9_info.value
    async with page.expect_download() as download11_info:
        await page.locator("#tablaRetencionesBancarias_botonTxtSifere").click()
    download11 = await download11_info.value

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        from dotenv import load_dotenv
        import os
        load_dotenv()
        
        usuario = os.getenv("AGIP_USUARIO")
        clave = os.getenv("AGIP_CLAVE")
        cuit_representado = os.getenv("AGIP_CUIT_REPRESENTADO")
        denominacion = os.getenv("AGIP_DENOMINACION")
        desde = os.getenv("AGIP_DESDE")
        hasta = os.getenv("AGIP_HASTA")
        headless = os.getenv("AGIP_HEADLESS", "True").lower() == "true"
        await bot_agip(playwright,
            usuario,
            clave,
            cuit_representado,
            denominacion,
            desde,
            hasta,
            headless
        )


asyncio.run(main())
