"""
Runner secuencial para validar el login ARCA con resolución de CAPTCHA (bots_dev).

Uso (desde bots_dev/):
    venv/bin/python shared/_dev_captcha_login_check.py

Ejecuta UN login de ARCA en orden (sin concurrencia) con resolución de CAPTCHA
via CapMonster (ImageToTextTask). La key de CapMonster se toma del .env sin
imprimirse. La salida es sanitizada: nunca imprime CUIT/clave/key/imagen/HTML.
"""
from __future__ import annotations

import asyncio
import logging
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
from shared.proxy import get_proxy_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
LOGGER = logging.getLogger("arca_captcha_dev")

# CapMonster: si el .env no define ARCA_CAPMONSTER_API_KEY, reutilizamos la key
# ya presente del proyecto (SRT/RETPER apuntan a la misma cuenta CapMonster).
if not os.getenv("ARCA_CAPMONSTER_API_KEY"):
    for candidate in ("SRT_CAPMONSTER_API_KEY", "RETPER_CAPMONSTER_API_KEY"):
        value = os.getenv(candidate, "").strip()
        if value:
            os.environ["ARCA_CAPMONSTER_API_KEY"] = value
            break


def _sanitize_result(result) -> dict:
    logs = [str(x) for x in (result.error_logs or [])]
    return {
        "success": bool(result.success),
        "captcha_detected": bool(result.captcha_detected),
        "password_change_required": bool(result.password_change_required),
        "error_logs": logs[:8],
    }


async def main() -> int:
    cuit = os.getenv("ARCA_TEST_CUIT", "").strip()
    clave = os.getenv("ARCA_TEST_CLAVE", "").strip()
    if not cuit or not clave:
        print("DEV_CHECK skipped=missing_credentials")
        return 2
    if not os.getenv("ARCA_CAPMONSTER_API_KEY"):
        print("DEV_CHECK skipped=no_capmonster_key")
        return 2

    proxy = None
    try:
        proxy = get_proxy_config()
    except Exception as exc:
        print(f"DEV_CHECK proxy_error={type(exc).__name__}")
        return 2

    async with async_playwright() as pw:
        try:
            session = await start_arca_session(
                pw,
                cuit,
                clave,
                headless=True,
                proxy=proxy,
                fail_on_captcha=False,
                solve_arca_captcha=True,
            )
        except Exception as exc:
            print(f"DEV_CHECK exception={type(exc).__name__}: {str(exc)[:120]}")
            return 1

        if session is None:
            print("DEV_CHECK login_failed session=None")
            return 1

        result = _sanitize_result(session.login_result)
        print(f"DEV_CHECK login result={result}")
        try:
            await session.context.close()
            await session.browser.close()
        except Exception:
            pass
        return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))