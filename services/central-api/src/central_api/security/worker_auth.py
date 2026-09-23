"""Autenticación interna de workers (plan 02 §4.8 y §7).

Contrato: mTLS + token de servicio de corta vida (audiencia
``central-api-internal``, identidad del worker, versión de protocolo,
expiración y nonce), vinculado al ``worker_id`` registrado; un worker nunca
reporta por otro. Este módulo valida pertenencia al inventario y, cuando hay
token configurado, su portador en tiempo constante; nunca acepta API keys de
clientes en rutas internas.

Fase de identidad: el token v1 va ligado al nodo ``ip:port`` (modo memoria);
el token v2 va ligado al ``worker_id`` UUID pleno emitido al registrar en
PostgreSQL (sin IDs correlativos, I-1/I-2). Ambos se aceptan durante la
transición; todo token nuevo se emite en v2 cuando hay UUID.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from uuid import UUID

from mrbot_contracts.version import PROTOCOL_VERSION

INTERNAL_AUDIENCE = "central-api-internal"
SERVICE_TOKEN_TTL_SECONDS = 600

def _firmar(signing_key: str, body: str) -> str:
    """Firma HMAC-SHA256 del cuerpo con la clave de firma interna."""
    return hmac.new(
        (signing_key or "dev-insecure").encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def mint_service_token(signing_key: str, worker_node: str) -> str:
    """Emite un token de servicio v1 ligado al nodo (modo memoria)."""
    nonce = secrets.token_hex(16)
    expiry = int(time.time()) + SERVICE_TOKEN_TTL_SECONDS
    body = f"{INTERNAL_AUDIENCE}.{worker_node}.{expiry}.{nonce}"
    return f"{body}.{_firmar(signing_key, body)}"


def check_service_token(signing_key: str, token: str, worker_node: str) -> bool:
    """Valida un token v1: audiencia, nodo, expiración y firma.

    El nodo ``ip:port`` puede contener puntos (IPv4): se recompone uniendo
    los segmentos intermedios en vez de exigir exactamente 5 partes.
    """
    try:
        parts = token.split(".")
        if len(parts) < 5:
            return False
        audience, sig, nonce, expiry_s = parts[0], parts[-1], parts[-2], parts[-3]
        node = ".".join(parts[1:-3])
    except (AttributeError, IndexError):
        return False
    if audience != INTERNAL_AUDIENCE or node != worker_node:
        return False
    try:
        if int(expiry_s) < int(time.time()):
            return False
    except ValueError:
        return False
    expected = _firmar(signing_key, f"{audience}.{node}.{expiry_s}.{nonce}")
    return hmac.compare_digest(expected, sig)


def mint_worker_token(signing_key: str, worker_id: UUID) -> str:
    """Emite un token de servicio v2 ligado al UUID pleno del worker."""
    nonce = secrets.token_hex(16)
    expiry = int(time.time()) + SERVICE_TOKEN_TTL_SECONDS
    body = f"{INTERNAL_AUDIENCE}.{worker_id.hex}.{expiry}.{nonce}.{PROTOCOL_VERSION}"
    return f"{body}.{_firmar(signing_key, body)}"


def check_worker_token(signing_key: str, token: str, worker_id: UUID) -> bool:
    """Valida un token v2: audiencia, UUID, expiración, protocolo y firma."""
    try:
        audience, who, expiry_s, nonce, proto_s, sig = token.split(".")
    except (ValueError, AttributeError):
        return False
    if audience != INTERNAL_AUDIENCE or who != worker_id.hex:
        return False
    try:
        if int(proto_s) != PROTOCOL_VERSION:
            return False
        if int(expiry_s) < int(time.time()):
            return False
    except ValueError:
        return False
    expected = _firmar(
        signing_key, f"{audience}.{who}.{expiry_s}.{nonce}.{proto_s}"
    )
    return hmac.compare_digest(expected, sig)


def check_any_token(
    signing_key: str, token: str, worker_node: str, worker_id: UUID | None
) -> bool:
    """Acepta el token v1 (nodo) o v2 (UUID) que corresponda al worker."""
    if worker_id is not None and check_worker_token(signing_key, token, worker_id):
        return True
    return check_service_token(signing_key, token, worker_node)


def worker_uuid_from_token(token: str) -> UUID | None:
    """Extrae el UUID de un token v2; ``None`` si es v1 o inválido."""
    try:
        parts = token.split(".")
        if len(parts) != 6:
            return None
        if parts[0] != INTERNAL_AUDIENCE:
            return None
        return UUID(hex=parts[1])
    except (ValueError, AttributeError, TypeError):
        return None


__all__ = [
    "INTERNAL_AUDIENCE",
    "PROTOCOL_VERSION",
    "SERVICE_TOKEN_TTL_SECONDS",
    "mint_service_token",
    "check_service_token",
    "mint_worker_token",
    "check_worker_token",
    "check_any_token",
    "worker_uuid_from_token",
]
