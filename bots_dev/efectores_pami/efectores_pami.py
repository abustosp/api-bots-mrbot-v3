import asyncio
import csv
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

load_dotenv()

HOME_URL = "https://efectores.pami.org.ar/pami_efectores/"
LOGIN_URL = "https://efectores.pami.org.ar/pami_efectores/login.php?xgap_historial=clear"
START_URL = "https://efectores.pami.org.ar/pami_nc/HomeBanking/afiliadosConsulta.php?xgap_historial=reset"
LIST_URL = (
    "https://efectores.pami.org.ar/pami_nc/HomeBanking/"
    "afiliadosConsulta.php?pgnro=0&rp=1000&xgap_historial=reset&&orderby=&ordersort="
)


def extract_table(html_content: str, table_id: str = "tabla-datos") -> Tuple[List[str], List[List[str]]]:
    soup = BeautifulSoup(html_content, "html.parser")
    table = soup.find("table", {"id": table_id})
    if not table:
        return [], []

    headers: List[str] = []
    thead = table.find("thead")
    if thead:
        header_row = thead.find("tr")
        if header_row:
            headers = [cell.get_text(strip=True) for cell in header_row.find_all(["th", "td"])]

    rows: List[List[str]] = []
    tbody = table.find("tbody")
    if tbody:
        for row in tbody.find_all("tr"):
            cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
            if cells:
                rows.append(cells)
    else:
        for row in table.find_all("tr"):
            cells = [cell.get_text(strip=True) for cell in row.find_all(["td", "th"])]
            if cells:
                rows.append(cells)

    if not rows:
        return headers, rows

    max_len = max(len(row) for row in rows)
    if not headers:
        headers = [f"col_{idx + 1}" for idx in range(max_len)]
    elif len(headers) < max_len:
        headers = headers + [f"col_{idx + 1}" for idx in range(len(headers), max_len)]
    elif len(headers) > max_len:
        headers = headers[:max_len]

    normalized_rows: List[List[str]] = []
    for row in rows:
        if len(row) < max_len:
            row = row + [""] * (max_len - len(row))
        elif len(row) > max_len:
            row = row[:max_len]
        normalized_rows.append(row)
    return headers, normalized_rows


def write_csv(headers: List[str], rows: List[List[str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        if headers:
            writer.writerow(headers)
        writer.writerows(rows)


async def get_popup_or_page(page):
    try:
        async with page.expect_popup(timeout=15000) as popup_info:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        return await popup_info.value
    except PlaywrightTimeoutError:
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
        return page


async def ensure_login(page, user: str, password: str) -> None:
    await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
    if not user or not password:
        raise ValueError("Faltan variables de entorno PAMI_USER y/o PAMI_PASS")
    if await page.locator("#c_usuario").count() == 0:
        return
    await page.locator("#c_usuario").fill(user)
    await page.locator("#password").fill(password)
    try:
        await page.get_by_role("button", name=re.compile(r"Ingresar", re.I)).click()
    except Exception:
        await page.locator("input[type='submit']").click()
    await page.wait_for_load_state("networkidle")


async def try_menu_flow(page) -> None:
    try:
        await page.get_by_role("link", name=re.compile(r"Efectores", re.I)).hover(timeout=5000)
        await page.get_by_role("link", name=re.compile(r"Padron", re.I)).hover(timeout=5000)
        await page.get_by_role(
            "link", name=re.compile(r"Consulta Padron Online", re.I)
        ).click(timeout=10000)
        await page.get_by_role("button", name=re.compile(r"Buscar", re.I)).click(timeout=10000)
    except Exception:
        return


async def fetch_table(page) -> Tuple[List[str], List[List[str]]]:
    await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
    try:
        await page.wait_for_selector("#tabla-datos tbody tr", timeout=20000)
    except PlaywrightTimeoutError:
        await page.goto(START_URL, wait_until="domcontentloaded", timeout=60000)
        await try_menu_flow(page)
        await page.goto(LIST_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_selector("#tabla-datos tbody tr", timeout=30000)

    html_content = await page.content()
    headers, rows = extract_table(html_content, "tabla-datos")
    if not rows:
        raise RuntimeError("No se pudieron extraer filas de la tabla.")
    return headers, rows


async def run() -> Path:
    user = os.getenv("PAMI_USER")
    password = os.getenv("PAMI_PASS")
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        try:
            page = await get_popup_or_page(page)
            await ensure_login(page, user, password)
            headers, rows = await fetch_table(page)
        finally:
            await context.close()
            await browser.close()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path("./descargas") / f"efectores_pami_{timestamp}.csv"
    write_csv(headers, rows, output_path)
    return output_path


if __name__ == "__main__":
    csv_path = asyncio.run(run())
    print(f"CSV guardado en: {csv_path}")
