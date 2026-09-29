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
# Bots cuyo contrato V3 pide periodos ``AAAAMM`` en ``periodo_desde`` y
# ``periodo_hasta`` aunque el cliente histórico V1 mande ``desde``/``hasta``.
# El resto de los bots usa fechas ``dd/mm/aaaa`` en ``fecha_desde``/``fecha_hasta``.
PERIODO_ALIAS_BOTS = frozenset({"retper_iibb_agip", "retper_iibb_misiones"})
_CREDENTIAL_CONTEXT_FIELDS = (
    "cuit_representante",
    "cuit_inicio_sesion",
    "cuit_login",
    "cuit_representado",
    "representado_cuit",
    "usuario",
)
# Nombres históricos de la bandera de subida. ``carga_minio`` y
# ``minio_upload`` los usan varios bots; ``archivo_historico_minio`` es el
# nombre V1 de Aportes en Línea y sin traducirlo el worker rechaza el cuerpo
# documentado del alias con ``ENVELOPE_INVALID`` (``extra=forbid``).
_UPLOAD_FLAG_SOURCES = ("carga_minio", "minio_upload", "archivo_historico_minio")


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
        "cuit_inicio_sesion": "cuit_representante",
        "cuit_login": "cuit_representante",
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

    if bot == "arba" and "cuit" in normalized:
        normalized.setdefault("representado_cuit", normalized["cuit"])
        normalized.pop("cuit", None)
    if bot in {
        "arba", "liquidacion_granos", "rcel", "retper_iibb_misiones",
        "mis_retenciones", "mis_retenciones_iva_simple", "portal_iva",
    }:
        if "denominacion" in normalized:
            normalized.setdefault("representado_nombre", normalized["denominacion"])
            normalized.pop("denominacion", None)

    if bot == "rcel" and "nombre_rcel" in normalized:
        normalized.setdefault("representado_nombre", normalized.pop("nombre_rcel"))

    if bot == "siper":
        for legacy, current in (
            ("detalle_minio", "incluir_detalle"),
            ("categorias_minio", "incluir_categorias"),
        ):
            if legacy in normalized:
                normalized.setdefault(current, normalized.pop(legacy))

    if bot == "sct":
        # V1 pedía salidas por sección y formato con nueve banderas; el worker
        # recibe dos listas. Se traduce solo lo pedido y se descartan las
        # banderas para no romper el esquema con ``extra=forbid``.
        secciones: list[str] = []
        formatos: list[str] = []
        for prefijo, seccion in (
            ("vencimientos", "vencimientos"),
            ("deudas", "deudas"),
            ("ddjj_pendientes", "ddjj_pendientes"),
        ):
            for sufijo, formato in (
                ("excel", "xlsx"),
                ("xlsx", "xlsx"),
                ("csv", "csv"),
                ("pdf", "pdf"),
            ):
                bandera = f"{prefijo}_{sufijo}_minio"
                if bandera not in normalized:
                    continue
                if bool(normalized.pop(bandera)):
                    secciones.append(seccion)
                    formatos.append(formato)
        if secciones:
            normalized.setdefault("secciones", sorted(set(secciones)))
            normalized.setdefault("formatos", sorted(set(formatos)))

    if "pdf" in normalized:
        pdf = normalized.pop("pdf")
        for target in (("pdf",) if bot == "compensaciones" else ("incluir_pdf", "subir_pdf")):
            if target not in normalized:
                normalized[target] = pdf
                break

    for origen, destino_fecha, destino_periodo in (
        ("desde", "fecha_desde", "periodo_desde"),
        ("hasta", "fecha_hasta", "periodo_hasta"),
    ):
        if origen not in normalized:
            continue
        destino = (
            destino_periodo if bot in PERIODO_ALIAS_BOTS else destino_fecha
        )
        normalized.setdefault(destino, normalized[origen])
        normalized.pop(origen, None)

    upload_flag = None
    upload_target_by_bot = {
        "aportes_en_linea": "subir",
        "arba": "subir",
        "ccma": "subir",
        "certificado_mipyme": "subir",
        "compensaciones": "subir",
        "comprobantes": "subir_csv",
        "consulta_pagos_vep": "subir_csv",
        "controladores_fiscales": "subir_constancia",
        "declaracion_en_linea": "subir_archivos",
        "hacienda": "subir_excel",
        "libros_portal_iva": "subir_archivos",
        "liquidacion_granos": "subir_archivos",
        "mis_comprobantes": "subir_csv",
        "mis_facilidades": "subir_archivos",
        "mis_retenciones": "subir_csv",
        "mis_retenciones_iva_simple": "subir_csv",
        "moa": "subir_csv",
        "pago_devoluciones": "subir_archivo",
        "portal_iva": "subir_csv",
        "rcel": "subir_pdf",
        "retper_iibb_agip": "subir_archivo",
        "retper_iibb_misiones": "subir_archivo",
        "sifere": "subir",
        "siper": "subir",
        "vep_archivo": "subir_pdf",
        "vep_ccma": "subir_pdf",
    }
    for source in _UPLOAD_FLAG_SOURCES:
        if source in normalized:
            upload_flag = normalized.pop(source)
            break
    if upload_flag is not None:
        target = upload_target_by_bot.get(bot)
        if target is not None:
            normalized.setdefault(target, upload_flag)
        else:
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
