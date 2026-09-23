"""Esquemas de entrada del bot ``carga_portal_iva``.

Porta la validacion de V2
(``api-bots-mrbot-v2/app/bot/carga_portal_iva_bot.py::bot_portal_iva_carga``):

- CUIT de 11 digitos (alias ``cuit``); periodo ``AAAAMM`` no futuro.
- Al menos un par completo de TXT (ventas: comprobantes+alicuotas, o
  compras: comprobantes+alicuotas) o un CSV de apertura (CF, CF
  restitucion, DF, DF restitucion); los pares incompletos se rechazan.
- Los archivos viajan como base64 en el payload (el worker no lee
  rutas del cliente): se validan tamano y contenido minimo antes del
  navegador y se materializan bajo ``work_dir`` en el plugin.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

CUIT_PATTERN = r"^\d{11}$"
PERIODO_PATTERN = r"^\d{6}$"
MAX_ARCHIVO_BYTES = 20_971_520

CAMPOS_ARCHIVO = (
    "liv_cbte_b64",
    "liv_alicuota_b64",
    "lic_cbte_b64",
    "lic_alicuota_b64",
    "csv_cf_b64",
    "csv_cf_restitucion_b64",
    "csv_df_b64",
    "csv_df_restitucion_b64",
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
        raise ValueError(f"periodo {digitos} es futuro.")
    return digitos


def decodificar_archivo(valor: Any, campo: str) -> bytes:
    """Decodifica un base64 y exige contenido minimo no vacio."""
    try:
        contenido = base64.b64decode(str(valor), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError(f"{campo} debe ser base64 valido") from None
    if not contenido:
        raise ValueError(f"{campo} esta vacio")
    if len(contenido) > MAX_ARCHIVO_BYTES:
        raise ValueError(f"{campo} excede el maximo de 20 MB")
    return contenido


class CargaPortalIvaInput(_Base):
    """Operacion ``cargar``: TXTs de ventas/compras y CSVs de apertura."""

    representado_cuit: str = Field(
        validation_alias=AliasChoices("representado_cuit", "cuit"),
        min_length=11,
        max_length=14,
    )
    periodo: str = Field(min_length=6, max_length=6)
    denominacion: str = Field(default="", max_length=128)
    operaciones_ng_o_e: bool = False
    prorrateo_global: bool = False
    prorrateo_asignacion_directa: bool = False
    prorrateo_ambos: bool = False
    importacion_definitiva_bienes: bool = False
    importacion_servicios: bool = False
    regimen_turiva: bool = False
    bienes_usados: bool = False
    ninguna_anteriores: bool = True
    liv_cbte_b64: str | None = None
    liv_alicuota_b64: str | None = None
    lic_cbte_b64: str | None = None
    lic_alicuota_b64: str | None = None
    csv_cf_b64: str | None = None
    csv_cf_restitucion_b64: str | None = None
    csv_df_b64: str | None = None
    csv_df_restitucion_b64: str | None = None

    @field_validator("representado_cuit", mode="before")
    @classmethod
    def _normalizar_cuit(cls, valor: Any) -> str:
        return limpiar_cuit(valor)

    @field_validator("periodo", mode="before")
    @classmethod
    def _normalizar_periodo(cls, valor: Any) -> str:
        return validar_periodo(valor)

    @field_validator(*CAMPOS_ARCHIVO, mode="before")
    @classmethod
    def _validar_archivo(cls, valor: Any, info: Any) -> Any:
        if valor is None:
            return None
        decodificar_archivo(valor, info.field_name)
        return valor

    @model_validator(mode="before")
    @classmethod
    def _chequear_carga(cls, datos: Any) -> Any:
        if not isinstance(datos, dict):
            return datos
        ventas = bool(datos.get("liv_cbte_b64") and datos.get("liv_alicuota_b64"))
        compras = bool(datos.get("lic_cbte_b64") and datos.get("lic_alicuota_b64"))
        csvs = any(
            datos.get(c)
            for c in (
                "csv_cf_b64",
                "csv_cf_restitucion_b64",
                "csv_df_b64",
                "csv_df_restitucion_b64",
            )
        )
        pares_rotos = (
            bool(datos.get("liv_cbte_b64")) != bool(datos.get("liv_alicuota_b64"))
        ) or (
            bool(datos.get("lic_cbte_b64")) != bool(datos.get("lic_alicuota_b64"))
        )
        if pares_rotos:
            raise ValueError(
                "los TXT van de a pares (comprobantes + alicuotas) por seccion"
            )
        if not (ventas or compras or csvs):
            raise ValueError(
                "enviar al menos un par de TXTs (ventas/compras) o un CSV de apertura"
            )
        return datos


OperacionCargaPortalIva = Literal["cargar"]


def esquema_entrada() -> dict[str, Any]:
    """JSON Schema de la operacion ``cargar``, sin credenciales."""
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit", "periodo"],
        "properties": {
            "operacion": {"type": "string", "enum": ["cargar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "periodo": {"type": "string", "pattern": PERIODO_PATTERN},
            "denominacion": {"type": "string"},
            "operaciones_ng_o_e": {"type": "boolean"},
            "prorrateo_global": {"type": "boolean"},
            "prorrateo_asignacion_directa": {"type": "boolean"},
            "prorrateo_ambos": {"type": "boolean"},
            "importacion_definitiva_bienes": {"type": "boolean"},
            "importacion_servicios": {"type": "boolean"},
            "regimen_turiva": {"type": "boolean"},
            "bienes_usados": {"type": "boolean"},
            "ninguna_anteriores": {"type": "boolean"},
            **{
                campo: {"type": "string", "contentEncoding": "base64"}
                for campo in CAMPOS_ARCHIVO
            },
        },
    }
