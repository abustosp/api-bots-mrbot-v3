"""Resolvedor de CAPTCHA (stub F2).

Usa las claves que la central provisiona por asignación dentro del sobre
sellado. Ninguna clave vive en el worker en reposo ni llega al plugin o
a los logs.
"""

from __future__ import annotations

from typing import Any


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
    ) -> None:
        self.enabled = enabled
        self._api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self.max_attempts = max_attempts
        self.provider = provider

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
        enabled = bool(profile.get("enabled", False)) and bool(key)
        return cls(
            enabled=enabled,
            api_key=key,
            timeout_seconds=int(profile.get("timeout_seconds", 60)),
            provider=provider,
        )

    async def solve_image(self, image_bytes: bytes) -> str:
        if not self.enabled:
            raise CaptchaUnsolvableError("solucionador deshabilitado")
        if not self._api_key:
            raise CaptchaUnsolvableError("sin credencial de proveedor")
        # F3: POST createTask/getTaskResult contra CapMonster con timeout.
        raise CaptchaUnsolvableError("stub F2: sin implementar")
