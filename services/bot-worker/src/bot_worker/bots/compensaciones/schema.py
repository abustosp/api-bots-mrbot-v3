"""Esquemas de entrada del bot ``compensaciones``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/compensaciones_bot.py::bot_compensaciones``):

- CUIT de 11 digitos (alias ``cuit``).
- Rango ``desde``/``hasta`` en ``dd/mm/aaaa`` con ``desde <= hasta``.
- Al menos un formato de salida: Excel (XLS), CSV o PDF.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
FORMATO_FECHA = "%d/%m/%Y"


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


def normalizar_fecha(valor: Any) -> str:
    """Acepta ``dd/mm/aaaa`` y la devuelve normalizada en ese formato."""
    texto = str(valor).strip()
    try:
        return datetime.strptime(texto, FORMATO_FECHA).strftime(FORMATO_FECHA)
    except ValueError:
        raise ValueError(
            f"fecha debe tener formato dd/mm/aaaa: {texto!r}"
        ) from None


class CompensacionesConsultarInput(_Base):
    """Operacion ``consultar``: compensaciones y afectaciones (SCT)."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    fecha_desde: str = Field(
        validation_alias=AliasChoices("fecha_desde", "desde"),
        min_length=10,
        max_length=10,
    )
    fecha_hasta: str = Field(
        validation_alias=AliasChoices("fecha_hasta", "hasta"),
        min_length=10,
        max_length=10,
    )
    excel: bool = False
    csv: bool = False
    pdf: bool = False
    subir: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            for clave in ("representado_cuit", "cuit"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = limpiar_cuit(datos[clave])
            for clave in ("fecha_desde", "desde", "fecha_hasta", "hasta"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = normalizar_fecha(datos[clave])
        return datos

    @model_validator(mode="after")
    def _chequear_rango_y_formatos(self) -> CompensacionesConsultarInput:
        desde = normalizar_fecha(self.fecha_desde)
        hasta = normalizar_fecha(self.fecha_hasta)
        object.__setattr__(self, "fecha_desde", desde)
        object.__setattr__(self, "fecha_hasta", hasta)
        if datetime.strptime(desde, FORMATO_FECHA) > datetime.strptime(
            hasta, FORMATO_FECHA
        ):
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
        if not (self.excel or self.csv or self.pdf):
            raise ValueError(
                "seleccionar al menos un formato de salida (excel, csv o pdf)"
            )
        return self


OperacionCompensaciones = Literal["consultar"]


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit", "fecha_desde", "fecha_hasta"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "fecha_desde": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "fecha_hasta": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "excel": {"type": "boolean"},
            "csv": {"type": "boolean"},
            "pdf": {"type": "boolean"},
            "subir": {"type": "boolean"},
        },
    }
