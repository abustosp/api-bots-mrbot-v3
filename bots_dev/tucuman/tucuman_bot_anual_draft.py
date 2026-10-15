import asyncio
import os
import re
from playwright.async_api import Playwright, async_playwright, expect

# Sanitizado: credenciales por variables de entorno (antes hardcodeadas).
TUCUMAN_CUIT = os.getenv("TUCUMAN_CUIT", "")
TUCUMAN_CLAVE = os.getenv("TUCUMAN_CLAVE", "")


async def run(playwright: Playwright) -> None:
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://auth.afip.gob.ar/contribuyente_/login.xhtml?action=SYSTEM")
    await page.get_by_role("spinbutton").click()
    await page.get_by_role("spinbutton").fill(TUCUMAN_CUIT)
    await page.get_by_role("spinbutton").press("Enter")
    await page.get_by_role("textbox", name="TU CLAVE").click()
    await page.get_by_role("textbox", name="TU CLAVE").fill(TUCUMAN_CLAVE)
    await page.get_by_role("button", name="Ingresar").click()
    await page.get_by_role("link", name="Ver todos").click()
    await page.locator("body").press("ControlOrMeta+f")
    async with page.expect_popup() as page1_info:
        await page.get_by_role("button", name="RENTAS TUCUMÁN Rentas Tucumán").click()
    page1 = await page1_info.value
    await page1.locator("div").filter(has_text="x").nth(3).click()
    await page1.get_by_role("button", name="Close").click()
    await page1.get_by_role("button", name="   Confirmar").click()
    await page1.get_by_role("link", name="Presentación de DJs Web").click()
    await page1.get_by_role("link", name="SiAPre WEB").click()
    await page1.get_by_role("link", name="F.611 / F.711 Web").click()
    await page1.locator("#anio").select_option("2017")
    await page1.get_by_role("button", name="   Buscar").click()
    await page1.get_by_role("button", name="✏   Cargar").click()
    await page1.get_by_text("DJ ANUAL PARA CIERRE DEL").click()
    await page1.get_by_label("F.711 Web - Ingresos Brutos").get_by_text("Cargar", exact=True).click()
    await page1.get_by_role("button", name="Vista Previa   ").click()
    await page1.get_by_role("button", name="Presentar   ").click()
    await page1.locator("#my_checkbox").check()
    await page1.get_by_role("button", name="Confirmar").click()
    async with page1.expect_popup() as page2_info:
        await page1.get_by_role("link", name="   Imprimir Acuse de Recibo").click()
    page2 = await page2_info.value
    async with page1.expect_popup() as page3_info:
        await page1.get_by_role("link", name="   Declaración Jurada Anual").click()
    page3 = await page3_info.value

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        await run(playwright)


asyncio.run(main())
