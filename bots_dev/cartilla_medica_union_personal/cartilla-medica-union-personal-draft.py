import asyncio
import re
from playwright.async_api import Playwright, async_playwright, expect


async def run(playwright: Playwright) -> None:
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()
    await page.goto("https://www.unionpersonal.com.ar/cartilla")
    await page.get_by_role("combobox").click()
    await page.get_by_role("combobox").click()
    await page.get_by_role("option", name="Interior del país").click()
    await page.get_by_role("heading", name="Plan Classic").click()
    await page.get_by_role("combobox").click()
    await page.get_by_text("ODONTOLOGICA").click()
    await page.get_by_role("combobox").click()
    await page.get_by_text("MISIONES").click()
    await page.get_by_role("combobox").click()
    await page.get_by_text("ODONTOLOGIA GENERAL").click()
    await page.get_by_role("heading", name="VIERA SERGIO ARIEL").click()
    await page.get_by_text("AV 9 DE JULIO").click()
    await page.get_by_text("CAPIOVISIÑO").nth(5).click()
    await page.get_by_text("Tel: +54 3743 476570").click()
    await page.get_by_role("button", name="Siguiente →").click()
    await page.get_by_role("button", name="Siguiente →").click()
    await page.get_by_role("button", name="Siguiente →").click()
    await page.get_by_role("button", name="Siguiente →").click()
    await page.get_by_role("button", name="Siguiente →").click()

    # ---------------------
    await context.close()
    await browser.close()


async def main() -> None:
    async with async_playwright() as playwright:
        await run(playwright)


asyncio.run(main())
