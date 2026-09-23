"""Bot ``moa`` (ola 3): despachos del arbol MOA."""

from bot_worker.bots.moa.plugin import MoaPlugin, nombre_base_archivo
from bot_worker.bots.moa.schema import MoaConsultarInput

__all__ = ["MoaConsultarInput", "MoaPlugin", "nombre_base_archivo"]
