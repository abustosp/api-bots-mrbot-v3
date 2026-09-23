"""Bot ``siper``: detalle e historia de categorias SIPER (port real, ola 3)."""

from bot_worker.bots.siper.plugin import SiperRealPlugin, nombre_captura_siper
from bot_worker.bots.siper.schema import SiperConsultarInput

__all__ = ["SiperConsultarInput", "SiperRealPlugin", "nombre_captura_siper"]
