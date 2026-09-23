"""Esquemas de entrada del bot ``aportes_en_linea``.

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/aportes_en_linea_bot.py::bot_aportes_en_linea``):

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios); si no se
  informa representado se usa el de la credencial (ver plugin).
- Al menos una salida: base64 inline o subida por slot prefirmado.
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


class AportesDescargarInput(_Base):
    """Operacion ``descargar``: archivo historico de Aportes en Linea."""

    representado_cuit: str | None = Field(
        default=None,
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    incluir_base64: bool = False
    subir: bool = True

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
    def _al_menos_una_salida(self) -> AportesDescargarInput:
        if not self.incluir_base64 and not self.subir:
            raise ValueError("seleccionar al menos una salida (base64 o subida)")
        return self


OperacionAportes = Literal["descargar"]


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``descargar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["descargar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "incluir_base64": {"type": "boolean"},
            "subir": {"type": "boolean"},
        },
    }
