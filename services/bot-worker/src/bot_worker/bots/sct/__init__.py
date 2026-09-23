"""Bot ``sct``: sistema de cuentas de ARCA."""

from bot_worker.bots.sct.plugin import SctPlugin, nombre_reporte_sct
from bot_worker.bots.sct.schema import SctConsultarInput

__all__ = ["SctConsultarInput", "SctPlugin", "nombre_reporte_sct"]
