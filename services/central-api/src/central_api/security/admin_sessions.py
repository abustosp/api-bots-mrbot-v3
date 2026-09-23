"""Sesiones del panel /admin (plan 05 §§3.2 y 10.3, fase token/sesión).

La cookie solo porta un identificador opaco de alta entropía; en PostgreSQL
(``admin_sessions``) vive su hash SHA-256, nunca el token en claro. PK UUIDv7
generada en la aplicación: sin IDs correlativos (I-1/I-2). Este módulo es
stdlib-only para poder probarse aislado.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

#: Nombre de la cookie de sesión del panel (valor opaco, nunca JWT legible).
SESSION_COOKIE_NAME = "mrbot_admin_session"

#: Duración de la sesión (8 h, paridad con la cookie V2).
SESSION_TTL_SECONDS = 8 * 3600

#: Límite de resumen para IP inicial y user agent (metadato, no secreto).
IP_RESUMEN_MAX = 64
UA_RESUMEN_MAX = 256


def utcnow() -> datetime:
    """Reloj UTC con zona para emisiones y expiraciones."""
    return datetime.now(timezone.utc)


def mint_session_token() -> str:
    """Genera un identificador opaco de sesión (256 bits de entropía)."""
    return secrets.token_hex(32)


def hash_session_token(token: str) -> str:
    """Hash SHA-256 hex del token; es lo único que se persiste."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def expira_en(base: datetime | None = None) -> datetime:
    """Expiración de una sesión emitida en ``base`` (por defecto ahora)."""
    return (base or utcnow()) + timedelta(seconds=SESSION_TTL_SECONDS)


def verificar_token(presentado: str, esperado_hash: str) -> bool:
    """Compara el token presentado con el hash guardado en tiempo constante."""
    if not presentado or not esperado_hash:
        return False
    return hmac.compare_digest(hash_session_token(presentado), esperado_hash)


def resumir(texto: str | None, tope: int) -> str | None:
    """Recorta un metadato operativo (IP, user agent) a su tope."""
    if texto is None:
        return None
    recorte = texto.strip()[:tope]
    return recorte or None


def parametros_cookie(secure: bool = True) -> dict:
    """Parámetros de la cookie de sesión (plan 05 §3.2 y criterio 5).

    ``HttpOnly``, ``Secure`` (sin excepción en producción), ``SameSite=Lax``
    y ``Path=/admin``. Sin ``secure`` solo en desarrollo local.
    """
    return {
        "key": SESSION_COOKIE_NAME,
        "httponly": True,
        "secure": secure,
        "samesite": "lax",
        "path": "/admin",
        "max_age": SESSION_TTL_SECONDS,
    }


__all__ = [
    "SESSION_COOKIE_NAME",
    "SESSION_TTL_SECONDS",
    "IP_RESUMEN_MAX",
    "UA_RESUMEN_MAX",
    "utcnow",
    "mint_session_token",
    "hash_session_token",
    "expira_en",
    "verificar_token",
    "resumir",
    "parametros_cookie",
]
