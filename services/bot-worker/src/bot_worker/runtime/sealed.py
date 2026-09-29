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
import math
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

# Debe coincidir con central_api.security.sealed: límite del JSON en claro.
# Se valida además el tamaño del blob Fernet antes de descifrar para acotar memoria.
MAX_SEALED_SECTION_BYTES = 4_194_304
# Fernet agrega versión, timestamp, IV, padding y HMAC: el sobre cifrado mide a
# lo sumo el texto plano más 57 bytes (con un bloque extra por el padding) y la
# representación base64 lo infla un tercio. El cálculo anterior usaba la fórmula
# del cifrado RSA, que daba un tope de ~1 MB y rechazaba la tabla APOC de 1,7 MB.
_FERNET_MAX_BYTES = MAX_SEALED_SECTION_BYTES + 57 + 16
_FERNET_MAX_B64_BYTES = 4 * math.ceil(_FERNET_MAX_BYTES / 3)

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
        enc_key = base64.b64decode(sealed["enc_key_b64"], validate=True)
        blob_b64 = sealed["blob_b64"]
        if not isinstance(blob_b64, str) or len(blob_b64) > _FERNET_MAX_B64_BYTES:
            raise SealedEnvelopeError("sección sensible excede 4 MiB")
        blob = base64.b64decode(blob_b64, validate=True)
        if len(blob) > _FERNET_MAX_BYTES:
            raise SealedEnvelopeError("sección sensible excede 4 MiB")
    except SealedEnvelopeError:
        raise
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
        if len(raw) > MAX_SEALED_SECTION_BYTES:
            raise SealedEnvelopeError("sección sensible excede 4 MiB")
    except SealedEnvelopeError:
        raise
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
    "MAX_SEALED_SECTION_BYTES",
    "generate_sealed_keypair",
    "decrypt_sealed_section",
]
