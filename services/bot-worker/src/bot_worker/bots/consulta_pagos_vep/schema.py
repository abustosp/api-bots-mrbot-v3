"""Esquemas de entrada del bot ``consulta_pagos_vep`` (worker V3).

Porta la validacion implicita de V2
(``api-bots-mrbot-v2/app/bot/consulta_pagos_vep_bot.py``,
``bot_consulta_pagos_veps``): CUIT de representante y de representado,
periodo de busqueda en meses (por defecto ``"72"`` como en V2) y flags
de salida. Falla antes de abrir el navegador.
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


class ConsultaPagosVepInput(_Base):
    """Entrada de la operacion ``consultar``: pagos VEP del representado."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit", "cuit_representado"),
        min_length=11,
        max_length=14,
    )
    periodo: str = Field(default="72", min_length=1, max_length=3, pattern=r"^\d{1,3}$")
    incluir_json: bool = True
    subir_csv: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar_cuit(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            for clave in ("representado_cuit", "cuit", "cuit_representado"):
                if clave in datos and datos[clave] is not None:
                    datos = dict(datos)
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos

    @model_validator(mode="after")
    def _salida_valida(self) -> ConsultaPagosVepInput:
        if not self.incluir_json and not self.subir_csv:
            raise ValueError("seleccionar al menos una salida (json o csv)")
        if int(self.periodo) < 1:
            raise ValueError("periodo debe ser al menos 1")
        return self


OperacionConsultaPagosVep = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": ConsultaPagosVepInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "periodo": {"type": "string", "pattern": r"^\d{1,3}$"},
            "incluir_json": {"type": "boolean"},
            "subir_csv": {"type": "boolean"},
        },
    }
