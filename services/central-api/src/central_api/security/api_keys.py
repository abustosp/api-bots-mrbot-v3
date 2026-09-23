"""Primitivas de autenticación de clientes (plan 02 §4.2-§4.4).

Formato V3: ``mbk_<key_id>_<secreto>``. El ``key_id`` es selector público;
el secreto (CSPRN, >= 256 bits) nunca se persiste en claro: se guarda
``HMAC-SHA-256(server_secret, secreto)`` y se compara con
``hmac.compare_digest``, incluso contra un verificador ficticio cuando la
fila no existe (resistencia a timing/oráculo).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from central_api.security import sign_secret

KEY_PREFIX = "mbk"
DUMMY_VERIFIER_HEX = "0" * 64


def parse_api_key(raw: str | None) -> tuple[str, str] | None:
    """Separa ``mbk_<key_id>_<secreto>`` en ``(key_id, secreto)``.

    Devuelve ``None`` ante cualquier formato inválido (mismo trato público
    que una key desconocida: 401 genérico, sin oráculo).
    """
    if not raw:
        return None
    parts = raw.split("_")
    if len(parts) != 3 or parts[0] != KEY_PREFIX:
        return None
    key_id, secret = parts[1], parts[2]
    if not key_id or not secret:
        return None
    return key_id, secret


def verify_presented_secret(
    server_secret: str,
    presented_secret: str,
    stored_verifier_hex: str | None,
) -> bool:
    """Compara en tiempo constante; usa verificador ficticio si no hay fila."""
    candidate = stored_verifier_hex or DUMMY_VERIFIER_HEX
    if not server_secret:
        return False
    expected = sign_secret(server_secret, presented_secret)
    return hmac.compare_digest(expected, candidate)


def new_key_id() -> str:
    """Genera un ``key_id`` público aleatorio (selector no secreto)."""
    return secrets.token_hex(8)


def new_secret() -> str:
    """Genera un secreto de 256 bits codificado en hexadecimal."""
    return secrets.token_hex(32)


def fingerprint_secret(server_secret: str, presented_secret: str) -> str:
    """Verificador almacenable para un secreto recién emitido (una sola vez)."""
    return hmac.new(
        server_secret.encode("utf-8"),
        presented_secret.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


__all__ = [
    "KEY_PREFIX",
    "DUMMY_VERIFIER_HEX",
    "parse_api_key",
    "verify_presented_secret",
    "new_key_id",
    "new_secret",
    "fingerprint_secret",
]
