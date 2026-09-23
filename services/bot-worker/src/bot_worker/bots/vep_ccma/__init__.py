"""Bot ``vep_ccma``: genera VEP desde CCMA con volante y QR."""

from bot_worker.bots.vep_ccma.plugin import VepCcmaPlugin
from bot_worker.bots.vep_ccma.schema import VepCcmaGenerarInput

__all__ = ["VepCcmaGenerarInput", "VepCcmaPlugin"]
