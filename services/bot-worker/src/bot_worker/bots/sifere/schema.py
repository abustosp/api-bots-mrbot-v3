"""Esquemas de entrada del plugin ``sifere``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/sifere_bot.py``: ``bot_sifere`` y
``_validar_periodo``): CUIT de 11 digitos, periodo ``AAAAMM`` con mes
01-12 y jurisdicciones del padron 901-924.

Paridad adicional con V1 (``api-bots-mrbot/app/bot/sifere_bot.py``,
``bot_sifere``):

- ``cuit_representado`` es alias valido del CUIT consultado (igual que
  ``cuit``).
- Los campos del sobre sellado ``cuit_representante``,
  ``clave_representante``, ``clave_encriptada`` y ``proxy_request`` se
  descartan antes de validar, como en ``bots/arba/schema.py``: la
  central los sella para el flujo de navegador y no viajan al plugin.
- ``representado_nombre`` (V1: ``representado``) es opcional y puede
  venir vacio/``null``: en ese caso el nombre de archivo usa el CUIT.
- ``jurisdicciones: []`` ya no es error: significa no consultar SIRCAR
  por jurisdiccion (V1 recorria la lista vacia sin fallar). ``null``
  sigue significando el padron completo 901-924.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
PERIODO_PATTERN = r"^\d{6}$"
JURISDICCION_MIN = 901
JURISDICCION_MAX = 924
#: Campos del contrato compartido con V1 que la central sella y V3 descarta.
CAMPOS_SOBRE_SELLADO = (
    "cuit_representante",
    "clave_representante",
    "clave_encriptada",
    "proxy_request",
)


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
    """Operacion ``consultar``: retenciones/percepciones SIFERE por jurisdiccion."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices(
            "representado_cuit", "cuit", "cuit_representado"
        ),
        min_length=11,
        max_length=14,
    )
    periodo: str = Field(min_length=6, max_length=6)
    representado_nombre: str | None = Field(
        default=None,
        validation_alias=AliasChoices("representado_nombre", "representado"),
        max_length=256,
    )
    jurisdicciones: list[int] | None = None
    incluir_json: bool = True
    # V1 usa ``carga_minio``: decide si los Excel se suben. Alias de ``subir``.
    subir: bool = Field(
        default=True,
        validation_alias=AliasChoices("subir", "carga_minio"),
    )
    # Por defecto genera los Excel de Retenciones/Coeficientes (SIRCREB) y de
    # SIRTAC del periodo, como V1 (sifere_bot.py). Se puede apagar.
    consolidados: bool = True

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            # La central sella estos campos para su propio flujo: nunca
            # deben llegar a la validacion del plugin (extra="forbid").
            for clave in CAMPOS_SOBRE_SELLADO:
                datos.pop(clave, None)
            for clave in ("representado_cuit", "cuit", "cuit_representado"):
                if clave in datos and datos[clave] is not None:
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos

    @model_validator(mode="after")
    def _periodo_y_jurisdicciones(self) -> SifereConsultarInput:
        object.__setattr__(self, "periodo", normalizar_periodo(self.periodo))
        nombre = (self.representado_nombre or "").strip()
        object.__setattr__(self, "representado_nombre", nombre or None)
        if self.jurisdicciones is None:
            object.__setattr__(
                self,
                "jurisdicciones",
                list(range(JURISDICCION_MIN, JURISDICCION_MAX + 1)),
            )
        else:
            # Lista vacia valida: no se consulta SIRCAR por jurisdiccion.
            validas = sorted(
                {j for j in self.jurisdicciones if JURISDICCION_MIN <= j <= JURISDICCION_MAX}
            )
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
            "cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "cuit_representado": {"type": "string", "pattern": CUIT_PATTERN},
            "periodo": {"type": "string", "pattern": PERIODO_PATTERN},
            "representado_nombre": {"type": ["string", "null"]},
            "representado": {"type": ["string", "null"]},
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
            "consolidados": {"type": "boolean"},
        },
    }
