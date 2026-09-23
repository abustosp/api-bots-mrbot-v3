"""Esquema de entrada del bot ``apoc``.

Porta la validacion implicita de V2
(``api-bots-mrbot-v2/app/bot/apoc.py::buscar_cuit_en_base_apoc``): un
CUIT son exactamente 11 digitos (se aceptan separadores comunes).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CUIT_PATTERN = r"^\d{11}$"


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def limpiar_cuit(valor: Any) -> str:
    """Quita separadores comunes y exige 11 digitos (port de V2)."""
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


class ApocConsultarInput(_Base):
    """Entrada de la operacion ``consultar``: un solo CUIT."""

    cuit: Annotated[str, Field(min_length=11, max_length=11)]

    @field_validator("cuit", mode="before")
    @classmethod
    def _normalizar_cuit(cls, valor: Any) -> str:
        return limpiar_cuit(valor)


OperacionApoc = Literal["consultar"]


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "cuit"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "cuit": {"type": "string", "pattern": CUIT_PATTERN},
        },
    }
