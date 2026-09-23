"""Seguridad de borde de central-api (plan 02 §4 y §10, plan 04 §10).

Contrato final en plans/02-central-api/plan.md: las API keys se verifican
con ``HMAC-SHA-256(server_secret, secreto exacto)`` y comparación en tiempo
constante; el sanitizador SEC-1 rige toda frontera pública; la clave RSA
privada solo vive en este proceso (SEC-3); el sobre sellado RSA-OAEP+Fernet
protege lo sensible hacia el worker.
"""

from __future__ import annotations

import hashlib
import hmac


def sign_secret(server_secret: str, presented_secret: str) -> str:
    """Devuelve el verificador hex ``HMAC-SHA-256(secret, presented)``."""
    return hmac.new(
        server_secret.encode("utf-8"),
        presented_secret.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_secret(server_secret: str, presented_secret: str, verifier_hex: str) -> bool:
    """Compara el verificador en tiempo constante."""
    if not server_secret or not verifier_hex:
        return False
    return hmac.compare_digest(sign_secret(server_secret, presented_secret), verifier_hex)


__all__ = ["sign_secret", "verify_secret"]
