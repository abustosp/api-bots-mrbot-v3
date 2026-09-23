"""Bot ``liquidacion_granos`` (ola 3): liquidacion primaria de granos."""

from bot_worker.bots.liquidacion_granos.plugin import (
    LiquidacionGranosPlugin,
    nombre_base_archivo,
)
from bot_worker.bots.liquidacion_granos.schema import (
    LiquidacionGranosConsultarInput,
)

__all__ = [
    "LiquidacionGranosConsultarInput",
    "LiquidacionGranosPlugin",
    "nombre_base_archivo",
]
