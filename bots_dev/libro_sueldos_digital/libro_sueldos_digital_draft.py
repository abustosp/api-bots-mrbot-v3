import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def libro_sueldos_digital(
    playwright: Playwright,
    cuit_representante: str = "20147130202",
    clave: str = "Martinj204",
    cuit_representado: str = "20147130202",
    periodo_desde: str = "202501",
    periodo_hasta: str = "202512",
    headless: bool = False
    ) -> None:
    browser = await playwright.chromium.launch(headless=headless)
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
        await page.get_by_role("button", name="LIBRO DE SUELDOS DIGITAL").click()
    page1 = await page1_info.value
    await page1.get_by_role("link", name="Liquidaciones y DDJJ").click()
    await page1.locator("#ctl00_ContentPlaceHolder1_regxpagina").select_option("5")
    await page1.goto("https://serviciossegsoc.afip.gob.ar/LibrodeSueldos/app/Contribuyente/LibroSueldos/Periodos.aspx")
    await page1.get_by_text("09/").click()
    await page1.locator("#ctl00_ContentPlaceHolder1_rptPadre_ctl00_uc_Liquidaciones_rptLiquidaciones_ctl00_VerLS").click()
    async with page1.expect_download() as download_info:
        await page1.get_by_role("button", name="DESCARGAR LIBRO SUELDOS").click()
    download = await download_info.value
    await page1.get_by_role("button", name="Salir", exact=True).click()
    await page1.get_by_text("08/").click()
    await page1.locator("#ctl00_ContentPlaceHolder1_rptPadre_ctl01_uc_Liquidaciones_rptLiquidaciones_ctl00_VerLS").click()
    async with page1.expect_download() as download1_info:
        await page1.get_by_role("button", name="DESCARGAR LIBRO SUELDOS").click()
    download1 = await download1_info.value
    await page1.get_by_role("button", name="Salir", exact=True).click()
    await page1.locator("#ctl00_ContentPlaceHolder1_rptPadre_ctl01_uc_Liquidaciones_rptLiquidaciones_ctl01_VerLS").click()
    async with page1.expect_download() as download2_info:
        await page1.get_by_role("button", name="DESCARGAR LIBRO SUELDOS").click()
    download2 = await download2_info.value
    await page1.get_by_role("button", name="Salir", exact=True).click()
    await page1.locator("#ctl00_ContentPlaceHolder1_rptPadre_ctl02_uc_Liquidaciones_rptLiquidaciones_ctl00_VerLS").click()
    # en algunos casos aparece el botón de generar libro sueldos digital, en otros no. Si aparece, se hace click y se descarga el libro sueldos digital generado.
    await page1.get_by_role("button", name="GENERAR LIBRO SUELDOS DIGITAL").click()
    async with page1.expect_download() as download3_info:
        await page1.get_by_role("button", name="DESCARGAR LIBRO SUELDOS").click()
    download3 = await download3_info.value

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    from dotenv import load_dotenv
    import os
    
    cuit_representante = os.getenv("LSD_CUIT_REPRESENTANTE", "")
    clave = os.getenv("LSD_CLAVE", "")
    cuit_representado = os.getenv("LSD_REPRESENTADO_CUIT", "")
    periodo_desde = os.getenv("PERIODO_DESDE", "")
    periodo_hasta = os.getenv("PERIODO_HASTA", "")
    headless = os.getenv("LSD_HEADLESS", "False").lower() == "true"
    
    async with async_playwright() as playwright:
        await libro_sueldos_digital(playwright)


asyncio.run(main())
