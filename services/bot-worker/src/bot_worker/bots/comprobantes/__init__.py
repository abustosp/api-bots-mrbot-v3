"""Bot ``comprobantes`` (Mis Comprobantes de ARCA, worker V3)."""

from bot_worker.bots.comprobantes.plugin import ComprobantesPlugin
from bot_worker.bots.comprobantes.schema import (
    ComprobantesConsultarInput,
    ComprobantesHistorialInput,
    ComprobantesSolicitarInput,
)

__all__ = [
    "ComprobantesConsultarInput",
    "ComprobantesHistorialInput",
    "ComprobantesPlugin",
    "ComprobantesSolicitarInput",
]
