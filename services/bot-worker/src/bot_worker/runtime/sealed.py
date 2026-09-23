"""Sobre sellado del lado worker: par RSA efímero + apertura en memoria.

El par se genera al arrancar y muere con el proceso (rotación por reinicio):
la pública viaja en el registro, la privada nunca sale de esta memoria y
nunca se escribe a disco. Lo descifrado (credenciales fiscales, URLs
prefirmadas) vive solo en memoria y el resultado se sanitiza antes del
callback (ver ``_sanitize`` en ``main``).
"""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa


class SealedEnvelopeError(ValueError):
    """El sobre sellado no pudo abrirse. Sin detalle del contenido."""


def generate_sealed_keypair() -> tuple[bytes, str]:
    """Genera el par efímero. Devuelve ``(privada_pem, publica_pem)``."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = priv.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pub_pem = (
        priv.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    return priv_pem, pub_pem


def decrypt_sealed_section(privkey_pem: bytes, sealed: dict[str, Any]) -> dict[str, Any]:
    """Abre la sección sellada con la privada efímera. Falla cerrado."""
    try:
        enc_key = base64.b64decode(sealed["enc_key_b64"])
        blob = base64.b64decode(sealed["blob_b64"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SealedEnvelopeError("sobre sellado malformado") from exc
    if sealed.get("alg") != "RSA-OAEP-SHA256+Fernet":
        raise SealedEnvelopeError("algoritmo de sellado no soportado")
    try:
        priv = serialization.load_pem_private_key(privkey_pem, password=None)
        data_key = priv.decrypt(  # type: ignore[union-attr]
            enc_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        raw = Fernet(data_key).decrypt(blob)
    except (ValueError, TypeError, InvalidToken) as exc:
        raise SealedEnvelopeError("sobre sellado inválido") from exc
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise SealedEnvelopeError("contenido sellado inválido") from exc
    if not isinstance(obj, dict):
        raise SealedEnvelopeError("contenido sellado inválido")
    return obj


__all__ = [
    "SealedEnvelopeError",
    "generate_sealed_keypair",
    "decrypt_sealed_section",
]
