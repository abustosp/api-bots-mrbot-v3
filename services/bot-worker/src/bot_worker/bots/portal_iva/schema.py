"""Esquemas de entrada del bot ``portal_iva`` (ola 3, plan 07).

Porta la validacion dispersa en V2
(``api-bots-mrbot-v2/app/bot/portal_iva_bot.py``: ``PortalIvaConfig``,
``ejecutar_descarga`` y ``ejecutar_carga``) a modelos Pydantic que
fallan antes de abrir el navegador:

- CUIT de 11 digitos (se aceptan guiones, puntos y espacios). El
  representado es opcional: si falta, se usa el CUIT representante de
  las credenciales del sobre sellado.
- ``periodo`` en ``mm/aaaa``, ``mm-aaaa``, ``aaaa/mm``, ``aaaa-mm`` o
  ``aaaamm``; se normaliza a ``aaaamm`` (port de
  ``_normalize_period_value`` de V2).
- ``descargar`` exige al menos un libro (ventas y/o compras);
  ``importar`` exige al menos un TXT (contenido inline, que el plugin
  escribe en ``work_dir`` antes de importar); ``gestionar`` combina
  ambas fases como el flujo completo de V2.
- Al menos una salida (JSON de resumen y/o CSV).
- Aliases de compatibilidad V1/V2: ``cuit``, ``descarga_CSV_ventas`` y
  ``descarga_CSV_compras``.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
PERIODO_PATTERN = r"^\d{6}$"


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
    """Normaliza el periodo a ``aaaamm`` (port de V2).

    Acepta ``mm/aaaa``, ``mm-aaaa``, ``aaaa/mm``, ``aaaa-mm`` y
    ``aaaamm``; valida mes 01-12.
    """
    texto = str(valor).strip()
    for patron in (r"(\d{2})/(\d{4})", r"(\d{2})-(\d{4})"):
        coincidencia = re.fullmatch(patron, texto)
        if coincidencia:
            texto = f"{coincidencia.group(2)}{coincidencia.group(1)}"
            break
    else:
        for patron in (r"(\d{4})/(\d{2})", r"(\d{4})-(\d{2})"):
            coincidencia = re.fullmatch(patron, texto)
            if coincidencia:
                texto = f"{coincidencia.group(1)}{coincidencia.group(2)}"
                break
    if not re.fullmatch(PERIODO_PATTERN, texto):
        raise ValueError(f"periodo invalido: {valor!r} (usar mm/aaaa o aaaamm)")
    if not 1 <= int(texto[4:6]) <= 12:
        raise ValueError(f"periodo con mes invalido: {valor!r}")
    return texto


class _Periodo(_Base):
    periodo: str = Field(min_length=6, max_length=7)

    @model_validator(mode="before")
    @classmethod
    def _normalizar_periodo(cls, datos: Any) -> Any:
        if isinstance(datos, dict) and datos.get("periodo") is not None:
            datos = dict(datos)
            datos["periodo"] = normalizar_periodo(datos["periodo"])
        return datos


class _Representado(_Base):
    representado_cuit: str | None = Field(
        default=None,
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    representado_nombre: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def _normalizar_cuit(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            for clave in ("representado_cuit", "cuit"):
                if clave in datos and datos[clave] is not None:
                    datos = dict(datos)
                    datos[clave] = limpiar_cuit(datos[clave])
        return datos


class PortalIvaDescargarInput(_Periodo, _Representado):
    """Operacion ``descargar``: CSV de ventas y/o compras del periodo."""

    descarga_ventas: bool = Field(
        default=False, validation_alias=AliasChoices("descarga_ventas", "descarga_CSV_ventas")
    )
    descarga_compras: bool = Field(
        default=False,
        validation_alias=AliasChoices("descarga_compras", "descarga_CSV_compras"),
    )
    incluir_json: bool = True
    subir_csv: bool = True

    @model_validator(mode="after")
    def _validar(self) -> PortalIvaDescargarInput:
        if not self.descarga_ventas and not self.descarga_compras:
            raise ValueError("seleccionar al menos ventas o compras")
        if not self.incluir_json and not self.subir_csv:
            raise ValueError("seleccionar al menos una salida (json o csv)")
        return self


class PortalIvaImportarInput(_Periodo, _Representado):
    """Operacion ``importar``: importa TXT de ventas y/o compras."""

    ventas_txt: str | None = Field(default=None, min_length=1, max_length=5_000_000)
    compras_txt: str | None = Field(default=None, min_length=1, max_length=5_000_000)

    @model_validator(mode="after")
    def _validar(self) -> PortalIvaImportarInput:
        if not self.ventas_txt and not self.compras_txt:
            raise ValueError("proveer al menos ventas_txt o compras_txt")
        return self


class PortalIvaGestionarInput(PortalIvaDescargarInput):
    """Operacion ``gestionar``: descarga y luego importa (flujo V2)."""

    ventas_txt: str | None = Field(default=None, min_length=1, max_length=5_000_000)
    compras_txt: str | None = Field(default=None, min_length=1, max_length=5_000_000)


OperacionPortalIva = Literal["descargar", "importar", "gestionar"]

ENTRADAS: dict[str, type[BaseModel]] = {
    "descargar": PortalIvaDescargarInput,
    "importar": PortalIvaImportarInput,
    "gestionar": PortalIvaGestionarInput,
}


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema combinado de las tres operaciones, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "periodo"],
        "properties": {
            "operacion": {
                "type": "string",
                "enum": ["descargar", "importar", "gestionar"],
            },
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "representado_nombre": {"type": "string"},
            "periodo": {"type": "string"},
            "descarga_ventas": {"type": "boolean"},
            "descarga_compras": {"type": "boolean"},
            "ventas_txt": {"type": "string"},
            "compras_txt": {"type": "string"},
            "incluir_json": {"type": "boolean"},
            "subir_csv": {"type": "boolean"},
        },
    }
