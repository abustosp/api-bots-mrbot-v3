"""Custodia recuperable de API keys cifradas para almacenamiento en PostgreSQL.

La API key completa solo se guarda como sobre híbrido: RSA-OAEP-SHA256 cifra
una clave Fernet aleatoria y Fernet cifra el valor. La clave pública se deriva
de ``RSA_PRIVATE_KEY`` de la central. El texto claro solo se devuelve al
operador autorizado en el endpoint de revelado y nunca se incluye en listados.
"""

from __future__ import annotations

import base64
import json

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from central_api.security.rsa_credentials import (
    load_private_key,
    public_key_fingerprint,
)

API_KEY_CIPHER_ALGORITHM = "RSA-OAEP-SHA256+Fernet"
API_KEY_CIPHER_VERSION = 1


class ApiKeyVaultError(ValueError):
    """El sobre cifrado de API key no pudo crearse o abrirse."""


class ApiKeyVaultNotConfigured(ApiKeyVaultError):
    """La clave privada de la central no está configurada."""


def _oaep() -> padding.OAEP:
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def _configured_private_key():
    from central_api.settings import get_settings

    pem = get_settings().rsa_private_key.strip()
    if not pem:
        raise ApiKeyVaultNotConfigured("RSA privada de central no configurada")
    try:
        return load_private_key(pem)
    except ValueError as exc:
        raise ApiKeyVaultError("RSA privada de central inválida") from exc


def encrypt_api_key(api_key: str) -> str:
    """Devuelve un sobre JSON cifrado, nunca una API key en texto claro."""
    if not isinstance(api_key, str) or not api_key:
        raise ApiKeyVaultError("API key vacía")
    private = _configured_private_key()
    public = private.public_key()
    public_pem = public.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")
    data_key = Fernet.generate_key()
    try:
        encrypted_data_key = public.encrypt(data_key, _oaep())
        encrypted_value = Fernet(data_key).encrypt(api_key.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - no incluir material sensible en errores
        raise ApiKeyVaultError("no se pudo cifrar la API key") from exc
    envelope = {
        "version": API_KEY_CIPHER_VERSION,
        "algorithm": API_KEY_CIPHER_ALGORITHM,
        "key_id": public_key_fingerprint(public_pem),
        "encrypted_key": base64.b64encode(encrypted_data_key).decode("ascii"),
        "ciphertext": base64.b64encode(encrypted_value).decode("ascii"),
    }
    return json.dumps(envelope, separators=(",", ":"), sort_keys=True)


def decrypt_api_key(envelope_json: str) -> str:
    """Abre un sobre previamente cifrado por la clave pública configurada."""
    try:
        envelope = json.loads(envelope_json)
        if (
            not isinstance(envelope, dict)
            or envelope.get("version") != API_KEY_CIPHER_VERSION
            or envelope.get("algorithm") != API_KEY_CIPHER_ALGORITHM
            or not isinstance(envelope.get("key_id"), str)
        ):
            raise ValueError("sobre no reconocido")
        private = _configured_private_key()
        public_pem = private.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("ascii")
        if envelope["key_id"] != public_key_fingerprint(public_pem):
            raise ApiKeyVaultError("el sobre usa otra clave pública central")
        encrypted_key = base64.b64decode(envelope["encrypted_key"], validate=True)
        ciphertext = base64.b64decode(envelope["ciphertext"], validate=True)
        data_key = private.decrypt(encrypted_key, _oaep())
        value = Fernet(data_key).decrypt(ciphertext).decode("utf-8")
        if not value:
            raise ValueError("API key vacía")
        return value
    except ApiKeyVaultNotConfigured:
        raise
    except ApiKeyVaultError:
        raise
    except (KeyError, TypeError, ValueError, InvalidToken, UnicodeDecodeError) as exc:
        raise ApiKeyVaultError("sobre de API key inválido o ilegible") from exc
    except Exception as exc:  # noqa: BLE001 - no exponer detalles criptográficos
        raise ApiKeyVaultError("no se pudo abrir el sobre de API key") from exc


__all__ = [
    "API_KEY_CIPHER_ALGORITHM",
    "ApiKeyVaultError",
    "ApiKeyVaultNotConfigured",
    "encrypt_api_key",
    "decrypt_api_key",
]
