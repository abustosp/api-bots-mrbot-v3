import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect
import os
from dotenv import load_dotenv

async def carga931(
    playwright: Playwright,
    cuit_representante: str,
    clave: str,
    periodo: str) -> None:
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml")
    await page.get_by_role("spinbutton").click()
    await page.get_by_role("spinbutton").fill(cuit_representante)
    await page.get_by_role("button", name="Siguiente").click()
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml;jsessionid=Ve59z-B3-doNsl93FExBAFWNfJuTioRZ0gNG9LEA.auth3")
    await page.get_by_role("textbox", name="TU CLAVE").click()
    await page.get_by_role("textbox", name="TU CLAVE").fill(clave)
    await page.get_by_role("button", name="Ingresar").click()
    await page.locator("div").filter(has_text=re.compile(r"^Ver todos$")).nth(1).click()
    await page.locator("body").press("ControlOrMeta+f")
    async with page.expect_popup() as page1_info:
        await page.get_by_role("button", name="LIBRO DE SUELDOS DIGITAL").click()
    page1 = await page1_info.value
    await page1.get_by_role("link", name="Liquidaciones y DDJJ").click()
    await page1.get_by_text("/2026").click()
    await page1.get_by_role("button", name="Eliminar la liquidación").click()
    await page1.get_by_role("button", name="Confirmar").click()
    await page1.get_by_role("button", name="Borrar Período").click()
    await page1.get_by_role("button", name="Confirmar").click()
    await page1.get_by_role("link", name="Nuevo Período").click()
    await page1.get_by_role("textbox", name="Formato mm/aaaa").fill(periodo)
    await page1.get_by_role("textbox", name="Formato mm/aaaa").click()
    await page1.get_by_role("textbox", name="Formato mm/aaaa").fill(periodo)
    await page1.get_by_role("button", name="Agregar").click()
    await page1.get_by_text("/2026").click()
    await page1.get_by_role("link", name="Agregar Liquidación").click()
    await page1.locator("#ctl00_ContentPlaceHolder1_rptPadre_ctl00_TextBoxLiquidacion").click()
    await page1.locator("#ctl00_ContentPlaceHolder1_rptPadre_ctl00_TextBoxLiquidacion").fill("1")
    # selección de jurisdicción (ver menu desplegable)
    await page1.locator("#ctl00_ContentPlaceHolder1_rptPadre_ctl00_DropDownJurisdicciones").select_option("7")
    await page1.get_by_role("button", name="Agregar").click()
    await page1.get_by_role("button", name="Importar desde archivo").click()
    await page1.get_by_role("button", name="Choose File").click()
    await page1.get_by_role("button", name="Choose File").set_input_files("TXT_liquidaciones_202608_LUZ_CUYANA_SAS.txt")
    await page1.get_by_text("Subir").click()
    await page1.get_by_role("button", name="VALIDAR").click()
    await page1.get_by_text("% VALIDANDO").click()
    await page1.get_by_text("% VERIFICANDO").click()
    await page1.get_by_text("% VALIDANDO...").click()
    await page1.get_by_role("button", name="Inicio").click()
    await page1.get_by_role("button", name="F931 Borrador").click()
    async with page1.expect_popup() as page2_info:
        await page1.goto("https://serviciossegsoc.afip.gob.ar/LibrodeSueldos/app/Contribuyente/LibroSueldos/Periodos.aspx")
    page2 = await page2_info.value
    await page2.goto("https://serviciossegsoc.afip.gob.ar/djproforma/app/altadj/datosgenerales.aspx")
    await page2.locator("#bodyLayer").click()

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        load_dotenv()
        cuit_representante = os.getenv("CUIT_REPRESENTANTE_931")
        clave = os.getenv("CLAVE_REPRESENTANTE_931")
        periodo = os.getenv("PERIODO_931")
        await carga931(playwright)


asyncio.run(main())
