"""Bot ``mis_retenciones_iva_simple`` (ola 3): IVA Simple."""

from bot_worker.bots.mis_retenciones_iva_simple.plugin import (
    MisRetencionesIvaSimplePlugin,
    nombre_base_archivo,
)
from bot_worker.bots.mis_retenciones_iva_simple.schema import (
    MisRetencionesIvaSimpleConsultarInput,
)

__all__ = [
    "MisRetencionesIvaSimpleConsultarInput",
    "MisRetencionesIvaSimplePlugin",
    "nombre_base_archivo",
]
