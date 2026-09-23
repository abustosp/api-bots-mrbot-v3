"""Bot ``sifere``: retenciones SIFERE de COMARB."""

from bot_worker.bots.sifere.plugin import SiferePlugin, nombre_csv_sifere
from bot_worker.bots.sifere.schema import SifereConsultarInput

__all__ = ["SifereConsultarInput", "SiferePlugin", "nombre_csv_sifere"]
