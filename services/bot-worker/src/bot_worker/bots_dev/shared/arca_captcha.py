"""Resolución de CAPTCHA de imagen del login de ARCA via CapMonster (para bots_dev).

Versión autocontenida de `app/utils/arca_captcha.py` para ejecutarse dentro de
`bots_dev/` (con `PYTHONPATH=bots_dev`) sin depender de `app.utils`. Debe mantener
paridad funcional con la variante de la API.

Seguridad: nunca persiste la imagen ni el HTML del CAPTCHA ni credenciales.
Sólo emite un evento de logger (nombre + flag) y devuelve el texto resuelto en
memoria para rellenar el input.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import urllib.error
import urllib.request
from typing import List, Optional

from playwright.async_api import Page

LOGGER = logging.getLogger(__name__)

ARCA_CAPTCHA_URL = "https://auth.afip.gob.ar/contribuyente_/loginCaptchaAfip.xhtml"

CAPTCHA_IMG_SELECTORS = [
    "img[src*='base64']",
    "img[alt*='captcha' i], img[alt='Captcha']",
    "#captcha img, div#captcha img",
]
CAPTCHA_INPUT_SELECTORS = [
    "input#F1\\:captchaSolutionInput",
    "input[name='F1:captchaSolutionInput']",
    "input[placeholder*='captcha' i], input[placeholder*='solución' i], input[placeholder*='solucion' i]",
]

_CAPMONSTER_CREATE_TASK_URL = "https://api.capmonster.cloud/createTask"
_CAPMONSTER_GET_TASK_RESULT_URL = "https://api.capmonster.cloud/getTaskResult"


def _arca_capmonster_api_key() -> str:
    """Prioriza ARCA_CAPMONSTER_API_KEY; reutiliza la key existente del proyecto."""
    for candidate in ("ARCA_CAPMONSTER_API_KEY", "SRT_CAPMONSTER_API_KEY", "RETPER_CAPMONSTER_API_KEY"):
        value = (os.getenv(candidate) or "").strip()
        if value:
            return value
    return ""


def _env_flag(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "y", "on")


def _optional_int(raw: Optional[str]) -> Optional[int]:
    try:
        return int(raw.strip()) if raw and raw.strip().lstrip("-").isdigit() else None
    except Exception:
        return None


def get_arca_capmonster_config() -> dict:
    return {
        "api_key": _arca_capmonster_api_key(),
        "timeout_s": _positive_int(os.getenv("ARCA_CAPMONSTER_TIMEOUT_SECONDS"), 60),
        "poll_s": _positive_int(os.getenv("ARCA_CAPMONSTER_POLL_SECONDS"), 3),
        "max_attempts": _positive_int(os.getenv("ARCA_CAPMONSTER_MAX_ATTEMPTS"), 5),
        "module": (os.getenv("ARCA_CAPMONSTER_MODULE") or "").strip(),
        "case": _env_flag("ARCA_CAPMONSTER_CASE", True),
        "threshold": _optional_int(os.getenv("ARCA_CAPMONSTER_THRESHOLD")),
    }


def _positive_int(raw: Optional[str], default: int) -> int:
    try:
        return int(raw.strip()) if raw and raw.strip().isdigit() else default
    except Exception:
        return default


def _post_json(url: str, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(body)
        return parsed if isinstance(parsed, dict) else {}
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError):
        return {}


async def solve_image_captcha(
    api_key: str,
    image_base64: str,
    *,
    timeout_s: int = 60,
    poll_s: int = 3,
    module: str = "",
    case: bool = True,
    threshold: Optional[int] = None,
) -> Optional[str]:
    if not api_key or not image_base64:
        return None
    task: dict = {"type": "ImageToTextTask", "body": image_base64, "Case": bool(case)}
    if module:
        task["CapMonsterModule"] = module
    if threshold is not None:
        task["recognizingThreshold"] = int(threshold)
    create_payload = {"clientKey": api_key, "task": task}
    data = await asyncio.to_thread(_post_json, _CAPMONSTER_CREATE_TASK_URL, create_payload)
    if data.get("errorId", 0) != 0 or "taskId" not in data:
        return None
    task_id = data["taskId"]
    result_payload = {"clientKey": api_key, "taskId": task_id}
    elapsed = 0
    while elapsed < timeout_s:
        await asyncio.sleep(poll_s)
        elapsed += poll_s
        result = await asyncio.to_thread(_post_json, _CAPMONSTER_GET_TASK_RESULT_URL, result_payload)
        if result.get("errorId", 0) != 0:
            return None
        if result.get("status") == "ready":
            text = result.get("solution", {}).get("text")
            return text if isinstance(text, str) and text.strip() else None
    return None


async def is_arca_captcha_page(page: Page) -> bool:
    try:
        url = (page.url or "").lower()
    except Exception:
        url = ""
    if ARCA_CAPTCHA_URL.split("//")[-1] in url or "logincaptchaafip" in url:
        return True
    try:
        input_count = await page.locator(",".join(CAPTCHA_INPUT_SELECTORS)).count()
    except Exception:
        input_count = 0
    return input_count > 0


def _extract_base64(src: str) -> Optional[str]:
    if not src:
        return None
    s = src.strip()
    if "," in s:
        s = s.split(",", 1)[1]
    s = s.strip()
    if not s:
        return None
    candidate = s.translate(str.maketrans("", "", " \n\r\t="))
    if not candidate or not all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/_-" for c in candidate):
        return None
    try:
        base64.b64decode(s + "==", validate=False)
    except Exception:
        return None
    return s


async def detect_arca_captcha_image_base64(page: Page) -> Optional[str]:
    for selector in CAPTCHA_IMG_SELECTORS:
        try:
            locator = page.locator(selector)
            count = await locator.count()
        except Exception:
            continue
        if count <= 0:
            continue
        for idx in range(min(count, 2)):
            try:
                src = await locator.nth(idx).get_attribute("src")
            except Exception:
                continue
            b64 = _extract_base64(src)
            if b64:
                return b64
    return None


async def fill_arca_captcha(page: Page, solution: str) -> bool:
    cleaned = (solution or "").strip()
    if not cleaned:
        return False
    for selector in CAPTCHA_INPUT_SELECTORS:
        try:
            locator = page.locator(selector)
            if await locator.count() == 0:
                continue
            target = locator.first
            try:
                await target.fill(cleaned, timeout=3_000)
            except Exception:
                try:
                    await target.evaluate("(el) => { el.style.display='block'; el.style.visibility='visible'; }")
                    await target.fill(cleaned, timeout=3_000)
                except Exception:
                    continue
            return True
        except Exception:
            continue
    return False


async def try_solve_arca_captcha(
    page: Page,
    *,
    error_logs: Optional[List[str]] = None,
    max_attempts: Optional[int] = None,
    announce: bool = True,
) -> bool:
    logs = error_logs if error_logs is not None else []
    try:
        if not await is_arca_captcha_page(page):
            return True
    except Exception as exc:
        LOGGER.warning("arca_captcha_check_skipped: %s", exc)
        return True

    cfg = get_arca_capmonster_config()
    LOGGER.info("arca_captcha_required url=%s", "logincaptchaafip")
    if announce:
        logs.append("Captcha detectado")

    if not cfg["api_key"]:
        LOGGER.info("arca_captcha_no_api_key")
        logs.append("ARCA_CAPMONSTER_API_KEY no definida, no se pudo resolver captcha")
        return False

    attempts = max_attempts if max_attempts and max_attempts > 0 else cfg["max_attempts"]
    for attempt in range(1, attempts + 1):
        image_b64 = await detect_arca_captcha_image_base64(page)
        if not image_b64:
            LOGGER.info("arca_captcha_image_not_found attempt=%d", attempt)
            continue
        try:
            solution = await solve_image_captcha(
                cfg["api_key"],
                image_b64,
                timeout_s=cfg["timeout_s"],
                poll_s=cfg["poll_s"],
                module=cfg.get("module", ""),
                case=cfg.get("case", True),
                threshold=cfg.get("threshold"),
            )
        except Exception:
            solution = None
        if not solution:
            LOGGER.info("arca_captcha_failed attempt=%d", attempt)
            continue
        filled = await fill_arca_captcha(page, solution)
        if not filled:
            LOGGER.warning("arca_captcha_fill_failed attempt=%d", attempt)
            return False
        LOGGER.info("arca_captcha_solved attempt=%d", attempt)
        logs.append("Captcha resuelto")
        return True

    LOGGER.info("arca_captcha_failed exhausted_attempts=%d", attempts)
    if announce:
        logs.append("No se pudo resolver el captcha de ARCA con CapMonster")
    return False
