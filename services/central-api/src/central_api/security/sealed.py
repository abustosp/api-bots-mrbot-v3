"""Sobre sellado central -> worker (híbrido RSA-OAEP-SHA256 + Fernet).

Mismas primitivas que la V2 (``rsa_credentials`` + ``job_secrets``), en
sentido inverso: el worker genera un par RSA efímero al arrancar y publica la
clave pública en su registro; la central cifra con ella la sección sensible
de cada asignación (credenciales fiscales, URLs prefirmadas de subida).

Propiedades:
- Sin la clave privada del worker (que nunca sale de su memoria) el sobre es
  opaco: protege contra escucha en la red aun sin TLS y contra logs que
  capturen el cuerpo del request.
- La clave Fernet es por asignación: comprometer una no abre otras.
- Este módulo es stdlib + ``cryptography``: no importa FastAPI ni el store,
  para poder probarse aislado.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

SEALED_ALG = "RSA-OAEP-SHA256+Fernet"
# The APOC AFIP cache is about 1.7 MiB before sealing. Keep a finite 4 MiB
# plaintext ceiling for it plus metadata; Fernet/base64 make the wire envelope
# larger, so this is deliberately a strict input bound, not an unbounded mode.
MAX_SEALED_SECTION_BYTES = 4_194_304


class SealedSectionError(ValueError):
    """La sección sellada no pudo cifrarse (clave inválida o dato excedido)."""


def _oaep() -> padding.OAEP:
    return padding.OAEP(
        mgf=padding.MGF1(algorithm=hashes.SHA256()),
        algorithm=hashes.SHA256(),
        label=None,
    )


def encrypt_sealed_section(pubkey_pem: str, section: dict[str, Any]) -> dict[str, str]:
    """Cifra ``section`` para el dueño de ``pubkey_pem`` (PEM pública RSA).

    Devuelve el wire ``{"alg", "enc_key_b64", "blob_b64"}`` que viaja en el
    sobre. Falla cerrado ante clave inválida o sección que excede el tope.
    """
    raw = json.dumps(section, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if len(raw) > MAX_SEALED_SECTION_BYTES:
        raise SealedSectionError("sección sensible excede 4 MiB")
    try:
        pubkey = serialization.load_pem_public_key(pubkey_pem.encode("utf-8"))
    except (ValueError, TypeError) as exc:
        raise SealedSectionError("clave pública del worker inválida") from exc
    data_key = Fernet.generate_key()
    try:
        enc_key = pubkey.encrypt(data_key, _oaep())  # type: ignore[union-attr]
    except Exception as exc:  # clave no-RSA o cifrado rechazado: no seguir en claro
        raise SealedSectionError("cifrado RSA rechazado") from exc
    blob = Fernet(data_key).encrypt(raw)
    return {
        "alg": SEALED_ALG,
        "enc_key_b64": base64.b64encode(enc_key).decode("ascii"),
        "blob_b64": base64.b64encode(blob).decode("ascii"),
    }


def is_sealed_section(obj: Any) -> bool:
    return (
        isinstance(obj, dict)
        and obj.get("alg") == SEALED_ALG
        and isinstance(obj.get("enc_key_b64"), str)
        and isinstance(obj.get("blob_b64"), str)
    )


__all__ = [
    "SEALED_ALG",
    "MAX_SEALED_SECTION_BYTES",
    "SealedSectionError",
    "encrypt_sealed_section",
    "is_sealed_section",
    "InvalidToken",
]
