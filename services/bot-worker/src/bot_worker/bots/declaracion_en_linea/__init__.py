"""Bot ``declaracion_en_linea`` (DDJJ en linea, worker V3)."""

from bot_worker.bots.declaracion_en_linea.plugin import DeclaracionEnLineaPlugin
from bot_worker.bots.declaracion_en_linea.schema import (
    DeclaracionEnLineaConsultarInput,
    generar_rango_periodos,
)

__all__ = [
    "DeclaracionEnLineaConsultarInput",
    "DeclaracionEnLineaPlugin",
    "generar_rango_periodos",
]
