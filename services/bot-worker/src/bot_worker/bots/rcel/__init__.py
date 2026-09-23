"""Bot ``rcel``: comprobantes en linea (facturas) de ARCA."""

from bot_worker.bots.rcel.plugin import RcelPlugin, nombre_base_rcel
from bot_worker.bots.rcel.schema import RcelDescargarInput

__all__ = ["RcelDescargarInput", "RcelPlugin", "nombre_base_rcel"]
