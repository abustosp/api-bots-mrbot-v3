"""Esquemas de entrada del plugin ``sifere``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/sifere_bot.py``: ``bot_sifere`` y
``_validar_periodo``): CUIT de 11 digitos, periodo ``AAAAMM`` con mes
01-12 y jurisdicciones del padron 901-924.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
PERIODO_PATTERN = r"^\d{6}$"
JURISDICCION_MIN = 901
JURISDICCION_MAX = 924


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


def normalizar_periodo(valor: Any) -> str:
    """Exige ``AAAAMM`` con mes 01-12 (port de ``_validar_periodo``)."""
    texto = str(valor).strip()
    if len(texto) != 6 or not texto.isdigit() or not 1 <= int(texto[4:6]) <= 12:
        raise ValueError(f"periodo invalido: {valor!r}. Use formato AAAAMM.")
    return texto


class SifereConsultarInput(_Base):
    """Operacion ``consultar``: retenciones SIFERE por jurisdiccion."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    periodo: str = Field(min_length=6, max_length=6)
    representado_nombre: str = Field(default="", max_length=256)
    jurisdicciones: list[int] | None = None
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
    def _periodo_y_jurisdicciones(self) -> SifereConsultarInput:
        object.__setattr__(self, "periodo", normalizar_periodo(self.periodo))
        if self.jurisdicciones is None:
            object.__setattr__(
                self,
                "jurisdicciones",
                list(range(JURISDICCION_MIN, JURISDICCION_MAX + 1)),
            )
        else:
            validas = sorted(
                {j for j in self.jurisdicciones if JURISDICCION_MIN <= j <= JURISDICCION_MAX}
            )
            if not validas:
                raise ValueError("jurisdicciones debe tener al menos una entre 901 y 924")
            object.__setattr__(self, "jurisdicciones", validas)
        if not self.incluir_json and not self.subir:
            raise ValueError("seleccionar al menos una salida (json o archivo)")
        return self


OperacionSifere = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": SifereConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "periodo": {"type": "string", "pattern": PERIODO_PATTERN},
            "representado_nombre": {"type": "string"},
            "jurisdicciones": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": JURISDICCION_MIN,
                    "maximum": JURISDICCION_MAX,
                },
            },
            "incluir_json": {"type": "boolean"},
            "subir": {"type": "boolean"},
        },
    }
