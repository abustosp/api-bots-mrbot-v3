"""Esquemas de entrada del plugin ``vep_archivo``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/vep_archivo_bot.py``:
``generar_vep_desde_archivo``): CUIT de 11 digitos, medio de pago
habilitado y archivo .txt (contenido b64) de hasta 600 registros.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
MEDIOS_PAGO = ("link", "pago_mis_cuentas", "internet_banking", "xn_group")
MAX_REGISTROS_POR_LOTE = 600
MAX_ARCHIVO_BYTES = 7_000_000


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


def decodificar_archivo(valor: Any) -> bytes:
    """Decodifica el .txt en b64 y limita su tamanio (port de V2)."""
    try:
        crudo = base64.b64decode(str(valor), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("archivo_b64 debe ser base64 valido") from None
    if not crudo:
        raise ValueError("archivo_b64 no puede estar vacio")
    if len(crudo) > MAX_ARCHIVO_BYTES:
        raise ValueError("archivo_b64 excede el maximo permitido")
    return crudo


class VepArchivoGenerarInput(_Base):
    """Operacion ``generar``: VEP desde archivo .txt."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    medio_pago: str = Field(default="internet_banking", min_length=1)
    archivo_nombre: str = Field(min_length=1, max_length=128)
    archivo_b64: str = Field(min_length=1)
    incluir_json: bool = True
    subir_pdf: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            for clave in ("representado_cuit", "cuit"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = limpiar_cuit(datos[clave])
            if "medio_pago" in datos and datos["medio_pago"] is not None:
                datos["medio_pago"] = str(datos["medio_pago"]).strip().lower()
        return datos

    @model_validator(mode="after")
    def _archivo_y_medio(self) -> VepArchivoGenerarInput:
        if self.medio_pago not in MEDIOS_PAGO:
            raise ValueError(f"medio_pago debe ser uno de {list(MEDIOS_PAGO)}")
        if not self.archivo_nombre.lower().endswith(".txt"):
            raise ValueError("archivo_nombre debe terminar en .txt")
        decodificar_archivo(self.archivo_b64)
        if not self.incluir_json and not self.subir_pdf:
            raise ValueError("seleccionar al menos una salida (json o pdf)")
        return self


OperacionVepArchivo = Literal["generar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "generar": VepArchivoGenerarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``generar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["generar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "medio_pago": {"type": "string", "enum": list(MEDIOS_PAGO)},
            "archivo_nombre": {"type": "string", "minLength": 1},
            "archivo_b64": {"type": "string", "minLength": 1},
            "incluir_json": {"type": "boolean"},
            "subir_pdf": {"type": "boolean"},
        },
    }
