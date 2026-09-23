"""Credenciales fiscales entrantes: custodia RSA solo en central (SEC-3).

La clave privada RSA vive exclusivamente en este proceso (nunca en el
worker, W-1/SEC-3). Los clientes cifran con la pública (RSA-OAEP-SHA256);
la central descifra, usa el material solo en memoria y lo re-sella por
asignación con la pública efímera del worker (ver ``sealed.py``).
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from central_api.security.sealed import _oaep


def load_private_key(pem: str):  # pragma: no cover - util fina
    """Carga una clave privada RSA desde PEM (falla cerrado si es inválida)."""
    try:
        key = serialization.load_pem_private_key(pem.encode("utf-8"), password=None)
    except (ValueError, TypeError) as exc:
        raise ValueError("clave privada RSA inválida") from exc
    return key


def decrypt_client_secret(private_pem: str, blob_b64: str) -> bytes:
    """Descifra un secreto de cliente (``clave_encriptada`` Base64)."""
    import base64

    key = load_private_key(private_pem)
    try:
        blob = base64.b64decode(blob_b64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("clave_encriptada no es Base64 válida") from exc
    try:
        return key.decrypt(blob, _oaep())  # type: ignore[union-attr]
    except Exception as exc:
        raise ValueError("no se pudo descifrar la credencial") from exc


def public_key_fingerprint(public_pem: str) -> str:
    """Huella SHA-256 hex de una pública PEM (para ``RSA_KEY_ID`` derivado)."""
    import hashlib

    key = serialization.load_pem_public_key(public_pem.encode("utf-8"))
    der = key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


__all__ = ["load_private_key", "decrypt_client_secret", "public_key_fingerprint"]
