"""Bot ``apoc``: condicion de apocrifo por CUIT, sin navegador."""

from bot_worker.bots.apoc.plugin import ApocPlugin, buscar_en_base
from bot_worker.bots.apoc.schema import ApocConsultarInput

__all__ = ["ApocConsultarInput", "ApocPlugin", "buscar_en_base"]
