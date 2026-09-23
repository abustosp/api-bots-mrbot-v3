"""Esquemas de entrada del plugin ``rcel``.

Porta la validacion implicita de V2
(``api-bots-mrbot-v2/app/bot/rcel_bot.py``: ``descargar_facturas``):
CUIT de 11 digitos, rango de fechas ``dd/mm/aaaa`` con
``desde <= hasta`` y al menos una salida (json o pdf).
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
        raise ValueError(f"fecha debe tener formato dd/mm/aaaa: {texto!r}") from None


class RcelDescargarInput(_Base):
    """Operacion ``descargar``: facturas del rango en 0..N PDF."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    representado_nombre: str = Field(min_length=1, max_length=128)
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
    incluir_json: bool = True
    subir_pdf: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            for clave in ("representado_cuit", "cuit"):
                if clave in datos and datos[clave] is not None:
                    datos = dict(datos)
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos

    @model_validator(mode="after")
    def _rango_y_salida(self) -> RcelDescargarInput:
        desde = normalizar_fecha(self.fecha_desde)
        hasta = normalizar_fecha(self.fecha_hasta)
        object.__setattr__(self, "fecha_desde", desde)
        object.__setattr__(self, "fecha_hasta", hasta)
        if datetime.strptime(desde, FORMATO_FECHA) > datetime.strptime(
            hasta, FORMATO_FECHA
        ):
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
        if not self.incluir_json and not self.subir_pdf:
            raise ValueError("seleccionar al menos una salida (json o pdf)")
        return self


OperacionRcel = Literal["descargar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "descargar": RcelDescargarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``descargar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["descargar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "representado_nombre": {"type": "string", "minLength": 1},
            "fecha_desde": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "fecha_hasta": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "incluir_json": {"type": "boolean"},
            "subir_pdf": {"type": "boolean"},
        },
    }
