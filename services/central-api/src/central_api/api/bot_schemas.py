"""Per-operation request schemas and examples for the public bot API.

The public contract intentionally uses a flat body, like V1: fiscal credentials
(such as ``cuit_representante`` and ``clave``) sit beside the bot's query
fields. The route adapter is responsible for moving credentials to the signed
job envelope; this module only describes the public request shape.
"""

from __future__ import annotations

from copy import deepcopy
from functools import lru_cache
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, create_model

from central_api.api.bot_payloads import (
    CompatBodyBase,
    _example_value,
    public_bot_compat_body_model,
)


_FIELD_DESCRIPTIONS: dict[str, str] = {
    "cuit_representante": "CUIT/CUIL del representante que inicia sesión en ARCA.",
    "cuit_login": "CUIT/CUIL de la cuenta que se autentica en ARCA.",
    "cuit_inicio_sesion": "CUIT/CUIL del representante que inicia sesión.",
    "clave": "Clave fiscal del representante. Se envía por HTTPS y no se conserva en el payload del job.",
    "clave_representante": "Clave fiscal del representante. Se envía por HTTPS y no se conserva en el payload del job.",
    "contrasena": "Clave fiscal del representante. Se envía por HTTPS y no se conserva en el payload del job.",
    "clave_encriptada": "Clave fiscal cifrada usando la clave pública de la API.",
    "cuit_representado": "CUIT/CUIL del contribuyente cuyos datos se consultan o modifican.",
    "representado_cuit": "CUIT/CUIL del contribuyente cuyos datos se consultan o modifican.",
    "representado_nombre": "Nombre o razón social del contribuyente representado.",
    "denominacion": "Denominación o razón social del contribuyente representado.",
    "fecha_desde": "Inicio del período solicitado, en formato DD/MM/AAAA.",
    "fecha_hasta": "Fin del período solicitado, en formato DD/MM/AAAA.",
    "desde": "Inicio del período solicitado, en formato DD/MM/AAAA.",
    "hasta": "Fin del período solicitado, en formato DD/MM/AAAA.",
    "periodo": "Período fiscal solicitado, normalmente AAAAMM.",
    "periodo_desde": "Primer período fiscal del rango, en formato AAAAMM.",
    "periodo_hasta": "Último período fiscal del rango, en formato AAAAMM.",
    "emitidos": "Incluir comprobantes emitidos.",
    "recibidos": "Incluir comprobantes recibidos.",
    "incluir_json": "Incluir los datos del resultado en formato JSON.",
    "subir_csv": "Solicitar la generación y carga del archivo CSV de resultado.",
    "subir_archivos": "Solicitar la generación y carga de los archivos de resultado.",
    "subir": "Solicitar la carga del archivo de resultado.",
    "proxy_request": "Configuración opcional de proxy para la solicitud.",
}


def _description(field_name: str) -> str:
    if field_name in _FIELD_DESCRIPTIONS:
        return _FIELD_DESCRIPTIONS[field_name]
    readable = field_name.replace("_", " ")
    if field_name.startswith(("incluir_", "descarga_", "subir_")):
        return f"Indica si se debe {readable.replace('_', ' ')}."
    if field_name.endswith("_b64"):
        return "Contenido del archivo codificado en Base64."
    return f"Valor de {readable} para esta operación del bot."


def _example_values(bot: str, operation: str, *, alternate: bool = False,
                    encrypted: bool = False) -> dict[str, Any]:
    """Build a complete, safe, useful flat body for OpenAPI documentation."""
    model = public_bot_compat_body_model(bot, operation)
    historical: dict[str, Any] = {}
    values: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        if name == "credentials":
            continue
        value = historical.get(name, _example_value(name, bot, operation, field.default))
        if alternate:
            if name in {"fecha_desde", "desde"}:
                value = "01/07/2026"
            elif name in {"fecha_hasta", "hasta"}:
                value = "31/07/2026"
            elif name == "periodo":
                value = "9" if bot == "consulta_pagos_vep" else "202607"
            elif name == "periodo_desde":
                value = "02/2026" if bot == "ccma" else "202607"
            elif name == "periodo_hasta":
                value = "03/2026" if bot == "ccma" else "202608"
            elif name in {"emitidos", "descarga_emitidos", "descarga_ventas"}:
                value = False
            elif name in {"recibidos", "descarga_recibidos", "descarga_compras"}:
                value = True
            elif name in {"incluir_json", "carga_json"}:
                value = False
            elif name in {"subir_csv", "carga_minio", "subir_archivos"}:
                value = True
        if encrypted and name in {"clave", "clave_representante", "contrasena"}:
            continue
        if encrypted and name == "clave_encriptada":
            value = "BASE64_RSA_OAEP_CIPHERTEXT_DEMO"
        values[name] = value

    # Use the canonical flat V1 credential pair even when the historical model
    # used an alias such as cuit_login/contrasena.
    values["cuit_representante"] = "20123456789"
    if encrypted:
        values["clave_encriptada"] = "BASE64_RSA_OAEP_CIPHERTEXT_DEMO"
    else:
        values["clave"] = "REEMPLAZAR_CON_CLAVE_FISCAL"
    return values


