"""Bot ``facturometro`` (lectura de Monotributo, worker V3)."""

from bot_worker.bots.facturometro.plugin import FacturometroPlugin
from bot_worker.bots.facturometro.schema import FacturometroConsultarInput

__all__ = ["FacturometroConsultarInput", "FacturometroPlugin"]
