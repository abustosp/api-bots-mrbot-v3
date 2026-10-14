"""Esquemas de entrada del bot ``ccma_compensacion``.

Porta ``app/schemas/ccma_compensacion.py::CompensacionCCMARequest`` (V1/V2):

- CUIT representado de 11 digitos (alias ``cuit``/``cuit_representado``).
- ``compensar`` (default ``True``): reafecta los pagos con saldo a favor.
- ``confirmar`` (default ``False``): confirma las reafectaciones; con
  ``False`` quedan agregadas al detalle sin confirmar en ARCA.
- ``incluir_movimientos`` (alias V1 ``movimientos``).
- Credenciales, ``cuit_representante`` y ``proxy_request`` viajan en el sobre
  sellado: se descartan si llegan en el payload.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from bot_worker.bots.ccma.schema import (
    ALIAS_CUIT,
    CAMPOS_SOBRE_SELLADO,
    CUIT_PATTERN,
    unificar_cuit,
)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CcmaCompensarInput(_Base):
    """Operacion ``compensar``: reafectacion de saldos a favor de CCMA."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices(*ALIAS_CUIT),
        min_length=11,
        max_length=14,
    )
    compensar: bool = True
    confirmar: bool = False
    incluir_movimientos: bool = Field(
        default=False,
        validation_alias=AliasChoices("incluir_movimientos", "movimientos"),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            unificar_cuit(datos)
            for clave in CAMPOS_SOBRE_SELLADO:
                datos.pop(clave, None)
            # V1 acepta ``pdf``/``subir``; V3 no genera PDF de la compensacion.
            for retirado in ("pdf", "incluir_pdf", "subir", "carga_minio", "minio_upload"):
                datos.pop(retirado, None)
            for flag in ("compensar", "confirmar"):
                if datos.get(flag) is None:
                    datos.pop(flag, None)
        return datos


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``compensar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit"],
        "properties": {
            "operacion": {"type": "string", "enum": ["compensar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "cuit_representado": {"type": "string", "pattern": CUIT_PATTERN},
            "compensar": {"type": "boolean"},
            "confirmar": {"type": "boolean"},
            "incluir_movimientos": {"type": "boolean"},
            "movimientos": {"type": "boolean"},
        },
    }
