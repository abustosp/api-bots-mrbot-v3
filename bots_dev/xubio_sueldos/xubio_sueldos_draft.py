import asyncio
import os
import re
from playwright.async_api import Playwright, async_playwright, expect

# Sanitizado: credenciales por variables de entorno (antes hardcodeadas).
XUBIO_EMAIL = os.getenv("XUBIO_EMAIL", "")
XUBIO_PASSWORD = os.getenv("XUBIO_PASSWORD", "")


async def run(playwright: Playwright) -> None:
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://connect.visma.com/?returnUrl=%2Fconnect%2Fauthorize%2Fcallback%3Fredirect_uri%3Dhttps%253A%252F%252Fxubio.com%252FNXV%252FvismaConnect%252Fcallback%26response_type%3Dcode%2520id_token%26scope%3Dopenid%2520email%26response_mode%3Dform_post%26client_id%3Dxubio%26state%3Dxubio%26nonce%3D020fd21c41c84faff3981abbdbfcb7dc")
    await page.get_by_role("button", name="Rechazarlas todas").click()
    await page.get_by_role("textbox", name="Email").click()
    await page.get_by_role("textbox", name="Email").fill(XUBIO_EMAIL)
    await page.get_by_role("button", name="Continuar").click()
    await page.get_by_role("textbox", name="Contraseña").click()
    await page.get_by_role("textbox", name="Contraseña").fill(XUBIO_PASSWORD)
    await page.get_by_role("button", name="Iniciar sesión").click()
    await page.goto("https://xubio.com/NXV/sueldos/informes/afip-libro-sueldos-digital")
    await page.locator("#iframe").content_frame.get_by_role("button", name="✕").click()
    await page.locator("#iframe").content_frame.get_by_role("textbox", name="Periodo Mensual").click()
    await page.locator("#iframe").content_frame.get_by_role("textbox", name="Periodo Mensual").fill("2026-02")
    await page.locator("#iframe").content_frame.get_by_role("textbox", name="Periodo Mensual").click()
    await page.locator("#iframe").content_frame.get_by_role("textbox", name="Periodo Mensual").fill("2026-07")
    await page.locator("#iframe").content_frame.get_by_role("button", name="Exportar TXT Liquidaciones").click()
    await page.locator("#iframe").content_frame.get_by_role("spinbutton", name="Número de Liquidación AFIP").click()
    await page.locator("#iframe").content_frame.get_by_role("spinbutton", name="Número de Liquidación AFIP").fill("00")
    async with page.expect_download() as download_info:
        await page.locator("#iframe").content_frame.get_by_role("button", name="Exportar").click()
    download = await download_info.value
    async with page.expect_download() as download1_info:
        await page.locator("#iframe").content_frame.get_by_role("button", name="Exportar TXT Conceptos").click()
    download1 = await download1_info.value
    await page.goto("https://xubio.com/NXV/sueldos/informes/reporte-sueldos")
    await page.locator("#iframe").content_frame.get_by_role("button", name="Filtros").click()
    await page.locator("#iframe").content_frame.get_by_role("textbox", name="Periodo de Liquidación Desde").click()
    await page.locator("#iframe").content_frame.get_by_role("textbox", name="Periodo de Liquidación Desde").fill("1999-07")
    await page.locator("#iframe").content_frame.locator("mat-form-field").filter(has_text="Periodo de Liquidación Desde").get_by_label("Open calendar").click()
    await page.locator("#iframe").content_frame.locator("mat-form-field").filter(has_text="Periodo de Liquidación Desde").get_by_label("Open calendar").click()
    await page.locator("#iframe").content_frame.locator("tbody").get_by_text("1999").click()
    await page.locator("#iframe").content_frame.get_by_role("button", name="Next year").click()
    await page.locator("#iframe").content_frame.get_by_role("button", name="Choose date").click()
    await page.locator("#iframe").content_frame.get_by_role("button", name="Choose month and year").click()
    await page.locator("#iframe").content_frame.get_by_role("button", name="Next 24 years").click()
    await page.locator("#iframe").content_frame.get_by_role("button", name="2026").click()
    await page.locator("#iframe").content_frame.get_by_role("button", name="-07").click()
    await page.locator("#iframe").content_frame.locator("mat-form-field").filter(has_text="Periodo de Liquidación Hasta").get_by_label("Open calendar").click()
    await page.locator("#iframe").content_frame.get_by_role("button", name="-07").click()
    async with page.expect_download() as download2_info:
        await page.locator("#iframe").content_frame.get_by_role("button", name="Exportar").click()
    download2 = await download2_info.value
    async with page.expect_download() as download3_info:
        await page.locator("#iframe").content_frame.get_by_role("button", name="Control de Liquidación").click()
    download3 = await download3_info.value
    await page.goto("https://xubio.com/NXV/home")

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        await run(playwright)


asyncio.run(main())
