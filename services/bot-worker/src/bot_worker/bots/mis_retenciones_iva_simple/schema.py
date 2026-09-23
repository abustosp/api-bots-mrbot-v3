"""Esquemas de entrada del bot ``mis_retenciones_iva_simple`` (ola 3).

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/mis_retenciones_iva_simple_bot.py``:
``bot_mis_retenciones_iva_simple`` y ``validate_iva_simple_date_range``)
a un modelo Pydantic que falla antes de abrir el navegador:

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios).
- Fechas ``dd/mm/aaaa`` con ``desde <= hasta`` y la regla propia de
  IVA Simple: ``hasta`` no puede superar el ultimo dia del mes
  siguiente a ``desde`` (port exacto de V2).
- Al menos una salida (JSON de muestra y/o CSV).
- Aliases de compatibilidad V1/V2: ``desde``/``hasta`` y ``cuit``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
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


def validar_rango_iva_simple(desde: str, hasta: str) -> None:
    """Port de ``validate_iva_simple_date_range`` de V2.

    ``hasta`` no puede ser anterior a ``desde`` ni posterior al ultimo
    dia del mes siguiente a ``desde``.
    """
    desde_dt = datetime.strptime(desde, FORMATO_FECHA)
    hasta_dt = datetime.strptime(hasta, FORMATO_FECHA)
    if hasta_dt < desde_dt:
        raise ValueError("la fecha hasta no puede ser anterior a la fecha desde")
    if desde_dt.month == 12:
        mes_siguiente = datetime(desde_dt.year + 1, 1, 1)
    else:
        mes_siguiente = datetime(desde_dt.year, desde_dt.month + 1, 1)
    max_hasta = mes_siguiente + timedelta(days=31)
    max_hasta = max_hasta.replace(day=1) - timedelta(days=1)
    if hasta_dt > max_hasta:
        raise ValueError(
            "la fecha hasta no puede ser posterior a "
            f"{max_hasta.strftime(FORMATO_FECHA)} para la fecha desde "
            f"{desde_dt.strftime(FORMATO_FECHA)}"
        )


class MisRetencionesIvaSimpleConsultarInput(_Base):
    """Operacion ``consultar``: retenciones de IVA Simple del rango."""

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
    subir_csv: bool = True

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
    def _rango_valido(self) -> MisRetencionesIvaSimpleConsultarInput:
        desde = normalizar_fecha(self.fecha_desde)
        hasta = normalizar_fecha(self.fecha_hasta)
        object.__setattr__(self, "fecha_desde", desde)
        object.__setattr__(self, "fecha_hasta", hasta)
        validar_rango_iva_simple(desde, hasta)
        if not self.incluir_json and not self.subir_csv:
            raise ValueError("seleccionar al menos una salida (json o csv)")
        return self


OperacionMisRetencionesIvaSimple = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": MisRetencionesIvaSimpleConsultarInput,
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
            "incluir_json": {"type": "boolean"},
            "subir_csv": {"type": "boolean"},
        },
    }
