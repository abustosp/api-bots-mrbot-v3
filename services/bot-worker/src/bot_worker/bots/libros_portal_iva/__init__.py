"""Bot ``libros_portal_iva`` (Portal IVA, worker V3)."""

from bot_worker.bots.libros_portal_iva.plugin import LibrosPortalIvaPlugin
from bot_worker.bots.libros_portal_iva.schema import (
    LibrosPortalIvaDdjjInput,
    LibrosPortalIvaLibrosInput,
    generar_rango_periodos,
)

__all__ = [
    "LibrosPortalIvaDdjjInput",
    "LibrosPortalIvaLibrosInput",
    "LibrosPortalIvaPlugin",
    "generar_rango_periodos",
]
