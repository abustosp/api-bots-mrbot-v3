"""Esquemas de entrada del bot ``mis_facilidades`` (ola 3, plan 07).

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/mis_facilidades_bot.py``:
``bot_mis_facilidades``) a un modelo Pydantic que falla antes de abrir
el navegador:

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios). El
  representado es opcional: si falta, se usa el CUIT representante de
  las credenciales del sobre sellado.
- Filtro opcional ``situacion_excluyente`` (port de V2: excluye planes
  cuya situacion contenga alguno de esos textos, p. ej.
  "Plan Cancelado").
- Filtro opcional por fecha de consolidacion del plan
  (``fecha_consolidacion_desde`` / ``fecha_consolidacion_hasta``,
  ``dd/mm/aaaa``, ambas inclusivas).
- Al menos una salida (PDF por plan y/o Excel de tablas).
- Aliases de compatibilidad V1/V2: ``cuit``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

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


def normalizar_fecha(valor: Any) -> str:
    """Acepta ``dd/mm/aaaa`` y la devuelve normalizada en ese formato."""
    texto = str(valor).strip()
    try:
        return datetime.strptime(texto, "%d/%m/%Y").strftime("%d/%m/%Y")
    except ValueError:
        raise ValueError(f"fecha debe tener formato dd/mm/aaaa: {texto!r}") from None


def parsear_fecha(texto: str) -> date:
    return datetime.strptime(texto, "%d/%m/%Y").date()


class MisFacilidadesConsultarInput(_Base):
    """Operacion ``consultar``: planes, cuotas y obligaciones."""

    representado_cuit: str | None = Field(
        default=None,
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    denominacion: str | None = Field(default=None, min_length=1, max_length=128)
    # V1 usa ``lista_exclusion_situacion``; V3 usa ``situacion_excluyente``. Se aceptan ambos.
    situacion_excluyente: list[str] = Field(
        default_factory=list,
        max_length=20,
        validation_alias=AliasChoices("situacion_excluyente", "lista_exclusion_situacion"),
    )
    # Solo se procesan los planes cuya fecha de consolidacion cae en el rango
    # (extremos inclusivos); cada limite es opcional.
    fecha_consolidacion_desde: str | None = Field(default=None, min_length=10, max_length=10)
    fecha_consolidacion_hasta: str | None = Field(default=None, min_length=10, max_length=10)
    incluir_pdf: bool = True
    incluir_xlsx: bool = True
    # V1 usa ``carga_minio``: decide si los archivos se suben. Alias de ``subir_archivos``.
    subir_archivos: bool = Field(
        default=True,
        validation_alias=AliasChoices("subir_archivos", "carga_minio"),
    )

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
    def _rango_consolidacion(self) -> MisFacilidadesConsultarInput:
        for campo in ("fecha_consolidacion_desde", "fecha_consolidacion_hasta"):
            valor = getattr(self, campo)
            if valor is not None:
                object.__setattr__(self, campo, normalizar_fecha(valor))
        desde, hasta = self.fecha_consolidacion_desde, self.fecha_consolidacion_hasta
        if desde and hasta and parsear_fecha(desde) > parsear_fecha(hasta):
            raise ValueError(
                "fecha_consolidacion_desde no puede ser posterior a fecha_consolidacion_hasta"
            )
        return self

    @model_validator(mode="after")
    def _al_menos_una_salida(self) -> MisFacilidadesConsultarInput:
        if not self.incluir_pdf and not self.incluir_xlsx:
            raise ValueError("seleccionar al menos una salida (pdf o xlsx)")
        return self


OperacionMisFacilidades = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": MisFacilidadesConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "denominacion": {"type": "string"},
            "situacion_excluyente": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string", "minLength": 1},
            },
            "lista_exclusion_situacion": {
                "type": "array",
                "maxItems": 20,
                "items": {"type": "string", "minLength": 1},
            },
            "fecha_consolidacion_desde": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "fecha_consolidacion_hasta": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "incluir_pdf": {"type": "boolean"},
            "incluir_xlsx": {"type": "boolean"},
            "subir_archivos": {"type": "boolean"},
            "carga_minio": {"type": "boolean"},
        },
    }
