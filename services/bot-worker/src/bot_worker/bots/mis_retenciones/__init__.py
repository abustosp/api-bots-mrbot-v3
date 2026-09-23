"""Bot ``mis_retenciones`` (ola 3): retenciones y percepciones."""

from bot_worker.bots.mis_retenciones.plugin import (
    MisRetencionesPlugin,
    nombre_base_archivo,
)
from bot_worker.bots.mis_retenciones.schema import MisRetencionesConsultarInput

__all__ = [
    "MisRetencionesConsultarInput",
    "MisRetencionesPlugin",
    "nombre_base_archivo",
]
