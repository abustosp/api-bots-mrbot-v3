"""Esquemas de entrada del plugin ``vep_ccma``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/vep_ccma_bot.py``: ``bot_vep_ccma``):
CUIT de 11 digitos, medio de pago habilitado y al menos un origen
(impuestos o intereses) con al menos una salida (json o pdf).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
MEDIOS_PAGO = ("link", "pago_mis_cuentas", "internet_banking", "xn_group")


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


class VepCcmaGenerarInput(_Base):
    """Operacion ``generar``: VEP desde CCMA con volante y QR."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    medio_pago: str = Field(default="internet_banking", min_length=1)
    seleccionar_impuestos: bool = True
    seleccionar_intereses: bool = True
    generar_volante: bool = True
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
    def _origen_y_salida(self) -> VepCcmaGenerarInput:
        if self.medio_pago not in MEDIOS_PAGO:
            raise ValueError(f"medio_pago debe ser uno de {list(MEDIOS_PAGO)}")
        if not self.seleccionar_impuestos and not self.seleccionar_intereses:
            raise ValueError("seleccionar al menos impuestos o intereses")
        if not self.incluir_json and not self.subir_pdf:
            raise ValueError("seleccionar al menos una salida (json o pdf)")
        return self


OperacionVepCcma = Literal["generar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "generar": VepCcmaGenerarInput,
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
            "seleccionar_impuestos": {"type": "boolean"},
            "seleccionar_intereses": {"type": "boolean"},
            "generar_volante": {"type": "boolean"},
            "incluir_json": {"type": "boolean"},
            "subir_pdf": {"type": "boolean"},
        },
    }