def _examples(bot: str, operation: str) -> list[dict[str, Any]]:
    return [
        _example_values(bot, operation),
        _example_values(bot, operation, alternate=True),
        _example_values(bot, operation, encrypted=True),
    ]


@lru_cache(maxsize=None)
def get_request_model(bot: str, operation: str) -> type[BaseModel]:
    """Return the Pydantic request class for a bot operation.

    Every catalogue pair has a dedicated class. Unknown pairs still receive a
    permissive, documented model so callers can keep a generic fallback rather
    than failing during route registration.
    """
    # ``public_bot_compat_body_model`` also provides a documented field-rich
    # fallback for unknown names, while keeping catalogue models precise.
    source_model = public_bot_compat_body_model(bot, operation)
    fields: dict[str, tuple[Any, Any]] = {}
    for name, field in source_model.model_fields.items():
        if name == "credentials":
            continue
        field_info = deepcopy(field)
        field_info.description = field_info.description or _description(name)
        fields[name] = (field.annotation, field_info)

    # A common flat credential pair is present for every bot and operation.
    fields.setdefault(
        "cuit_representante",
        (str | None, Field(default=None, description=_FIELD_DESCRIPTIONS["cuit_representante"], pattern=r"^\d{11}$")),
    )
    fields.setdefault(
        "clave",
        (str | None, Field(default=None, description=_FIELD_DESCRIPTIONS["clave"], max_length=4096)),
    )
    fields.setdefault(
        "clave_encriptada",
        (str | None, Field(default=None, description=_FIELD_DESCRIPTIONS["clave_encriptada"], max_length=16384)),
    )

    base_examples = _examples(bot, operation)
    base = type(
        f"{bot}_{operation}_RequestBase",
        (CompatBodyBase,),
        {
            "model_config": ConfigDict(
                extra="allow",
                str_strip_whitespace=True,
                json_schema_extra={
                    "title": f"{bot.replace('_', ' ').title()} {operation.replace('_', ' ').title()} Request",
                    "description": (
                        f"Cuerpo plano de la operación {bot}/{operation}. "
                        "Incluye los campos de consulta y las credenciales fiscales "
                        "del representante, como en la API V1."
                    ),
                    "examples": base_examples,
                },
            )
        },
    )
    model_name = "".join(part.capitalize() for part in f"{bot}_{operation}_request".split("_"))
    return create_model(model_name, __base__=base, **fields)


def get_openapi_examples(bot: str, operation: str) -> dict[str, dict[str, Any]]:
    """Return examples in FastAPI's ``openapi_examples`` format."""
    return {
        "consulta_habitual": {
            "summary": "Consulta habitual",
            "description": "Ejemplo de consulta con credenciales planas y parámetros de período habituales.",
            "value": _examples(bot, operation)[0],
        },
        "rango_y_opciones": {
            "summary": "Período alternativo y flags",
            "description": "Ejemplo alternativo con fechas/períodos y opciones de salida explícitas.",
            "value": _examples(bot, operation)[1],
        },
        "clave_cifrada": {
            "summary": "Autenticación con clave cifrada",
            "description": "Ejemplo documental que reemplaza la clave en claro por una clave cifrada de demostración.",
            "value": _examples(bot, operation)[2],
        },
    }


__all__ = ["get_request_model", "get_openapi_examples"]
