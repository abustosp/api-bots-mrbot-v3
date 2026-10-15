"""Bot de login ARCA (versión limpia).

Realiza el login en ARCA con CUIT/clave usando `shared.arca_login` y resuelve el
captcha de imagen con CapMonster apenas aparece (hasta 5 intentos por captcha).

Variables de entorno:
    ARCA_LOGIN_CUIT / ARCA_TEST_CUIT
    ARCA_LOGIN_CLAVE / ARCA_TEST_CLAVE
    ARCA_LOGIN_HEADLESS (default true)
    ARCA_LOGIN_PROXY_REQUEST (default false)

Uso (desde bots_dev/):
    venv/bin/python arca_login/arca_login_bot.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bots_dev"))

from dotenv import load_dotenv
load_dotenv(REPO / ".env")

from playwright.async_api import async_playwright

from shared.arca_login import start_arca_session
from shared.arca_captcha import get_arca_capmonster_config
from shared.proxy import get_proxy_config


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


async def bot_arca_login(
    cuit: str,
    clave: str,
    *,
    headless: bool = True,
    proxy_request: bool = False,
) -> dict:
    proxy = get_proxy_config() if proxy_request else None
    async with async_playwright() as pw:
        session = await start_arca_session(
            pw,
            cuit,
            clave,
            headless=headless,
            proxy=proxy,
            fail_on_captcha=False,
            solve_arca_captcha=True,
        )
        if session is None:
            return {"success": False, "captcha_detected": False, "error_logs": ["login_failed"]}
        try:
            return _sanitize_result(session.login_result)
        finally:
            try:
                await session.context.close()
                await session.browser.close()
            except Exception:
                pass


async def main() -> int:
    cuit = (os.getenv("ARCA_LOGIN_CUIT") or os.getenv("ARCA_TEST_CUIT") or "").strip()
    clave = (os.getenv("ARCA_LOGIN_CLAVE") or os.getenv("ARCA_TEST_CLAVE") or "").strip()
    if not cuit or not clave:
        print("ARCA_LOGIN skipped=missing_credentials")
        return 2
    if not load_capmonster_key():
        print("ARCA_LOGIN skipped=no_capmonster_key")
        return 2

    result = await bot_arca_login(
        cuit,
        clave,
        headless=_env_flag("ARCA_LOGIN_HEADLESS", "true"),
        proxy_request=_env_flag("ARCA_LOGIN_PROXY_REQUEST", "false"),
    )
    print(f"ARCA_LOGIN result={result}")
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
