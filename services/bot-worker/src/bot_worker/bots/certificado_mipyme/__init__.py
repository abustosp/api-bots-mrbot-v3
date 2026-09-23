"""Bot ``certificado_mipyme``: certificado MiPyME (LUFE) con navegador."""

from bot_worker.bots.certificado_mipyme.plugin import (
    CertificadoMipymePlugin,
    nombre_certificado,
)
from bot_worker.bots.certificado_mipyme.schema import (
    CertificadoMipymeDescargarInput,
)

__all__ = [
    "CertificadoMipymeDescargarInput",
    "CertificadoMipymePlugin",
    "nombre_certificado",
]
