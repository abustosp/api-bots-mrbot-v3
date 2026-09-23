"""Bot ``consulta_pagos_vep`` (consulta de pagos VEP, worker V3)."""

from bot_worker.bots.consulta_pagos_vep.plugin import ConsultaPagosVepPlugin
from bot_worker.bots.consulta_pagos_vep.schema import ConsultaPagosVepInput

__all__ = ["ConsultaPagosVepInput", "ConsultaPagosVepPlugin"]
