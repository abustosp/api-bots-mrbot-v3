"""Bot ``pago_devoluciones`` (ola 3): pagos de devoluciones."""

from bot_worker.bots.pago_devoluciones.plugin import (
    PagoDevolucionesPlugin,
    nombre_base_archivo,
)
from bot_worker.bots.pago_devoluciones.schema import PagoDevolucionesConsultarInput

__all__ = [
    "PagoDevolucionesConsultarInput",
    "PagoDevolucionesPlugin",
    "nombre_base_archivo",
]
