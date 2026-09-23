"""Bot ``mis_facilidades`` (ola 3): planes de facilidades de pago."""

from bot_worker.bots.mis_facilidades.plugin import (
    MisFacilidadesPlugin,
    nombre_base_archivo,
)
from bot_worker.bots.mis_facilidades.schema import MisFacilidadesConsultarInput

__all__ = [
    "MisFacilidadesConsultarInput",
    "MisFacilidadesPlugin",
    "nombre_base_archivo",
]
