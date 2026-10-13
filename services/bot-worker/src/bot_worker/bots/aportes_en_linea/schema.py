"""Esquemas de entrada del bot ``aportes_en_linea``.

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/aportes_en_linea_bot.py::bot_aportes_en_linea``):

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios); si no se
  informa representado se usa el de la credencial (ver plugin).
- Al menos una salida: base64 inline o subida por slot prefirmado.
- Aliases de compatibilidad V1/V2: ``cuit``, ``cuit_representado``,
  ``archivo_historico_b64`` (base64 inline) y ``archivo_historico_minio``
  (subida). ``cuit_representante``, ``clave_representante`` y
  ``proxy_request`` de V1 se aceptan y se ignoran: la credencial y el proxy
  viajan en el sobre sellado, nunca en el payload.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

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
        validation_alias=AliasChoices(
            "representado_cuit", "cuit", "cuit_representado"
        ),
        min_length=11,
        max_length=14,
    )
    # V1/V2 podian pedir el archivo en Base64 dentro del resultado. Ya no se
    # devuelve: el archivo sale por ``files[]``. Se acepta y se ignora.
    incluir_base64: bool = Field(
        default=False,
        exclude=True,
        validation_alias=AliasChoices("incluir_base64", "archivo_historico_b64"),
    )
    subir: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "subir", "archivo_historico_minio", "carga_minio", "subir_archivo"
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
    clave_representante: str | None = Field(
        default=None,
        validation_alias=AliasChoices("clave_representante", "contrasena", "clave"),
        repr=False,
        max_length=4096,
    )
    proxy_request: bool | None = None

    @field_validator("cuit_representante", mode="after")
    @classmethod
    def _normalizar_cuit_representante(cls, valor: str | None) -> str | None:
        return limpiar_cuit(valor) if valor else None

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            for clave in ("representado_cuit", "cuit", "cuit_representado"):
                if clave in datos and datos[clave] is not None:
                    datos = dict(datos)
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos


OperacionAportes = Literal["descargar"]


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``descargar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["descargar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "subir": {"type": "boolean"},
        },
    }
