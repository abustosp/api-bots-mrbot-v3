"""Bot ``retper_iibb_agip``: retenciones/percepciones IIBB de AGIP."""

from bot_worker.bots.retper_iibb_agip.plugin import (
    RetperIibbAgipPlugin,
    nombre_descarga_agip,
)
from bot_worker.bots.retper_iibb_agip.schema import RetperIibbAgipConsultarInput

__all__ = [
    "RetperIibbAgipConsultarInput",
    "RetperIibbAgipPlugin",
    "nombre_descarga_agip",
]
