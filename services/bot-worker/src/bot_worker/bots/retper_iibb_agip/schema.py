"""Esquemas de entrada del plugin ``retper_iibb_agip``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/retper_iibb_agip_bot.py``:
``bot_retper_iibb_agip``): CUIT de 11 digitos, denominacion no vacia y
periodos ``AAAAMM`` con ``desde <= hasta``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
PERIODO_PATTERN = r"^\d{6}$"


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


def normalizar_periodo(valor: Any) -> str:
    """Acepta ``AAAAMM`` (con o sin separadores) y valida mes 01-12."""
    digitos = re.sub(r"\D", "", str(valor))
    if len(digitos) < 6:
        raise ValueError(f"periodo invalido: {valor!r}. Debe ser AAAAMM.")
    periodo = digitos[:6]
    mes = int(periodo[4:6])
    if not 1 <= mes <= 12:
        raise ValueError(f"mes invalido en periodo: {valor!r}.")
    return periodo


class RetperIibbAgipConsultarInput(_Base):
    """Operacion ``consultar``: retenciones/percepciones AGIP del rango."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    usuario: str | None = Field(default=None, min_length=1, max_length=256)
    denominacion: str = Field(min_length=1, max_length=256)
    periodo_desde: str = Field(
        validation_alias=AliasChoices("periodo_desde", "desde"),
        min_length=6,
        max_length=7,
    )
    periodo_hasta: str = Field(
        validation_alias=AliasChoices("periodo_hasta", "hasta"),
        min_length=6,
        max_length=7,
    )
    incluir_json: bool = True
    subir_archivo: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            for clave in ("representado_cuit", "cuit"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos

    @model_validator(mode="after")
    def _rango_y_salida(self) -> RetperIibbAgipConsultarInput:
        desde = normalizar_periodo(self.periodo_desde)
        hasta = normalizar_periodo(self.periodo_hasta)
        object.__setattr__(self, "periodo_desde", desde)
        object.__setattr__(self, "periodo_hasta", hasta)
        if desde > hasta:
            raise ValueError("periodo_desde no puede ser posterior a periodo_hasta")
        if not self.incluir_json and not self.subir_archivo:
            raise ValueError("seleccionar al menos una salida (json o archivo)")
        return self


OperacionRetperIibbAgip = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": RetperIibbAgipConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "usuario": {"type": "string", "minLength": 1, "maxLength": 256},
            "denominacion": {"type": "string", "minLength": 1},
            "periodo_desde": {"type": "string", "pattern": PERIODO_PATTERN},
            "periodo_hasta": {"type": "string", "pattern": PERIODO_PATTERN},
            "incluir_json": {"type": "boolean"},
            "subir_archivo": {"type": "boolean"},
        },
    }
