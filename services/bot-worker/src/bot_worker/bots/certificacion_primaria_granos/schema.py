"""Esquema de entrada del bot ``certificacion_primaria_granos``.

Misma forma que ``liquidacion_granos`` sin las secciones de liquidaciones:
CUIT de 11 digitos, denominacion del representado y rango ``dd/mm/aaaa``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from bot_worker.bots.liquidacion_granos.schema import (
    ALIAS_SUBIDA,
    CAMPOS_SOBRE_SELLADO,
    CUIT_PATTERN,
    FORMATO_FECHA,
    _unificar_cuit,
    normalizar_fecha,
)


class CertificacionPrimariaGranosConsultarInput(BaseModel):
    """Operacion ``consultar``: certificados como depositante del rango."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit", "cuit_representado"),
        min_length=11,
        max_length=14,
    )
    representado_nombre: str = Field(
        validation_alias=AliasChoices("representado_nombre", "denominacion"),
        min_length=1,
        max_length=128,
    )
    fecha_desde: str = Field(
        validation_alias=AliasChoices("fecha_desde", "desde"), min_length=10, max_length=10
    )
    fecha_hasta: str = Field(
        validation_alias=AliasChoices("fecha_hasta", "hasta"), min_length=10, max_length=10
    )
    subir_archivos: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            _unificar_cuit(datos)
            for clave in CAMPOS_SOBRE_SELLADO:
                datos.pop(clave, None)
            presentes = [c for c in ALIAS_SUBIDA if datos.get(c) is not None]
            for c in ALIAS_SUBIDA:
                if c not in presentes:
                    datos.pop(c, None)
            if presentes:
                valores = {bool(datos[c]) for c in presentes}
                if len(valores) > 1:
                    raise ValueError("subir_archivos y minio_upload contradictorios")
                for c in presentes:
                    datos.pop(c)
                datos["subir_archivos"] = valores.pop()
        return datos

    @model_validator(mode="after")
    def _rango(self) -> CertificacionPrimariaGranosConsultarInput:
        desde = normalizar_fecha(self.fecha_desde)
        hasta = normalizar_fecha(self.fecha_hasta)
        object.__setattr__(self, "fecha_desde", desde)
        object.__setattr__(self, "fecha_hasta", hasta)
        if datetime.strptime(desde, FORMATO_FECHA) > datetime.strptime(hasta, FORMATO_FECHA):
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
        return self


ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": CertificacionPrimariaGranosConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    fecha = {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"}
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
            "fecha_desde": fecha,
            "fecha_hasta": fecha,
            "subir_archivos": {"type": "boolean"},
            "minio_upload": {"type": "boolean"},
        },
    }
