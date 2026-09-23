"""Esquemas de entrada del bot ``moa`` (ola 3, plan 07).

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/moa_bot.py``: ``moa_bot``) a un modelo
Pydantic que falla antes de abrir el navegador:

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios). El
  representado es obligatorio en V2 (los despachos pertenecen a su
  arbol de empresa).
- ``despachos`` no vacio (hasta 100 identificadores, port del loop de
  V2 que itera la lista y cuenta exitos/errores).
- ``tipo_agente`` y ``rol`` con los valores por defecto de V2.
- Al menos una salida (JSON de datos y/o CSV).
- Aliases de compatibilidad V1/V2: ``cuit``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
MAX_DESPACHOS = 100

TIPO_AGENTE_DEFECTO = "IMEX-IMEX-IMPORTADOR/EXPORT."
ROL_DEFECTO = "IMEX-Rol Importador Exportador"


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


class MoaConsultarInput(_Base):
    """Operacion ``consultar``: scrapeo de despachos del arbol MOA."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    despachos: list[str] = Field(min_length=1, max_length=MAX_DESPACHOS)
    tipo_agente: str = Field(default=TIPO_AGENTE_DEFECTO, min_length=1, max_length=128)
    rol: str = Field(default=ROL_DEFECTO, min_length=1, max_length=128)
    metodo: Literal["url", "form"] = "url"
    incluir_json: bool = True
    subir_csv: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            for clave in ("representado_cuit", "cuit"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = limpiar_cuit(datos[clave])
            if "despachos" in datos and isinstance(datos["despachos"], list):
                vistos: list[str] = []
                for item in datos["despachos"]:
                    texto = str(item).strip()
                    if not texto:
                        raise ValueError("despachos no puede tener vacios")
                    if len(texto) > 64:
                        raise ValueError("cada despacho admite hasta 64 caracteres")
                    if texto not in vistos:
                        vistos.append(texto)
                datos["despachos"] = vistos
        return datos

    @model_validator(mode="after")
    def _al_menos_una_salida(self) -> MoaConsultarInput:
        if not self.despachos:
            raise ValueError("despachos no puede estar vacio")
        if not self.incluir_json and not self.subir_csv:
            raise ValueError("seleccionar al menos una salida (json o csv)")
        return self


OperacionMoa = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": MoaConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit", "despachos"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "despachos": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_DESPACHOS,
                "items": {"type": "string", "minLength": 1, "maxLength": 64},
            },
            "tipo_agente": {"type": "string"},
            "rol": {"type": "string"},
            "metodo": {"type": "string", "enum": ["url", "form"]},
            "incluir_json": {"type": "boolean"},
            "subir_csv": {"type": "boolean"},
        },
    }
