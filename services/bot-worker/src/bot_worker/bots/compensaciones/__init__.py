"""Bot ``compensaciones``: consulta de compensaciones (SCT) con navegador."""

from bot_worker.bots.compensaciones.plugin import (
    CompensacionesPlugin,
    nombre_descarga,
)
from bot_worker.bots.compensaciones.schema import CompensacionesConsultarInput

__all__ = [
    "CompensacionesConsultarInput",
    "CompensacionesPlugin",
    "nombre_descarga",
]
