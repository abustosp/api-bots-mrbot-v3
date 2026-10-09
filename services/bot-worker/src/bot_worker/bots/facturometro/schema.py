"""Esquemas de entrada del bot ``facturometro`` (worker V3).

Porta la validacion implicita de V2
(``api-bots-mrbot-v2/app/bot/facturometro_bot.py``, ``bot_facturometro``):
CUIT de sesion y CUIT representado para leer monto, tope y categoria del
facturometro de Monotributo. El bot es de solo lectura y no produce
archivos: la respuesta es el trio presentado por el servicio.
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


class FacturometroConsultarInput(_Base):
    """Entrada de ``consultar``: lectura del facturometro del representado.

    Si no se informa representado se usa el CUIT de la sesion, como V1
    (que en ese caso solo clickeaba el enlace de representado).
    """

    representado_cuit: str | None = Field(
        default=None,
        validation_alias=AliasChoices("representado_cuit", "cuit", "cuit_representado"),
        min_length=11,
        max_length=14,
    )
    incluir_captura: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "incluir_captura", "carga_minio", "subir_captura"
        ),
    )
    # Campos de V1 que el sobre sellado resuelve: se aceptan y se ignoran.
    cuit_representante: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "cuit_representante", "cuit_inicio_sesion", "cuit_login"
        ),
        repr=False,
        max_length=14,
    )
    proxy_request: bool | None = None
    headless: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalizar_cuit(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            for clave in (
                "representado_cuit",
                "cuit",
                "cuit_representado",
                "cuit_representante",
                "cuit_inicio_sesion",
                "cuit_login",
            ):
                if clave in datos and datos[clave] is not None:
                    datos = dict(datos)
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos


OperacionFacturometro = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": FacturometroConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "incluir_captura": {"type": "boolean"},
        },
    }
