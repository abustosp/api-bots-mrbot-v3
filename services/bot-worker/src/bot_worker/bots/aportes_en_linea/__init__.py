"""Bot ``aportes_en_linea``: archivo historico de ARCA con navegador."""

from bot_worker.bots.aportes_en_linea.plugin import (
    AportesEnLineaPlugin,
    nombre_archivo_historico,
)
from bot_worker.bots.aportes_en_linea.schema import AportesDescargarInput

__all__ = [
    "AportesDescargarInput",
    "AportesEnLineaPlugin",
    "nombre_archivo_historico",
]
