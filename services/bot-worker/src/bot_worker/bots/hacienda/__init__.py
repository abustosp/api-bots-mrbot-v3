"""Bot ``hacienda`` (comprobantes de hacienda, worker V3)."""

from bot_worker.bots.hacienda.plugin import HaciendaPlugin
from bot_worker.bots.hacienda.schema import HaciendaConsultarInput

__all__ = ["HaciendaConsultarInput", "HaciendaPlugin"]
