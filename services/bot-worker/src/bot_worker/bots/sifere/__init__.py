"""Bot ``sifere``: retenciones y percepciones SIFERE de COMARB."""

from bot_worker.bots.sifere.plugin import SiferePlugin
from bot_worker.bots.sifere.schema import SifereConsultarInput

__all__ = ["SifereConsultarInput", "SiferePlugin"]
