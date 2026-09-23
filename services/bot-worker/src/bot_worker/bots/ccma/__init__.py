"""Bot ``ccma``: cuenta corriente de monotributistas/autonomos."""

from bot_worker.bots.ccma.plugin import CcmaPlugin
from bot_worker.bots.ccma.schema import CcmaConsultarInput

__all__ = ["CcmaConsultarInput", "CcmaPlugin"]
