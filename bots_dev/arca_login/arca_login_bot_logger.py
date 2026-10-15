"""Bot de login ARCA (versión logger con evidencias).

Realiza el login en ARCA y deja evidencia por paso en
`./temp/arca_login_{timestamp}/{descargas,html,screenshots,har}`.
Resuelve el captcha de imagen con CapMonster apenas aparece (hasta 5 intentos
por captcha).

Variables de entorno:
    ARCA_LOGIN_CUIT / ARCA_TEST_CUIT
    ARCA_LOGIN_CLAVE / ARCA_TEST_CLAVE
    ARCA_LOGIN_HEADLESS (default true)
    ARCA_LOGIN_PROXY_REQUEST (default false)

Uso (desde bots_dev/):
    venv/bin/python arca_login/arca_login_bot_logger.py
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bots_dev"))

from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from playwright.async_api import async_playwright

from shared.arca_login import login_arca, launch_arca_browser_context, ARCA_LOGIN_URL
from shared.arca_captcha import get_arca_capmonster_config
from shared.proxy import get_proxy_config

LOGGER_PREFIX = "arca_login"


def _env_flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "y", "on")


def load_capmonster_key() -> str:
    if not os.getenv("ARCA_CAPMONSTER_API_KEY"):
        for candidate in ("SRT_CAPMONSTER_API_KEY", "RETPER_CAPMONSTER_API_KEY"):
            value = os.getenv(candidate, "").strip()
            if value:
                os.environ["ARCA_CAPMONSTER_API_KEY"] = value
                break
    return get_arca_capmonster_config()["api_key"]


def _sanitize_result(result) -> dict:
    logs = [str(x) for x in (result.error_logs or [])]
    return {
        "success": bool(result.success),
        "captcha_detected": bool(result.captcha_detected),
        "password_change_required": bool(result.password_change_required),
        "error_logs": logs[:8],
    }


class DebugCapture:
    def __init__(self) -> None:
        self.run_dir = Path(__file__).resolve().parents[1] / "temp" / (
            f"{LOGGER_PREFIX}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        self.screenshots_dir = self.run_dir / "screenshots"
        self.html_dir = self.run_dir / "html"
        self.har_dir = self.run_dir / "har"
        self.descargas_dir = self.run_dir / "descargas"
        for path in (self.screenshots_dir, self.html_dir, self.har_dir, self.descargas_dir):
            path.mkdir(parents=True, exist_ok=True)
        self.step = 0

    def log(self, message: str) -> None:
        print(f"[{LOGGER_PREFIX}] {message}", flush=True)

    async def capture(self, page, step: str) -> None:
        if page is None:
            return
        self.step += 1
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "_", step).strip("_") or "step"
        prefix = f"{self.step:03d}_{safe}"
        try:
            await page.screenshot(path=str(self.screenshots_dir / f"{prefix}.png"), full_page=True)
        except Exception:
            pass
        try:
            html = await page.content()
            (self.html_dir / f"{prefix}.html").write_text(html, encoding="utf-8")
        except Exception:
            pass


async def main() -> int:
    debug = DebugCapture()
    debug.log(f"run_dir={debug.run_dir}")

    cuit = (os.getenv("ARCA_LOGIN_CUIT") or os.getenv("ARCA_TEST_CUIT") or "").strip()
    clave = (os.getenv("ARCA_LOGIN_CLAVE") or os.getenv("ARCA_TEST_CLAVE") or "").strip()
    if not cuit or not clave:
        print("ARCA_LOGIN skipped=missing_credentials")
        return 2
    if not load_capmonster_key():
        print("ARCA_LOGIN skipped=no_capmonster_key")
        return 2

    headless = _env_flag("ARCA_LOGIN_HEADLESS", "true")
    proxy_request = _env_flag("ARCA_LOGIN_PROXY_REQUEST", "false")
    proxy = get_proxy_config() if proxy_request else None
    har_path = debug.har_dir / f"{LOGGER_PREFIX}.har"

    async with async_playwright() as pw:
        browser = context = page = None
        try:
            browser, context, page = await launch_arca_browser_context(
                pw,
                headless=headless,
                proxy=proxy,
                context_kwargs={
                    "record_har_path": str(har_path),
                    "record_har_content": "embed",
                },
            )
            await debug.capture(page, "inicio")

            logs: list[str] = []
            result = await login_arca(
                page,
                cuit,
                clave,
                error_logs=logs,
                fail_on_captcha=False,
                solve_arca_captcha=True,
            )
            captcha = result.captcha_detected or any("Captcha" in x for x in logs)
            await debug.capture(page, "captcha" if captcha else "post_login")

            sanitized = _sanitize_result(result)
            debug.log(f"login result={sanitized}")
            debug.log(f"url_final={ARCA_LOGIN_URL}")
            await debug.capture(page, "final")
            return 0 if result.success else 1
        finally:
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    pass
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
