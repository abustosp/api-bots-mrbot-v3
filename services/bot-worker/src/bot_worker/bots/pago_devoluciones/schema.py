"""Esquemas de entrada del bot ``pago_devoluciones`` (ola 3, plan 07).

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/pago_devoluciones_bot.py``:
``bot_pago_devoluciones``) a un modelo Pydantic que falla antes de
abrir el navegador:

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios). El
  representado es opcional: si falta, se usa el CUIT representante de
  las credenciales del sobre sellado (port de V2).
- Al menos una salida (JSON de resumen y/o Excel descargado).
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


class PagoDevolucionesConsultarInput(_Base):
    """Operacion ``consultar``: pagos de devoluciones y su Excel."""

    representado_cuit: str | None = Field(
        default=None,
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    incluir_json: bool = True
    subir_archivo: bool = True

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
    def _al_menos_una_salida(self) -> PagoDevolucionesConsultarInput:
        if not self.incluir_json and not self.subir_archivo:
            raise ValueError("seleccionar al menos una salida (json o archivo)")
        return self


OperacionPagoDevoluciones = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": PagoDevolucionesConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "incluir_json": {"type": "boolean"},
            "subir_archivo": {"type": "boolean"},
        },
    }
