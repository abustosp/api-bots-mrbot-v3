"""Version del protocolo y primitivas base del wire format.

Este modulo es la raiz de dependencias del paquete: no importa ningun otro
modulo de `mrbot_contracts`. Contiene la constante normativa
`PROTOCOL_VERSION` (plan 00, S5.2), la version SemVer del paquete, el chequeo
de compatibilidad y las primitivas compartidas (`Uuid7`, `ProtocolMessage`,
`SemVer`, digests). Viven aqui para que `jobs`, `worker`, `artifacts` y
`errors` compartan una sola definicion sin ciclos de importacion.
"""
from __future__ import annotations

import json
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

PROTOCOL_VERSION = 1
PACKAGE_VERSION = "1.0.0"


def is_compatible(request_protocol_version: int) -> bool:
    """Acepta una request si y solo si su version iguala la local (S5.2)."""
    return request_protocol_version == PROTOCOL_VERSION


def _coerce_uuid7(value: Any) -> str:
    try:
        parsed = UUID(str(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"UUID invalido: {value!r}") from exc
    if parsed.version != 7:
        raise ValueError(f"se esperaba UUIDv7, version={parsed.version}")
    return str(parsed)


Uuid7 = Annotated[str, BeforeValidator(_coerce_uuid7)]


def _normalize_sha256(value: Any) -> str:
    text = str(value).lower()
    if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
        raise ValueError("sha256 debe ser 64 caracteres hex")
    return text


Sha256Hex = Annotated[str, BeforeValidator(_normalize_sha256)]

SemVer = Annotated[
    str,
    Field(pattern=r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)([-+][0-9A-Za-z.-]+)?$"),
]
ImageDigest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]

MAX_JSON_PAYLOAD_BYTES = 1_048_576


def check_json_size(value: object, label: str) -> None:
    """Limita el tamano serializado de un payload libre (S6.3)."""
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if len(raw.encode("utf-8")) > MAX_JSON_PAYLOAD_BYTES:
        raise ValueError(f"{label} excede {MAX_JSON_PAYLOAD_BYTES} bytes")


class ProtocolMessage(BaseModel):
    """Base de todo mensaje top-level: version + rechazo de campos extra."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: int = Field(default=PROTOCOL_VERSION, ge=1)
