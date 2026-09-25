"""Resolvedor de CAPTCHA (stub F2).

Usa las claves que la central provisiona por asignación dentro del sobre
sellado. Ninguna clave vive en el worker en reposo ni llega al plugin o
a los logs.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any

import httpx

_CREATE_TASK_URL = "https://api.capmonster.cloud/createTask"
_GET_RESULT_URL = "https://api.capmonster.cloud/getTaskResult"


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(parsed, minimum), maximum)


def _profile_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


class CaptchaUnsolvableError(Exception):
    category = "CAPTCHA_UNSOLVABLE"


class CaptchaSolver:
    """Proveedor CapMonster. F3 implementa createTask/getTaskResult reales."""

    def __init__(
        self,
        enabled: bool = True,
        api_key: str | None = None,
        timeout_seconds: int = 60,
        poll_seconds: int = 3,
        max_attempts: int = 5,
        provider: str = "arca",
        module: str = "",
        case_sensitive: bool = True,
        recognizing_threshold: int | None = None,
    ) -> None:
        self.enabled = enabled
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.max_attempts = max_attempts
        self.provider = provider
        self.module = module
        self.case_sensitive = case_sensitive
        self.recognizing_threshold = recognizing_threshold

    def __repr__(self) -> str:
        return f"CaptchaSolver(provider={self.provider!r}, api_key=<redacted>)"

    @classmethod
    def from_profile(
        cls, provider: str = "arca", profile: dict[str, Any] | None = None
    ) -> "CaptchaSolver":
        """Construye el resolvedor desde el perfil sellado de la asignación.

        Sin perfil o sin clave, el resolvedor nace deshabilitado: el plugin
        recibe el error ``sin credencial de proveedor`` en vez de leer
        entorno (el worker no tiene variables).
        """
        profile = profile if isinstance(profile, dict) else {}
        raw = profile.get(f"{provider}_key") or profile.get("api_key") or ""
        key = str(raw).strip() or None
        if key in ("sin-configurar", "placeholder"):
            key = None
        enabled = _profile_bool(profile.get("enabled"), False) and bool(key)
        threshold = profile.get("threshold")
        return cls(
            enabled=enabled,
            api_key=key,
            timeout_seconds=_bounded_int(profile.get("timeout_seconds"), 60, 5, 300),
            poll_seconds=_bounded_int(profile.get("poll_seconds"), 3, 1, 10),
            max_attempts=_bounded_int(profile.get("max_attempts"), 5, 1, 5),
            provider=provider,
            module=str(profile.get("module", ""))[:128],
            case_sensitive=_profile_bool(profile.get("case"), True),
            recognizing_threshold=(
                _bounded_int(threshold, 0, 0, 100) if threshold is not None else None
            ),
        )

    async def solve_image(self, image_bytes: bytes) -> str:
        if not self.enabled:
            raise CaptchaUnsolvableError("solucionador deshabilitado")
        if not self._api_key:
            raise CaptchaUnsolvableError("sin credencial de proveedor")
        if not image_bytes:
            raise CaptchaUnsolvableError("imagen CAPTCHA vacía")

        task: dict[str, Any] = {
            "type": "ImageToTextTask",
            "body": base64.b64encode(image_bytes).decode("ascii"),
            "Case": self.case_sensitive,
        }
        if self.module:
            task["CapMonsterModule"] = self.module
        if self.recognizing_threshold is not None:
            task["recognizingThreshold"] = self.recognizing_threshold
        timeout_seconds = _bounded_int(self.timeout_seconds, 60, 5, 300)
        poll_seconds = _bounded_int(self.poll_seconds, 3, 1, 10)
        request_timeout = httpx.Timeout(min(timeout_seconds, 120))
        payload = {"clientKey": self._api_key, "task": task}
        deadline = time.monotonic() + timeout_seconds

        try:
            async with httpx.AsyncClient(timeout=request_timeout) as client:
                response = await client.post(_CREATE_TASK_URL, json=payload)
                response.raise_for_status()
                created = response.json()
                task_id = created.get("taskId")
                if created.get("errorId", 0) != 0 or not task_id:
                    raise CaptchaUnsolvableError("el proveedor rechazó la tarea CAPTCHA")

                result_payload = {"clientKey": self._api_key, "taskId": task_id}
                while time.monotonic() < deadline:
                    await asyncio.sleep(poll_seconds)
                    response = await client.post(
                        _GET_RESULT_URL, json=result_payload
                    )
                    response.raise_for_status()
                    result = response.json()
                    if result.get("errorId", 0) != 0:
                        raise CaptchaUnsolvableError(
                            "el proveedor no pudo resolver el CAPTCHA"
                        )
                    if result.get("status") == "ready":
                        text = result.get("solution", {}).get("text")
                        if isinstance(text, str) and text.strip():
                            return text.strip()
                        raise CaptchaUnsolvableError(
                            "el proveedor devolvió una solución vacía"
                        )
        except CaptchaUnsolvableError:
            raise
        except Exception:
            # No propagar mensajes de HTTPX, que podrían incluir datos del request.
            raise CaptchaUnsolvableError(
                "falló la comunicación con el proveedor CAPTCHA"
            ) from None
        raise CaptchaUnsolvableError("agotado el tiempo del proveedor CAPTCHA")
