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
- Al menos una salida (PDF por plan y/o Excel de tablas).
- Aliases de compatibilidad V1/V2: ``cuit``.
"""

from __future__ import annotations

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


class MisFacilidadesConsultarInput(_Base):
    """Operacion ``consultar``: planes, cuotas y obligaciones."""

    representado_cuit: str | None = Field(
        default=None,
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    denominacion: str | None = Field(default=None, min_length=1, max_length=128)
    situacion_excluyente: list[str] = Field(default_factory=list, max_length=20)
    incluir_pdf: bool = True
    incluir_xlsx: bool = True
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
            "incluir_pdf": {"type": "boolean"},
            "incluir_xlsx": {"type": "boolean"},
            "subir_archivos": {"type": "boolean"},
        },
    }
