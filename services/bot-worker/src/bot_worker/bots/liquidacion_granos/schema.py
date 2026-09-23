"""Esquemas de entrada del bot ``liquidacion_granos`` (ola 3, plan 07).

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/liquidacion_granos_bot.py``:
``descargar_liquidacion_granos`` / ``liquidacion_granos``) a un modelo
Pydantic que falla antes de abrir el navegador:

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios).
- Fechas ``dd/mm/aaaa`` con ``desde <= hasta``.
- Al menos una seccion (LPG emitidas/recibidas, LSG emitidas/recibidas,
  certificados de deposito) y al menos una salida.
- Aliases de compatibilidad V1/V2: ``desde``/``hasta`` y ``cuit``.
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


class LiquidacionGranosConsultarInput(_Base):
    """Operacion ``consultar``: planillas y comprobantes del rango."""

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
    lpg_emitidas: bool = True
    lpg_recibidas: bool = True
    lsg_emitidas: bool = True
    lsg_recibidas: bool = True
    certificados_deposito: bool = True
    subir_archivos: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar_cuit(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            for clave in ("representado_cuit", "cuit"):
                if clave in datos and datos[clave] is not None:
                    datos = dict(datos)
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos

    @model_validator(mode="after")
    def _rango_y_salidas(self) -> LiquidacionGranosConsultarInput:
        desde = normalizar_fecha(self.fecha_desde)
        hasta = normalizar_fecha(self.fecha_hasta)
        object.__setattr__(self, "fecha_desde", desde)
        object.__setattr__(self, "fecha_hasta", hasta)
        if datetime.strptime(desde, FORMATO_FECHA) > datetime.strptime(
            hasta, FORMATO_FECHA
        ):
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
        secciones = (
            self.lpg_emitidas,
            self.lpg_recibidas,
            self.lsg_emitidas,
            self.lsg_recibidas,
            self.certificados_deposito,
        )
        if not any(secciones):
            raise ValueError("seleccionar al menos una seccion a descargar")
        return self


OperacionLiquidacionGranos = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": LiquidacionGranosConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": [
            "operacion",
            "representado_cuit",
            "representado_nombre",
            "fecha_desde",
            "fecha_hasta",
        ],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "representado_nombre": {"type": "string"},
            "fecha_desde": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "fecha_hasta": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "lpg_emitidas": {"type": "boolean"},
            "lpg_recibidas": {"type": "boolean"},
            "lsg_emitidas": {"type": "boolean"},
            "lsg_recibidas": {"type": "boolean"},
            "certificados_deposito": {"type": "boolean"},
            "subir_archivos": {"type": "boolean"},
        },
    }
