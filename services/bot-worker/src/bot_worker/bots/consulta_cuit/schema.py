"""Esquemas de entrada del piloto ``consulta_cuit`` (ola 1, plan 07).

Porta la validacion implicita de V2
(``api-bots-mrbot-v2/app/bot/consulta_cuit.py``): un CUIT son exactamente
11 digitos. No se exige digito verificador porque los fixtures del plan
usan CUIT de ejemplo no verificables.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CUIT_PATTERN = r"^\d{11}$"
MAX_CUITS_MASIVO = 100


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _validar_cuit(valor: Any) -> str:
    """Normaliza un CUIT quitando separadores y exige 11 digitos."""
    texto = (
        str(valor)
        .replace(" ", "")
        .replace("-", "")
        .replace(".", "")
        .replace("/", "")
    )
    if len(texto) != 11 or not texto.isdigit():
        raise ValueError("cuit debe tener 11 digitos")
    return texto


class ConsultaCuitInput(_Base):
    """Entrada de la operacion ``consultar``: un solo CUIT."""

    cuit: Annotated[str, Field(min_length=11, max_length=11)]

    @field_validator("cuit", mode="before")
    @classmethod
    def _normalizar_cuit(cls, valor: Any) -> str:
        return _validar_cuit(valor)


class ConsultaCuitMasivaInput(_Base):
    """Entrada de la operacion ``consultar_masivo``: lote de CUIT."""

    cuits: Annotated[list[str], Field(min_length=1, max_length=MAX_CUITS_MASIVO)]

    @field_validator("cuits", mode="before")
    @classmethod
    def _cuits(cls, valor: Any) -> list[str]:
        if not isinstance(valor, list):
            raise ValueError("cuits debe ser lista")
        vistos: list[str] = []
        for item in valor:
            normalizado = _validar_cuit(item)
            if normalizado not in vistos:
                vistos.append(normalizado)
        if not vistos:
            raise ValueError("cuits no puede estar vacia")
        return vistos


OperacionConsultaCuit = Literal["consultar", "consultar_masivo"]


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema combinado de ambas operaciones, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {
                "type": "string",
                "enum": ["consultar", "consultar_masivo"],
            },
            "cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "cuits": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_CUITS_MASIVO,
                "items": {"type": "string", "pattern": CUIT_PATTERN},
            },
        },
    }
