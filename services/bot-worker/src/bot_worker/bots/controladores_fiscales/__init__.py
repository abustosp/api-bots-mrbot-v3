"""Bot ``controladores_fiscales`` (presentacion fiscal, worker V3)."""

from bot_worker.bots.controladores_fiscales.plugin import ControladoresFiscalesPlugin
from bot_worker.bots.controladores_fiscales.schema import (
    ControladoresFiscalesPresentarInput,
)

__all__ = ["ControladoresFiscalesPlugin", "ControladoresFiscalesPresentarInput"]
