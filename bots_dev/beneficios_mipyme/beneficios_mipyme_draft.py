import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def run(playwright: Playwright,
              cuit_representante: str,
              clave: str,
              cuit_representado: str,
              headless: bool = True
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
    async with page.expect_popup() as page2_info:
        await page.get_by_role("button", name="PYMES SOLICITUD DE").click()
    page2 = await page2_info.value
    # se debe seleccionar al representado, en algunos casos no se muestran opciones
    await page2.get_by_role("listbox").select_option("3")
    await page2.get_by_role("button", name="Aceptar").click()
    await page2.locator(".v-filterselect-button").first.click()
    await page2.get_by_text("Ver").click()
    await page2.get_by_role("button", name="Siguiente >").click()
    await page2.get_by_role("button", name="Siguiente >").click()
    await page2.get_by_role("button", name="Siguiente >").click()

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        from dotenv import load_dotenv
        import os
        load_dotenv()
        
        CERTIFICADO_MIPYME_CUIT_REPRESENTANTE = os.getenv("CERTIFICADO_MIPYME_CUIT_REPRESENTANTE")
        CERTIFICADO_MIPYME_CLAVE = os.getenv("CERTIFICADO_MIPYME_CLAVE") 
        CERTIFICADO_MIPYME_CUIT_REPRESENTADO = os.getenv("CERTIFICADO_MIPYME_CUIT_REPRESENTADO")
        CERTIFICADO_MIPYME_HEADLESS = os.getenv("CERTIFICADO_MIPYME_HEADLESS").lower() == "true"
        await run(playwright, CERTIFICADO_MIPYME_CUIT_REPRESENTANTE, CERTIFICADO_MIPYME_CLAVE, CERTIFICADO_MIPYME_CUIT_REPRESENTADO, CERTIFICADO_MIPYME_HEADLESS)


asyncio.run(main())
