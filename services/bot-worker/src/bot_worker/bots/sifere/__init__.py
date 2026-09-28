"""Bot ``sifere``: retenciones SIFERE de COMARB."""

from bot_worker.bots.sifere.plugin import SiferePlugin, nombre_excel_sifere
from bot_worker.bots.sifere.schema import SifereConsultarInput

__all__ = ["SifereConsultarInput", "SiferePlugin", "nombre_excel_sifere"]
