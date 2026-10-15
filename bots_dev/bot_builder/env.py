"""Carga de .env y resolucion de variables con prefijo por bot."""
from __future__ import annotations

import os
from typing import Any, Optional


def load_env() -> None:
    """Carga `.env` si esta disponible. Silencioso si no existe."""
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass


def get_env(key: str, default: Optional[str] = None, *, prefix: str = "") -> str:
    """Lee una variable con prefijo por bot y fallback al nombre sin prefijo.

    Ej: get_env("CUIT_LOGIN", prefix="MIS_FACILIDADES") busca
    MIS_FACILIDADES_CUIT_LOGIN y luego CUIT_LOGIN.
    """
    if prefix:
        v = os.getenv(f"{prefix}_{key}")
        if v not in (None, ""):
            return v
    v = os.getenv(key, default if default is not None else "")
    return v if v is not None else ""


def env_bool(key: str, default: bool = False, *, prefix: str = "") -> bool:
    raw = get_env(key, prefix=prefix)
    if raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "si", "y", "on")
