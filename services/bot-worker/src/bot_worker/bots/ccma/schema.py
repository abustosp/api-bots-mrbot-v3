"""Esquemas de entrada del bot ``ccma``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/ccma_bot.py::bot_ccma``):

- CUIT de 11 digitos (alias ``cuit``).
- Periodo desde ``MM/AAAA`` (V2 usa ``04/2000`` fijo para el calculo
  de deuda; aca es configurable con ese mismo default).
- Flags de salida: movimientos en JSON y/o PDF (el PDF requiere
  movimientos, igual que V2).
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
PERIODO_DESDE_DEFAULT = "04/2000"


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


def normalizar_periodo_desde(valor: Any) -> str:
    """Acepta ``MM/AAAA`` con mes 01-12 y lo devuelve normalizado."""
    texto = str(valor or "").strip()
    if not re.match(r"^\d{2}/\d{4}$", texto):
        raise ValueError(f"periodo_desde debe tener formato MM/AAAA: {texto!r}")
    mes = int(texto[:2])
    if not 1 <= mes <= 12:
        raise ValueError(f"periodo_desde invalido: mes {mes} fuera de rango.")
    return texto


class CcmaConsultarInput(_Base):
    """Operacion ``consultar``: saldos CCMA y movimientos opcionales."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    periodo_desde: str = Field(default=PERIODO_DESDE_DEFAULT)
    incluir_movimientos: bool = False
    incluir_pdf: bool = False
    subir: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            if datos.get("representado_cuit") is not None or datos.get("cuit") is not None:
                clave = (
                    "representado_cuit"
                    if datos.get("representado_cuit") is not None
                    else "cuit"
                )
                datos[clave] = limpiar_cuit(datos[clave])
            if datos.get("periodo_desde") is not None:
                datos["periodo_desde"] = normalizar_periodo_desde(
                    datos["periodo_desde"]
                )
        return datos

    @model_validator(mode="after")
    def _pdf_requiere_movimientos(self) -> CcmaConsultarInput:
        if self.incluir_pdf and not self.incluir_movimientos:
            object.__setattr__(self, "incluir_movimientos", True)
        return self


OperacionCcma = Literal["consultar"]


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "periodo_desde": {"type": "string", "pattern": r"^\d{2}/\d{4}$"},
            "incluir_movimientos": {"type": "boolean"},
            "incluir_pdf": {"type": "boolean"},
            "subir": {"type": "boolean"},
        },
    }
