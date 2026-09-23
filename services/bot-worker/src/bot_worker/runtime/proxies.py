"""Configuración de proxy provisionada por la central (sin entorno).

El perfil llega dentro del sobre sellado de cada asignación; sin perfil no
hay proxy. Ningún secreto de proxy vive en el worker en reposo.
"""

from __future__ import annotations

from typing import Any

from bot_worker.runtime.context import ProxyConfig


def build_proxy_config(profile: dict[str, Any] | None) -> ProxyConfig | None:
    """Construye el proxy desde el perfil sellado. ``None`` sin perfil."""
    if not isinstance(profile, dict) or not profile.get("host"):
        return None
    return ProxyConfig(
        mode=str(profile.get("mode") or "standard"),
        host=profile.get("host"),
        username=profile.get("username"),
        password=profile.get("password"),
        country=str(profile.get("country") or "ar"),
    )


__all__ = ["build_proxy_config"]
