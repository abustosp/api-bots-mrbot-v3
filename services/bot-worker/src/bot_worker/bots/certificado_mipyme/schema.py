"""Esquemas de entrada del bot ``certificado_mipyme``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/certificado_mipyme_bot.py``:
``descargar_certificado_mipyme`` exige CUIT representante, clave y
CUIT representado): en V3 el representante y la clave viajan en las
credenciales del sobre sellado, asi que el esquema solo pide el
representado, con alias ``cuit`` de compatibilidad V1/V2.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

CUIT_PATTERN = r"^\d{11}$"
#: Campos del contrato compartido que V3 no toma del payload (van en el sobre).
CAMPOS_SOBRE_SELLADO = ("clave", "clave_representante", "clave_encriptada", "cuit_representante", "proxy_request")



def _unificar_cuit(datos: dict[str, Any], alias: tuple[str, ...] = ("representado_cuit", "cuit", "cuit_representado")) -> None:
    """Reduce los alias de CUIT del bot a uno.

    Si vienen varios con el mismo valor no hay contradiccion y se acepta. Si
    difieren, el pedido es ambiguo y se rechaza.
    """
    presentes = [c for c in alias if datos.get(c) is not None]
    if not presentes:
        return
    valores = {limpiar_cuit(datos[c]) for c in presentes}
    if len(valores) > 1:
        raise ValueError("los alias de CUIT del pedido no coinciden")
    valor = valores.pop()
    for c in presentes:
        datos.pop(c, None)
    datos["representado_cuit"] = valor


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


class CertificadoMipymeDescargarInput(_Base):
    """Operacion ``descargar``: certificado MiPyME (servicio LUFE)."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit", "cuit_representado"),
        min_length=11,
        max_length=14,
    )
    subir: bool = True

    @model_validator(mode="before")
    @classmethod
    def _descartar_sobre_sellado(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            _unificar_cuit(datos, ("representado_cuit", "cuit", "cuit_representado"))
            for clave in CAMPOS_SOBRE_SELLADO:
                datos.pop(clave, None)
        return datos

    @field_validator("representado_cuit", mode="before")
    @classmethod
    def _normalizar_cuit(cls, valor: Any) -> str:
        return limpiar_cuit(valor)


OperacionCertificadoMipyme = Literal["descargar"]


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``descargar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit"],
        "properties": {
            "operacion": {"type": "string", "enum": ["descargar"]},
            "subir": {"type": "boolean"},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
        },
    }
