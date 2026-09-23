"""Esquemas de entrada del plugin ``srt``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/srt_bot.py``: ``bot_srt_alicuotas``):
lote de 1 a 50 CUIT de 11 digitos para la consulta de alicuotas ART.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

CUIT_PATTERN = r"^\d{11}$"
MAX_CUITS_LOTE = 50


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


class SrtAlicuotasInput(_Base):
    """Operacion ``consultar_alicuotas``: alicuotas ART por CUIT."""

    cuits_consulta: list[str] = Field(min_length=1, max_length=MAX_CUITS_LOTE)
    incluir_json: bool = True
    subir_json: bool = True

    @field_validator("cuits_consulta", mode="before")
    @classmethod
    def _cuits(cls, valor: Any) -> list[str]:
        if not isinstance(valor, list):
            raise ValueError("cuits_consulta debe ser lista")
        vistos: list[str] = []
        for item in valor:
            normalizado = limpiar_cuit(item)
            if normalizado not in vistos:
                vistos.append(normalizado)
        if not vistos:
            raise ValueError("cuits_consulta no puede estar vacia")
        return vistos

    @model_validator(mode="after")
    def _al_menos_una_salida(self) -> SrtAlicuotasInput:
        if not self.incluir_json and not self.subir_json:
            raise ValueError("seleccionar al menos una salida (json o archivo)")
        return self


OperacionSrt = Literal["consultar_alicuotas"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar_alicuotas": SrtAlicuotasInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar_alicuotas"]},
            "cuits_consulta": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_CUITS_LOTE,
                "items": {"type": "string", "pattern": CUIT_PATTERN},
            },
            "incluir_json": {"type": "boolean"},
            "subir_json": {"type": "boolean"},
        },
    }
