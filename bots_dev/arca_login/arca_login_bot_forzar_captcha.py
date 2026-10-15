"""Script de desarrollo: fuerza la aparición del captcha de ARCA y lo resuelve.

Solo a efectos de facilitar el desarrollo del caso particular. Reutiliza una
única sesión de navegador (misma IP/proxy) y repite intentos de login con una
clave deliberadamente incorrecta hasta que ARCA muestra el captcha; en cuanto
aparece se resuelve con CapMonster (hasta 5 intentos por captcha).

Variables de entorno:
    ARCA_FORCE_CUIT (default ARCA_LOGIN_CUIT / ARCA_TEST_CUIT)
    ARCA_FORCE_CLAVE (clave incorrecta para forzar el captcha)
    ARCA_FORCE_CLAVE_CORRECTA (opcional; si se define, al validar el captcha
        se reintenta el login con esta clave y se espera success=True)
    ARCA_FORCE_MAX_INTENTOS (default 10)
    ARCA_FORCE_HEADLESS (default true)
    ARCA_FORCE_PROXY_REQUEST (default true)
    ARCA_FORCE_EVIDENCIAS (default true)

Uso (desde bots_dev/):
    venv/bin/python arca_login/arca_login_bot_forzar_captcha.py
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

from shared.arca_login import launch_arca_browser_context, login_arca
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


def _sanitize_logs(logs) -> list[str]:
    safe = []
    for item in logs or []:
        text = str(item)
        text = re.sub(r"\b\d{10,11}\b", "***", text)
        safe.append(text)
    return safe


class Evidencias:
    def __init__(self, habilitado: bool) -> None:
        self.habilitado = habilitado
        base_temp = Path(__file__).resolve().parents[1] / "temp"
        self.run_dir = base_temp / f"arca_login_captcha_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        if habilitado:
            for sub in ("screenshots", "html"):
                (self.run_dir / sub).mkdir(parents=True, exist_ok=True)

    async def capturar(self, page, nombre: str) -> None:
        if not self.habilitado or page is None:
            return
        try:
            await page.screenshot(path=str(self.run_dir / "screenshots" / f"{nombre}.png"), full_page=True)
        except Exception:
            pass
        try:
            html = await page.content()
            (self.run_dir / "html" / f"{nombre}.html").write_text(html, encoding="utf-8")
        except Exception:
            pass


async def launch_with_retry(pw, *, headless: bool, proxy, attempts: int = 5):
    last_exc = None
    for i in range(1, attempts + 1):
        try:
            return await launch_arca_browser_context(pw, headless=headless, proxy=proxy)
        except Exception as exc:
            last_exc = exc
            print(f"FORCE_CAPTCHA browser_fail intento={i} error={type(exc).__name__}", flush=True)
            await asyncio.sleep(1.5)
    raise last_exc


async def main() -> int:
    cuit = (os.getenv("ARCA_FORCE_CUIT") or os.getenv("ARCA_LOGIN_CUIT") or os.getenv("ARCA_TEST_CUIT") or "").strip()
    clave_erronea = (os.getenv("ARCA_FORCE_CLAVE") or os.getenv("ARCA_LOGIN_CLAVE_INCORRECTA") or os.getenv("ARCA_TEST_CLAVE") or "").strip()
    clave_correcta = (os.getenv("ARCA_FORCE_CLAVE_CORRECTA") or "").strip()
    max_intentos = int(os.getenv("ARCA_FORCE_MAX_INTENTOS", "10") or "10")
    headless = _env_flag("ARCA_FORCE_HEADLESS", "true")
    proxy_request = _env_flag("ARCA_FORCE_PROXY_REQUEST", "true")
    evidencias = Evidencias(_env_flag("ARCA_FORCE_EVIDENCIAS", "true"))

    if not cuit or not clave_erronea:
        print("FORCE_CAPTCHA skipped=missing_credentials")
        return 2
    if not load_capmonster_key():
        print("FORCE_CAPTCHA skipped=no_capmonster_key")
        return 2

    proxy = get_proxy_config() if proxy_request else None
    proxy_error = False
    try:
        get_proxy_config()
    except Exception:
        proxy_error = True
    if proxy_request and proxy is None and not proxy_error:
        proxy_request = False

    captcha_present = False
    captcha_resuelto = False
    clave_incorrecta = False
    clave_actual = clave_erronea

    async with async_playwright() as pw:
        browser, context, page = await launch_with_retry(pw, headless=headless, proxy=proxy)
        try:
            for intento in range(1, max_intentos + 1):
                logs: list[str] = []
                try:
                    result = await login_arca(
                        page,
                        cuit,
                        clave_actual,
                        error_logs=logs,
                        fail_on_captcha=False,
                        solve_arca_captcha=True,
                    )
                except Exception as exc:
                    print(f"FORCE_CAPTCHA intento={intento} exception={type(exc).__name__}", flush=True)
                    continue

                detectado = result.captcha_detected or any("Captcha detectado" in x for x in logs)
                resuelto = any("Captcha resuelto" in x for x in logs)
                incorrecta = any("Clave Incorrecta" in x for x in logs)
                reintentos = [x for x in logs if "reintento" in x.lower()]

                print(
                    f"FORCE_CAPTCHA intento={intento} success={result.success} "
                    f"captcha_detectado={detectado} captcha_resuelto={resuelto} "
                    f"reintentos={len(reintentos)} logs={_sanitize_logs(logs)}",
                    flush=True,
                )
                await evidencias.capturar(page, f"{intento:02d}_{'captcha' if detectado else 'login'}")

                if result.success:
                    print("FORCE_CAPTCHA login_ok=true", flush=True)
                    return 0
                if detectado:
                    captcha_present = True
                if resuelto:
                    captcha_resuelto = True
                if incorrecta:
                    clave_incorrecta = True

                captcha_ok = captcha_present and captcha_resuelto
                if captcha_ok and not clave_correcta and clave_incorrecta:
                    print("FORCE_CAPTCHA captcha_ok=true (clave incorrecta esperada)", flush=True)
                    return 0

                if captcha_ok and clave_correcta and clave_actual != clave_correcta:
                    print(
                        "FORCE_CAPTCHA captcha_ok=true verificando login con clave correcta...",
                        flush=True,
                    )
                    clave_actual = clave_correcta

            print(
                f"FORCE_CAPTCHA captcha_ok={captcha_present and captcha_resuelto} "
                f"captcha_present={captcha_present} captcha_resuelto={captcha_resuelto} "
                f"clave_correcta_ok=false",
                flush=True,
            )
            return 1
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
