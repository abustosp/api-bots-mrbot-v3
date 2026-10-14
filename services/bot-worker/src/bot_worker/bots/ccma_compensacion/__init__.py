"""Bot ``ccma_compensacion``: compensacion de saldos a favor de CCMA."""

from bot_worker.bots.ccma_compensacion.plugin import CcmaCompensacionPlugin
from bot_worker.bots.ccma_compensacion.schema import CcmaCompensarInput

__all__ = ["CcmaCompensacionPlugin", "CcmaCompensarInput"]
