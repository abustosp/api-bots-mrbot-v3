"""Esquemas de entrada del bot ``arba`` (Ret/Per IIBB).

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/arba_bot.py``: ``_validate_period`` y
``_build_download_name``):

- CUIT de 11 digitos (se aceptan separadores); alias ``cuit``.
- Periodo ``AAAAMM`` con mes 01-12 y no futuro.
- Denominacion obligatoria (alias ``denominacion``); se sanitiza para
  el nombre de archivo igual que V2.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

CUIT_PATTERN = r"^\d{11}$"
PERIODO_PATTERN = r"^\d{6}$"
INVALID_FILENAME_RE = re.compile(r"[\\/:*?\"<>|]+")


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


def validar_periodo(valor: Any) -> str:
    """Exige ``AAAAMM`` con mes valido y no futuro (port de V2)."""
    digitos = re.sub(r"\D", "", str(valor or ""))
    if len(digitos) != 6:
        raise ValueError(f"periodo invalido: {valor!r}. Debe ser AAAAMM.")
    mes = int(digitos[4:6])
    if not 1 <= mes <= 12:
        raise ValueError(f"periodo invalido: mes {mes} fuera de rango.")
    ahora = datetime.now()
    fecha_periodo = datetime(year=int(digitos[:4]), month=mes, day=1)
    if fecha_periodo > ahora.replace(day=1):
        raise ValueError(
            f"periodo {digitos} es futuro. "
            f"No se permiten periodos posteriores a {ahora.strftime('%Y%m')}."
        )
    return digitos


def sanear_parte_nombre(valor: str, default: str) -> str:
    """Quita caracteres invalidos para archivos (port de V2)."""
    texto = re.sub(r"\s+", " ", (valor or "").strip())
    texto = INVALID_FILENAME_RE.sub("", texto).strip()
    return texto or default


def nombre_archivo_descarga(cuit: str, periodo: str, denominacion: str) -> str:
    """Replica el patron V2 ``'<fin> - <cuit> - RETPER ARBA - ...zip'``."""
    digitos = re.sub(r"\D", "", cuit or "")
    if not digitos:
        raise ValueError("CUIT invalido o vacio.")
    seguro = sanear_parte_nombre(denominacion, "SIN_DENOMINACION")
    return f"{digitos[-1]} - {digitos} - RETPER ARBA - {periodo} - {seguro}.zip"


class ArbaDescargarInput(_Base):
    """Operacion ``descargar``: retenciones/percepciones IIBB ARBA."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    periodo: str = Field(min_length=6, max_length=6)
    representado_nombre: str = Field(
        validation_alias=AliasChoices("representado_nombre", "denominacion"),
        min_length=1,
        max_length=128,
    )
    subir: bool = True

    @field_validator("representado_cuit", mode="before")
    @classmethod
    def _normalizar_cuit(cls, valor: Any) -> str:
        return limpiar_cuit(valor)

    @field_validator("periodo", mode="before")
    @classmethod
    def _normalizar_periodo(cls, valor: Any) -> str:
        return validar_periodo(valor)


OperacionArba = Literal["descargar"]


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``descargar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit", "periodo"],
        "properties": {
            "operacion": {"type": "string", "enum": ["descargar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "periodo": {"type": "string", "pattern": PERIODO_PATTERN},
            "representado_nombre": {"type": "string"},
            "subir": {"type": "boolean"},
        },
    }
