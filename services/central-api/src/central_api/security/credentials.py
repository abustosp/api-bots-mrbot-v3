"""Normalización y cifrado de credenciales fiscales en la central.

Los clientes V2 pueden enviar ``clave``, ``clave_representante`` o
``contrasena`` en claro, o ``clave_encriptada`` cifrada con la clave pública
RSA de la central. La central entrega al worker únicamente el valor en memoria
dentro del sobre RSA+Fernet y persiste solo el ciphertext RSA.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any

from central_api.security.rsa_credentials import (
    CredentialDecryptionError,
    decrypt_configured_credential,
    encrypt_configured_credential,
)

CREDENTIAL_FIELDS = ("clave", "clave_representante", "contrasena")
CREDENTIAL_TRANSPORT_FIELDS = (*CREDENTIAL_FIELDS, "clave_encriptada")
_CREDENTIAL_CONTEXT_FIELDS = (
    "cuit_representante",
    "cuit_inicio_sesion",
    "cuit_login",
    "cuit_representado",
    "representado_cuit",
    "usuario",
)


def credential_metadata(
    payload: Mapping[str, Any], credentials: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Devuelve contexto no secreto para el registro administrativo.

    El valor de la credencial nunca se incluye. Solo se conserva qué campo
    llegó, por qué vía y qué identidad fiscal se estaba consultando, para que
    el panel pueda explicar la ejecución sin leer el ciphertext.
    """
    supplied = {**dict(payload), **dict(credentials or {})}
    fields = [
        name
        for name in CREDENTIAL_TRANSPORT_FIELDS
        if supplied.get(name) not in (None, "")
    ]
    context = {
        name: supplied[name]
        for name in _CREDENTIAL_CONTEXT_FIELDS
        if supplied.get(name) not in (None, "")
        and isinstance(supplied[name], (str, int))
    }
    return {
        "fields": fields,
        "context": context,
    }


def normalize_v2_payload(
    bot: str, operation: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Traduce nombres de campos V2 al contrato que valida el worker V3.

    La traducción ocurre antes del fingerprint y de la persistencia. Los
    nombres V2 siguen siendo los que ve el cliente, mientras que el worker
    recibe un único vocabulario canónico.
    """
    normalized = dict(payload)

    aliases = {
        "cuit_representado": "representado_cuit",
        "movimientos": "incluir_movimientos",
        "descarga_emitidos": "emitidos",
        "descarga_recibidos": "recibidos",
        "descarga_csv_ventas": "descarga_ventas",
        "descarga_csv_compras": "descarga_compras",
        "carga_json": "incluir_json",
    }
    for old_name, new_name in aliases.items():
        if old_name in normalized and new_name not in normalized:
            normalized[new_name] = normalized[old_name]
        normalized.pop(old_name, None)

    if "pdf" in normalized:
        pdf = normalized.pop("pdf")
        for target in ("incluir_pdf", "subir_pdf"):
            if target not in normalized:
                normalized[target] = pdf
                break

    if "desde" in normalized:
        normalized.setdefault("fecha_desde", normalized["desde"])
        normalized.pop("desde", None)
    if "hasta" in normalized:
        normalized.setdefault("fecha_hasta", normalized["hasta"])
        normalized.pop("hasta", None)

    upload_flag = None
    for source in ("carga_minio", "minio_upload"):
        if source in normalized:
            upload_flag = normalized.pop(source)
            break
    if upload_flag is not None:
        for target in (
            "subir",
            "subir_csv",
            "subir_archivos",
            "subir_archivo",
            "subir_pdf",
            "subir_excel",
        ):
            if target not in normalized:
                normalized[target] = upload_flag
                break

    # El proxy de V2 era una instrucción de infraestructura. La central lo
    # resuelve desde su configuración y lo entrega al worker en el sobre
    # sellado, por lo que nunca debe terminar en el payload persistido.
    normalized.pop("proxy_request", None)
    return normalized


class CredentialInputError(ValueError):
    """La credencial recibida es inválida o no puede custodiarse."""


def _first_nonempty(values: Mapping[str, Any], names: tuple[str, ...]) -> str | None:
    selected: str | None = None
    for name in names:
        value = values.get(name)
        if value in (None, ""):
            continue
        if not isinstance(value, str):
            raise CredentialInputError(f"{name} debe ser texto")
        if selected is None:
            selected = value
        elif value != selected:
            raise CredentialInputError("Los campos de credencial enviados no coinciden")
    return selected


def normalize_payload_and_credentials(
    payload: Mapping[str, Any], credentials: Mapping[str, Any] | None = None,
    *, require_encryption: bool = True,
) -> tuple[dict[str, Any], dict[str, Any], str | None, str | None]:
    """Separa credenciales del payload y las prepara para memoria y DB.

    Devuelve ``payload_sin_secretos``, ``credentials_para_worker``,
    ``ciphertext_rsa`` y un fingerprint no reversible para idempotencia.
    """
    clean_payload = dict(payload)
    supplied = dict(credentials or {})
    for name in CREDENTIAL_TRANSPORT_FIELDS:
        if name in clean_payload and name not in supplied:
            supplied[name] = clean_payload[name]
        clean_payload.pop(name, None)

    representative = _first_nonempty(
        {**clean_payload, **supplied},
        ("cuit_representante", "cuit_inicio_sesion"),
    )
    for name in ("cuit_representante", "cuit_inicio_sesion"):
        supplied.pop(name, None)
        clean_payload.pop(name, None)

    plaintext = _first_nonempty(supplied, CREDENTIAL_FIELDS)
    encrypted = supplied.get("clave_encriptada")
    if encrypted not in (None, "") and not isinstance(encrypted, str):
        raise CredentialInputError("clave_encriptada debe ser texto Base64")
    if encrypted:
        try:
            decrypted = decrypt_configured_credential(encrypted)
        except (CredentialDecryptionError, RuntimeError, ValueError) as exc:
            raise CredentialInputError("no se pudo desencriptar clave_encriptada") from exc
        if plaintext is not None and plaintext != decrypted:
            raise CredentialInputError("clave y clave_encriptada no coinciden")
        plaintext = decrypted

    if plaintext is None:
        if encrypted:
            raise CredentialInputError("clave_encriptada vacía")
        return clean_payload, {}, None, None

    try:
        ciphertext = encrypt_configured_credential(plaintext)
    except (RuntimeError, ValueError, TypeError) as exc:
        if require_encryption:
            raise CredentialInputError(
                "la central no tiene una clave RSA válida para custodiar la credencial"
            ) from exc
        ciphertext = None

    worker_credentials: dict[str, Any] = {"clave": plaintext}
    if representative:
        worker_credentials["cuit_representante"] = representative
    fingerprint = hashlib.sha256(plaintext.encode("utf-8")).hexdigest()
    return clean_payload, worker_credentials, ciphertext, fingerprint


__all__ = [
    "CREDENTIAL_FIELDS",
    "CREDENTIAL_TRANSPORT_FIELDS",
    "CredentialInputError",
    "credential_metadata",
    "normalize_v2_payload",
    "normalize_payload_and_credentials",
]
