"""Esquemas de entrada del plugin ``siper``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/siper_bot.py``: ``bot_siper``): CUIT de
11 digitos y al menos una vista (detalle o categorias) con al menos
una salida (json o png).
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


class SiperConsultarInput(_Base):
    """Operacion ``consultar``: detalle e historia de categorias SIPER."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    incluir_detalle: bool = True
    incluir_categorias: bool = True
    incluir_json: bool = True
    subir: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            for clave in ("representado_cuit", "cuit"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos

    @model_validator(mode="after")
    def _al_menos_vista_y_salida(self) -> SiperConsultarInput:
        if not self.incluir_detalle and not self.incluir_categorias:
            raise ValueError("seleccionar al menos detalle o categorias")
        if not self.incluir_json and not self.subir:
            raise ValueError("seleccionar al menos una salida (json o png)")
        return self


OperacionSiper = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": SiperConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "incluir_detalle": {"type": "boolean"},
            "incluir_categorias": {"type": "boolean"},
            "incluir_json": {"type": "boolean"},
            "subir": {"type": "boolean"},
        },
    }
