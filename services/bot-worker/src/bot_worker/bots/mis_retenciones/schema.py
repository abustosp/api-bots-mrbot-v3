"""Esquemas de entrada del bot ``mis_retenciones`` (ola 3, plan 07).

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/mis_retenciones_bot.py``:
``bot_mis_retenciones``) a un modelo Pydantic que falla antes de abrir
el navegador:

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios).
- Fechas ``dd/mm/aaaa`` con ``desde <= hasta``.
- Impuestos por codigo SIAP (port de ``IMPUESTOS_CONFIG``): 216, 217,
  219, 353, 767 y 787. Por defecto se consultan todos. Con
  ``exportar_para_aplicativo`` rige ``SIAP_IMPUESTOS_CONFIG`` (sin 787):
  pedir 787 en ese modo es error de entrada.
- Tipos ``Retencion``/``Percepcion`` segun impuesto (port de V2: 216 y
  787 solo admiten ``Retencion``).
- Al menos una salida (JSON de muestra y/o CSV).
- Aliases de compatibilidad V1/V2: ``desde``/``hasta`` y ``cuit``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
FORMATO_FECHA = "%d/%m/%Y"

IMPUESTOS_VALIDOS = ("216", "217", "219", "353", "767", "787")
IMPUESTOS_SIAP = ("216", "217", "219", "767", "353")
TIPOS_POR_IMPUESTO: dict[str, tuple[str, ...]] = {
    "216": ("Retencion",),
    "217": ("Retencion", "Percepcion"),
    "219": ("Retencion", "Percepcion"),
    "353": ("Retencion", "Percepcion"),
    "767": ("Retencion", "Percepcion"),
    "787": ("Retencion",),
}


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


def normalizar_fecha(valor: Any) -> str:
    """Acepta ``dd/mm/aaaa`` y la devuelve normalizada en ese formato."""
    texto = str(valor).strip()
    try:
        return datetime.strptime(texto, FORMATO_FECHA).strftime(FORMATO_FECHA)
    except ValueError:
        raise ValueError(f"fecha debe tener formato dd/mm/aaaa: {texto!r}") from None


class MisRetencionesConsultarInput(_Base):
    """Operacion ``consultar``: retenciones/percepciones por impuesto."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    representado_nombre: str = Field(min_length=1, max_length=128)
    fecha_desde: str = Field(
        validation_alias=AliasChoices("fecha_desde", "desde"),
        min_length=10,
        max_length=10,
    )
    fecha_hasta: str = Field(
        validation_alias=AliasChoices("fecha_hasta", "hasta"),
        min_length=10,
        max_length=10,
    )
    impuestos: list[str] | None = Field(default=None, max_length=6)
    tipos: list[Literal["Retencion", "Percepcion"]] = Field(
        default_factory=lambda: ["Retencion", "Percepcion"]
    )
    exportar_para_aplicativo: bool = False
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
            if "impuestos" in datos and datos["impuestos"] is not None:
                datos["impuestos"] = [str(c).strip() for c in datos["impuestos"]]
        return datos

    @model_validator(mode="after")
    def _validar(self) -> MisRetencionesConsultarInput:
        desde = normalizar_fecha(self.fecha_desde)
        hasta = normalizar_fecha(self.fecha_hasta)
        object.__setattr__(self, "fecha_desde", desde)
        object.__setattr__(self, "fecha_hasta", hasta)
        if datetime.strptime(desde, FORMATO_FECHA) > datetime.strptime(
            hasta, FORMATO_FECHA
        ):
            raise ValueError("fecha_desde no puede ser posterior a fecha_hasta")
        if self.impuestos is not None:
            desconocidos = [c for c in self.impuestos if c not in IMPUESTOS_VALIDOS]
            if desconocidos:
                raise ValueError(f"impuestos desconocidos: {desconocidos}")
            if self.exportar_para_aplicativo:
                fuera = [c for c in self.impuestos if c not in IMPUESTOS_SIAP]
                if fuera:
                    raise ValueError(
                        f"impuestos no disponibles para aplicativo: {fuera}"
                    )
            for codigo in self.impuestos:
                admitidos = TIPOS_POR_IMPUESTO[codigo]
                invalidos = [t for t in self.tipos if t not in admitidos]
                if invalidos:
                    raise ValueError(
                        f"impuesto {codigo} no admite tipos: {invalidos}"
                    )
        if not self.tipos:
            raise ValueError("seleccionar al menos un tipo")
        if not self.incluir_json and not self.subir_csv:
            raise ValueError("seleccionar al menos una salida (json o csv)")
        return self


OperacionMisRetenciones = Literal["consultar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "consultar": MisRetencionesConsultarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``consultar``, sin credenciales."""
    return {
        "type": "object",
        "required": [
            "operacion",
            "representado_cuit",
            "representado_nombre",
            "fecha_desde",
            "fecha_hasta",
        ],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "representado_nombre": {"type": "string"},
            "fecha_desde": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "fecha_hasta": {"type": "string", "pattern": r"^\d{2}/\d{2}/\d{4}$"},
            "impuestos": {
                "type": "array",
                "maxItems": 6,
                "items": {"type": "string", "enum": list(IMPUESTOS_VALIDOS)},
            },
            "tipos": {
                "type": "array",
                "items": {"type": "string", "enum": ["Retencion", "Percepcion"]},
            },
            "exportar_para_aplicativo": {"type": "boolean"},
            "incluir_json": {"type": "boolean"},
            "subir_csv": {"type": "boolean"},
        },
    }
