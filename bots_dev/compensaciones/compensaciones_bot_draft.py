import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def compensaciones(
    playwright: Playwright,
    cuit_representante: str,
    clave_representante: str,
    cuit_representado: str,
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
    await page.get_by_role("textbox", name="TU CLAVE").fill(clave_representante)
    await page.get_by_role("button", name="Ingresar").click()
    await page.get_by_role("link", name="Ver todos").click()
    async with page.expect_popup() as page1_info:
        await page.get_by_role("button", name="SISTEMA DE CUENTAS").click()
    page1 = await page1_info.value
    # Seleccionar la opción en base al CUIT
    await page1.get_by_role("combobox").select_option("0")
    await page1.goto("https://ctacte.cloud.afip.gob.ar/contribuyente/externo")
    await page1.get_by_role("cell", name=" Consultas").click()
    await page1.get_by_role("cell", name="Consulta Compensaciones y").click()
    # convertid de yyyymmdd a dd/mm/yyyy
    desde = desde [:4] + "/" + desde[4:6] + "/" + desde[6:]
    hasta = hasta [:4] + "/" + hasta[4:6] + "/" + hasta[6:]
    await page1.locator("iframe").content_frame.get_by_role("textbox", name="Fecha Desde").click()
    await page1.locator("iframe").content_frame.get_by_role("textbox", name="Fecha Desde").fill(desde)
    await page1.locator("iframe").content_frame.get_by_role("textbox", name="Fecha Hasta").click()
    await page1.locator("iframe").content_frame.get_by_role("textbox", name="Fecha Hasta").fill(hasta)
    await page1.locator("iframe").content_frame.get_by_role("button", name=" CONSULTAR").click()
    await page1.locator("iframe").content_frame.get_by_role("button", name="Exportar").click()
    async with page1.expect_download() as download_info:
        await page1.locator("iframe").content_frame.get_by_role("button", name=" XLS").click()
    download = await download_info.value
    await page1.locator("iframe").content_frame.get_by_role("button", name="Exportar").click()
    async with page1.expect_download() as download1_info:
        await page1.locator("iframe").content_frame.get_by_role("button", name=" CSV").click()
    download1 = await download1_info.value
    await page1.locator("iframe").content_frame.get_by_role("button", name="Exportar").click()
    async with page1.expect_download() as download2_info:
        await page1.locator("iframe").content_frame.get_by_role("button", name=" PDF").click()
    download2 = await download2_info.value

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    import os
    import dotenv
    
    dotenv.load_dotenv()
    cuit_representante = os.getenv("CUIT_REPRESENTANTE_SCT_COMPENSACIONES")
    clave_representante = os.getenv("CLAVE_REPRESENTANTE_SCT_COMPENSACIONES")
    cuit_representado = os.getenv("CUIT_REPRESENTADO_SCT_COMPENSACIONES")
    desde = os.getenv("DESDE_COMPENSACIONES")
    hasta = os.getenv("HASTA_COMPENSACIONES")
    
    async with async_playwright() as playwright:
        await compensaciones(playwright)


asyncio.run(main())
