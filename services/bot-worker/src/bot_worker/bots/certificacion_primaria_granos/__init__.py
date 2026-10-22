"""Bot ``certificacion_primaria_granos``: certificados de depósito como depositante."""

from bot_worker.bots.certificacion_primaria_granos.plugin import (
    CertificacionPrimariaGranosPlugin,
)
from bot_worker.bots.certificacion_primaria_granos.schema import (
    CertificacionPrimariaGranosConsultarInput,
)

__all__ = [
    "CertificacionPrimariaGranosConsultarInput",
    "CertificacionPrimariaGranosPlugin",
]
