"""Esquemas de entrada del plugin ``sct``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/sct_bot.py``: ``bot_sct``): CUIT de 11
digitos, al menos una seccion (vencimientos, deudas o ddjj
pendientes) y al menos un formato (xlsx, csv o pdf).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
SECCIONES_VALIDAS = ("vencimientos", "deudas", "ddjj_pendientes")
FORMATOS_VALIDOS = ("xlsx", "csv", "pdf")


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


class SctConsultarInput(_Base):
    """Operacion ``consultar``: reportes del Sistema de Cuentas."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    secciones: list[str] = Field(min_length=1)
    formatos: list[str] = Field(min_length=1)
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
    def _seleccion_valida(self) -> SctConsultarInput:
        secciones = [str(s).strip().lower() for s in self.secciones]
        if not secciones or any(s not in SECCIONES_VALIDAS for s in secciones):
            raise ValueError(f"secciones deben ser de {list(SECCIONES_VALIDAS)}")
        object.__setattr__(self, "secciones", sorted(set(secciones)))
        formatos = [str(f).strip().lower() for f in self.formatos]
        if not formatos or any(f not in FORMATOS_VALIDOS for f in formatos):
            raise ValueError(f"formatos deben ser de {list(FORMATOS_VALIDOS)}")
        object.__setattr__(self, "formatos", sorted(set(formatos)))
        if not self.incluir_json and not self.subir:
            raise ValueError("seleccionar al menos una salida (json o archivo)")
        return self


OperacionSct = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": SctConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "secciones": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "enum": list(SECCIONES_VALIDAS)},
            },
            "formatos": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "string", "enum": list(FORMATOS_VALIDOS)},
            },
            "incluir_json": {"type": "boolean"},
            "subir": {"type": "boolean"},
        },
    }
