"""Esquemas de entrada del piloto ``mis_comprobantes`` (ola 2, plan 07).

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/comprobantes_bot.py``:
``descargar_mis_comprobantes``, ``solicitar_consulta_mis_comprobantes`` y
``descarga_csv_consulta``) a modelos Pydantic que fallan antes de abrir
el navegador:

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios).
- Fechas ``dd/mm/aaaa`` con ``desde <= hasta``.
- Al menos un tipo (emitidos o recibidos) y al menos una salida.
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


class _RangoFechas(_Base):
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

    @model_validator(mode="after")
    def _rango_valido(self) -> _RangoFechas:
        desde = normalizar_fecha(self.fecha_desde)
        hasta = normalizar_fecha(self.fecha_hasta)
        object.__setattr__(self, "fecha_desde", desde)
        object.__setattr__(self, "fecha_hasta", hasta)
        if datetime.strptime(desde, FORMATO_FECHA) > datetime.strptime(
            hasta, FORMATO_FECHA
        ):
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
        return self


class _Representado(_Base):
    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    representado_nombre: str = Field(min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def _normalizar_cuit(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            for clave in ("representado_cuit", "cuit"):
                if clave in datos and datos[clave] is not None:
                    datos = dict(datos)
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos


class _TiposComprobante(_Base):
    emitidos: bool = False
    recibidos: bool = False
    puntos_venta_emitidos: list[int] | None = None
    puntos_venta_recibidos: list[int] | None = None

    @model_validator(mode="after")
    def _al_menos_un_tipo(self) -> _TiposComprobante:
        if not self.emitidos and not self.recibidos:
            raise ValueError("seleccionar al menos emitidos o recibidos")
        return self


class MisComprobantesConsultarInput(_RangoFechas, _Representado, _TiposComprobante):
    """Operacion ``consultar``: descarga emitidos/recibidos del rango."""

    incluir_json: bool = True
    subir_csv: bool = True

    @model_validator(mode="after")
    def _al_menos_una_salida(self) -> MisComprobantesConsultarInput:
        if not self.incluir_json and not self.subir_csv:
            raise ValueError("seleccionar al menos una salida (json o csv)")
        return self


class MisComprobantesSolicitarInput(_RangoFechas, _Representado, _TiposComprobante):
    """Operacion ``solicitar``: pide la consulta async sin descargar."""


class MisComprobantesHistorialInput(_RangoFechas, _Representado, _TiposComprobante):
    """Operacion ``historial``: descarga CSV del historial existente."""

    incluir_json: bool = True
    subir_csv: bool = True

    @model_validator(mode="after")
    def _al_menos_una_salida(self) -> MisComprobantesHistorialInput:
        if not self.incluir_json and not self.subir_csv:
            raise ValueError("seleccionar al menos una salida (json o csv)")
        return self


OperacionMisComprobantes = Literal["consultar", "solicitar", "historial"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": MisComprobantesConsultarInput,
    "solicitar": MisComprobantesSolicitarInput,
    "historial": MisComprobantesHistorialInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema combinado de las tres operaciones, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit", "fecha_desde", "fecha_hasta"],
        "properties": {
            "operacion": {
                "type": "string",
                "enum": ["consultar", "solicitar", "historial"],
            },
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "representado_nombre": {"type": "string"},
            "fecha_desde": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "fecha_hasta": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "emitidos": {"type": "boolean"},
            "recibidos": {"type": "boolean"},
            "puntos_venta_emitidos": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
            },
            "puntos_venta_recibidos": {
                "type": "array",
                "items": {"type": "integer", "minimum": 1},
            },
            "incluir_json": {"type": "boolean"},
            "subir_csv": {"type": "boolean"},
        },
    }
