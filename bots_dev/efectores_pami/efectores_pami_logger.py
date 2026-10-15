import asyncio
import csv
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from bs4 import BeautifulSoup
from dotenv import load_dotenv
from playwright.async_api import (
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    async_playwright,
)

load_dotenv()


INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _safe_step_name(step: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_")
    return safe or "step"


def safe_filename(value: str) -> str:
    cleaned = INVALID_FILENAME_RE.sub("_", value or "")
    cleaned = re.sub(r"\s+", "_", cleaned).strip("_")
    return cleaned or "efectores_pami"


class DebugCapture:
    def __init__(self, base_dir: str = "temp", prefix: str = "efectores_pami") -> None:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.run_dir = Path(base_dir) / f"run_{run_id}"
        self.logs_dir = self.run_dir / "logs"
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        for path in (self.logs_dir, self.screenshots_dir, self.html_dir):
            path.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger(f"{prefix}.{run_id}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            handler = logging.FileHandler(self.logs_dir / f"{prefix}.log")
            formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
            handler.setFormatter(formatter)
            self.logger.addHandler(handler)
            stream_handler = logging.StreamHandler()
            stream_handler.setFormatter(formatter)
            self.logger.addHandler(stream_handler)
            self.logger.propagate = False

        self.step = 0
        self.logger.info("run_dir=%s", self.run_dir)

    def log(self, message: str) -> None:
        self.logger.info("%s", message)

    async def capture(self, page, step: str) -> None:
        if page is None:
            self.logger.info("capture_skipped step=%s page=None", step)
            return

        self.step += 1
        safe_step = _safe_step_name(step)
        prefix = f"{self.step:03d}_{safe_step}"
        self.logger.info("step=%s name=%s url=%s", self.step, step, getattr(page, "url", ""))

        screenshot_path = self.screenshots_dir / f"{prefix}.png"
        html_path = self.html_dir / f"{prefix}.html"

        try:
            await page.screenshot(path=str(screenshot_path), full_page=True)
            self.logger.info("screenshot=%s", screenshot_path)
        except Exception:
            self.logger.exception("screenshot_failed step=%s", step)

        try:
            content = await page.content()
            html_path.write_text(content, encoding="utf-8")
            self.logger.info("html=%s", html_path)
        except Exception:
            self.logger.exception("html_failed step=%s", step)


def _normalize_table(headers: List[str], rows: List[List[str]]) -> Tuple[List[str], List[List[str]]]:
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

    return _normalize_table(headers, rows)


def write_csv(headers: List[str], rows: List[List[str]], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        if headers:
            writer.writerow(headers)
        writer.writerows(rows)


def log_print(debug: DebugCapture, message: str) -> None:
    debug.log(f"print={message}")
    print(message)


async def extract_table_from_page(page, debug: DebugCapture, step_name: str) -> Tuple[List[str], List[List[str]]]:
    try:
        html_content = await page.content()
        headers, rows = extract_table(html_content, "tabla-datos")
        debug.log(f"{step_name} rows={len(rows)} headers={len(headers)}")
        return headers, rows
    except Exception as exc:
        debug.log(f"{step_name}_failed error={exc}")
        return [], []


async def goto_and_capture(page, debug: DebugCapture, url: str, step_name: str) -> None:
    debug.log(f"goto url={url}")
    await page.goto(url, wait_until="domcontentloaded", timeout=60000)
    await debug.capture(page, step_name)


async def click_and_capture(page, debug: DebugCapture, locator, step_name: str) -> bool:
    try:
        await locator.click()
        try:
            await page.wait_for_load_state("networkidle", timeout=10000)
        except PlaywrightTimeoutError:
            debug.log(f"wait_networkidle_timeout step={step_name}")
        await debug.capture(page, step_name)
        return True
    except Exception as exc:
        debug.log(f"{step_name}_failed error={exc}")
        await debug.capture(page, f"{step_name}_failed")
        return False


async def wait_for_table(page, debug: DebugCapture, step_name: str) -> bool:
    try:
        await page.wait_for_selector("#tabla-datos", timeout=60000)
        await page.wait_for_selector("#tabla-datos tbody tr", timeout=60000)
        await debug.capture(page, step_name)
        return True
    except PlaywrightTimeoutError:
        debug.log(f"{step_name}_timeout")
        await debug.capture(page, f"{step_name}_timeout")
        return False


def attach_network_logging(page, debug: DebugCapture, domain_filter: str = "efectores.pami.org.ar") -> None:
    def _match(url: str) -> bool:
        return domain_filter in url

    page.on(
        "request",
        lambda req: debug.log(f"request method={req.method} url={req.url}")
        if _match(req.url)
        else None,
    )
    page.on(
        "response",
        lambda res: debug.log(f"response status={res.status} url={res.url}")
        if _match(res.url)
        else None,
    )
    page.on(
        "requestfailed",
        lambda req: debug.log(f"request_failed url={req.url} error={req.failure}")
        if _match(req.url)
        else None,
    )


async def ensure_login(page, debug: DebugCapture, pami_user: str, pami_pass: str) -> None:
    if not pami_user or not pami_pass:
        raise ValueError("Faltan variables de entorno PAMI_USER y/o PAMI_PASS")

    try:
        await page.locator("#c_usuario").wait_for(timeout=5000)
    except PlaywrightTimeoutError:
        debug.log("login_form_not_visible")
        return

    debug.log("login_form_visible")
    await debug.capture(page, "login_form")
    await page.locator("#c_usuario").fill(pami_user)
    await page.locator("#password").fill(pami_pass)
    await debug.capture(page, "login_filled")

    try:
        await page.get_by_role("button", name=re.compile(r"Ingresar", re.I)).click()
    except Exception:
        await page.locator("input[type='submit']").click()

    await page.wait_for_load_state("networkidle")
    await debug.capture(page, "login_submitted")


async def pami_efectores(playwright: Playwright, pami_user: str, pami_pass: str) -> Path:
    debug = DebugCapture()
    debug.log("bot_start")

    browser = await playwright.chromium.launch(headless=False)
    save_har = _env_flag("SAVE_HAR", default=True)
    har_path = debug.run_dir / "network.har" if save_har else None
    if har_path is not None:
        debug.log(f"har_path={har_path}")
        context = await browser.new_context(
            record_har_path=str(har_path),
            record_har_content="embed",
        )
    else:
        context = await browser.new_context()
    context.set_default_timeout(60000)
    page = await context.new_page()
    attach_network_logging(page, debug)
    await debug.capture(page, "new_page")

    start_url = (
        "https://efectores.pami.org.ar/pami_nc/HomeBanking/afiliadosConsulta.php?xgap_historial=reset"
    )
    login_url = "https://efectores.pami.org.ar/pami_efectores/login.php?xgap_historial=clear"
    home_url = "https://efectores.pami.org.ar/pami_efectores/"
    list_url = (
        "https://efectores.pami.org.ar/pami_nc/HomeBanking/"
        "afiliadosConsulta.php?pgnro=0&rp=1000&xgap_historial=reset&&orderby=&ordersort="
    )

    try:
        popup_page = None
        try:
            async with page.expect_popup(timeout=15000) as popup_info:
                await page.goto(home_url, wait_until="domcontentloaded", timeout=60000)
            await debug.capture(page, "home_url_loaded")
            popup_page = await popup_info.value
        except PlaywrightTimeoutError:
            await goto_and_capture(page, debug, home_url, "home_url_loaded")

        if popup_page is not None:
            await debug.capture(popup_page, "popup_opened")
            page = popup_page
            attach_network_logging(page, debug)

        await goto_and_capture(page, debug, login_url, "login_url_loaded")
        await ensure_login(page, debug, pami_user, pami_pass)

        try:
            await page.get_by_role("link", name=re.compile(r"Efectores", re.I)).hover()
            await debug.capture(page, "efectores_hovered")
        except Exception as exc:
            debug.log(f"efectores_hover_failed error={exc}")
            await debug.capture(page, "efectores_hover_failed")

        try:
            await page.get_by_role("link", name=re.compile(r"Padron", re.I)).hover()
            await debug.capture(page, "padron_hovered")
        except Exception as exc:
            debug.log(f"padron_hover_failed error={exc}")
            await debug.capture(page, "padron_hover_failed")

        clicked_padron = await click_and_capture(
            page,
            debug,
            page.get_by_role("link", name=re.compile(r"Consulta Padron Online", re.I)),
            "padron_online_clicked",
        )
        if not clicked_padron and "afiliadosConsulta.php" not in (page.url or ""):
            debug.log("padron_online_fallback_start_url")
            await goto_and_capture(page, debug, start_url, "start_url_loaded")

        await click_and_capture(
            page,
            debug,
            page.get_by_role("button", name=re.compile(r"Buscar", re.I)),
            "buscar_clicked",
        )
        await goto_and_capture(page, debug, list_url, "list_url_after_buscar")
        headers, rows = await extract_table_from_page(page, debug, "table_extract_after_buscar")

        if not rows:
            table_ready = await wait_for_table(page, debug, "table_ready_after_buscar")
            try:
                await page.locator("input[name=\"spag1\"]").fill("2")
                await debug.capture(page, "spag1_filled")
                await click_and_capture(
                    page,
                    debug,
                    page.get_by_role("button", name=re.compile(r"Ir", re.I)),
                    "ir_clicked",
                )
            except Exception as exc:
                debug.log(f"spag1_flow_failed error={exc}")
                await debug.capture(page, "spag1_flow_failed")

            try:
                await page.locator("input[name=\"rp\"]").fill("1000")
                await debug.capture(page, "rp_filled")
                await click_and_capture(
                    page,
                    debug,
                    page.get_by_role("button", name=re.compile(r"Mostrar", re.I)),
                    "mostrar_clicked",
                )
                await click_and_capture(
                    page,
                    debug,
                    page.get_by_role("button", name=re.compile(r"Mostrar", re.I)),
                    "mostrar_clicked_again",
                )
            except Exception as exc:
                debug.log(f"rp_flow_failed error={exc}")
                await debug.capture(page, "rp_flow_failed")

            table_ready = await wait_for_table(page, debug, "table_ready")
            if not table_ready:
                debug.log("table_fallback_list_url")
                await goto_and_capture(page, debug, list_url, "list_url_loaded")
                await wait_for_table(page, debug, "table_ready_after_fallback")
            headers, rows = await extract_table_from_page(page, debug, "table_extract_after_fallback")

        try:
            await page.get_by_role("link").filter(has_text=re.compile(r"^$")).click()
            await debug.capture(page, "empty_link_clicked")
        except Exception as exc:
            debug.log(f"empty_link_click_failed error={exc}")
            await debug.capture(page, "empty_link_click_failed")

        try:
            await page.locator("#cont-tabla-datos").click()
            await debug.capture(page, "cont_tabla_clicked")
        except Exception as exc:
            debug.log(f"cont_tabla_click_failed error={exc}")
            await debug.capture(page, "cont_tabla_click_failed")

        if not rows:
            headers, rows = await extract_table_from_page(page, debug, "table_extract_final")

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = debug.run_dir / f"{safe_filename('efectores_pami')}_{stamp}.csv"
        write_csv(headers, rows, output_path)
        debug.log(f"csv_saved path={output_path}")
        log_print(debug, f"CSV guardado en: {output_path}")
        await debug.capture(page, "csv_saved")
        return output_path
    except Exception as exc:
        debug.log(f"bot_error error={exc}")
        await debug.capture(page, "bot_error")
        raise
    finally:
        await _safe_close(context, browser, debug, har_path)


async def _safe_close(context, browser, debug: DebugCapture, har_path: Path | None = None) -> None:
    try:
        await context.close()
        debug.log("close_context")
    except Exception as exc:
        debug.log(f"close_context_failed error={exc}")
    if har_path is not None:
        try:
            if har_path.exists():
                debug.log(f"har_saved path={har_path} size={har_path.stat().st_size}")
            else:
                debug.log(f"har_missing path={har_path}")
        except Exception as exc:
            debug.log(f"har_check_failed error={exc}")
    try:
        await browser.close()
        debug.log("close_browser")
    except Exception as exc:
        debug.log(f"close_browser_failed error={exc}")


async def main() -> None:
    async with async_playwright() as playwright:
        await pami_efectores(
            playwright,
            os.getenv("PAMI_USER"),
            os.getenv("PAMI_PASS"),
        )


asyncio.run(main())
