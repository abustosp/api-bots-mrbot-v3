"""Esquemas de entrada del bot ``hacienda`` (worker V3).

Porta la validacion implicita de V2
(``api-bots-mrbot-v2/app/bot/hacienda_bot.py``, ``hacienda`` /
``descargar_hacienda``): CUIT de representado, denominacion del
contribuyente, rango de fechas ``dd/mm/aaaa`` de comprobantes y flags
por consulta (emisor/receptor) y de salida. Falla antes del navegador.

V2 consolida los cuadros por emisor/receptor en un Excel con pandas; en
V3 el worker no garantiza pandas, asi que el plugin escribe un CSV
consolidado por consulta con la biblioteca estandar.
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


class HaciendaConsultarInput(_Base):
    """Entrada de ``consultar``: comprobantes de hacienda por rango."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit", "cuit_representado"),
        min_length=11,
        max_length=14,
    )
    denominacion: str = Field(min_length=1, max_length=256)
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
    por_emisor: bool = True
    por_receptor: bool = True
    incluir_json: bool = True
    subir_excel: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            for clave in ("representado_cuit", "cuit", "cuit_representado"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = limpiar_cuit(datos[clave])
            for clave in ("fecha_desde", "desde", "fecha_hasta", "hasta"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = normalizar_fecha(datos[clave])
        return datos

    @model_validator(mode="after")
    def _rango_valido(self) -> HaciendaConsultarInput:
        desde = normalizar_fecha(self.fecha_desde)
        hasta = normalizar_fecha(self.fecha_hasta)
        object.__setattr__(self, "fecha_desde", desde)
        object.__setattr__(self, "fecha_hasta", hasta)
        if datetime.strptime(desde, FORMATO_FECHA) > datetime.strptime(
            hasta, FORMATO_FECHA
        ):
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
        if not self.por_emisor and not self.por_receptor:
            raise ValueError("seleccionar al menos por_emisor o por_receptor")
        if not self.incluir_json and not self.subir_excel:
            raise ValueError("seleccionar al menos una salida (json o excel)")
        return self


OperacionHacienda = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": HaciendaConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": [
            "operacion",
            "representado_cuit",
            "denominacion",
            "fecha_desde",
            "fecha_hasta",
        ],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "denominacion": {"type": "string"},
            "fecha_desde": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "fecha_hasta": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "por_emisor": {"type": "boolean"},
            "por_receptor": {"type": "boolean"},
            "incluir_json": {"type": "boolean"},
            "subir_excel": {"type": "boolean"},
        },
    }
