"""Bot ``retper_iibb_misiones``: retenciones/percepciones de ATM Misiones."""

from bot_worker.bots.retper_iibb_misiones.plugin import RetperIibbMisionesPlugin
from bot_worker.bots.retper_iibb_misiones.schema import (
    RetperIibbMisionesConsultarInput,
)

__all__ = ["RetperIibbMisionesConsultarInput", "RetperIibbMisionesPlugin"]
