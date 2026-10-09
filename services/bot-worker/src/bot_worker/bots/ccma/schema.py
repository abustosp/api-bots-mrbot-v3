"""Esquemas de entrada del bot ``ccma``.

Porta la validacion de V1
(``api-bots-mrbot/app/bot/ccma_bot.py::bot_ccma`` y
``app/schemas/ccma.py::ConsultaCCMARequest``):

- CUIT de 11 digitos (alias ``cuit``/``cuit_representado`` de V1).
- Periodo desde ``MM/AAAA`` (V1 usa ``04/2000`` fijo para el calculo
  de deuda; aca es configurable con ese mismo default).
- Flags de salida: movimientos en JSON y/o PDF (el PDF requiere
  movimientos, igual que V1). V1 los llama ``movimientos`` y ``pdf``;
  se aceptan ambos nombres.
- ``proxy_request`` (V1) y las credenciales viajan en el sobre sellado:
  se descartan si llegan en el payload.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

CUIT_PATTERN = r"^\d{11}$"
PERIODO_DESDE_DEFAULT = "04/2000"
#: Campos del contrato compartido con V1 que V3 no toma del payload (van en
#: el sobre sellado o los resuelve la infraestructura).
CAMPOS_SOBRE_SELLADO = (
    "clave",
    "clave_representante",
    "clave_encriptada",
    "cuit_representante",
    "cuit_inicio_sesion",
    "proxy_request",
    "proxy",
)
ALIAS_CUIT = ("representado_cuit", "cuit", "cuit_representado")


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def limpiar_cuit(valor: Any) -> str:
    """Quita separadores comunes y exige 11 digitos (port de V1)."""
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


def unificar_cuit(
    datos: dict[str, Any], alias: tuple[str, ...] = ALIAS_CUIT
) -> None:
    """Reduce los alias de CUIT del bot a ``representado_cuit``.

    Si vienen varios con el mismo valor no hay contradiccion y se acepta. Si
    difieren, el pedido es ambiguo y se rechaza.
    """
    presentes = [c for c in alias if datos.get(c) is not None]
    if not presentes:
        return
    valores = {limpiar_cuit(datos[c]) for c in presentes}
    if len(valores) > 1:
        raise ValueError("los alias de CUIT del pedido no coinciden")
    valor = valores.pop()
    for c in presentes:
        datos.pop(c, None)
    datos["representado_cuit"] = valor


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
        validation_alias=AliasChoices(*ALIAS_CUIT),
        min_length=11,
        max_length=14,
    )
    periodo_desde: str = Field(default=PERIODO_DESDE_DEFAULT)
    incluir_movimientos: bool = Field(
        default=False,
        validation_alias=AliasChoices("incluir_movimientos", "movimientos"),
    )
    incluir_pdf: bool = Field(
        default=False,
        validation_alias=AliasChoices("incluir_pdf", "pdf"),
    )
    subir: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "subir", "carga_minio", "minio_upload", "archivo_historico_minio"
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _normalizar(cls, datos: Any) -> Any:
        if isinstance(datos, dict):
            datos = dict(datos)
            unificar_cuit(datos)
            for clave in CAMPOS_SOBRE_SELLADO:
                datos.pop(clave, None)
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
    """JSON Schema de la operacion ``consultar``, sin credenciales.

    Documenta los nombres canonicos de V3 y los alias historicos de V1 que
    el modelo acepta (``cuit``/``cuit_representado``, ``movimientos``,
    ``pdf``).
    """
    return {
        "type": "object",
        "required": ["operacion", "representado_cuit"],
        "properties": {
            "operacion": {"type": "string", "enum": ["consultar"]},
            "representado_cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "cuit": {"type": "string", "pattern": CUIT_PATTERN},
            "cuit_representado": {"type": "string", "pattern": CUIT_PATTERN},
            "periodo_desde": {"type": "string", "pattern": r"^\d{2}/\d{4}$"},
            "incluir_movimientos": {"type": "boolean"},
            "movimientos": {"type": "boolean"},
            "incluir_pdf": {"type": "boolean"},
            "pdf": {"type": "boolean"},
            "subir": {"type": "boolean"},
            "carga_minio": {"type": "boolean"},
        },
    }
