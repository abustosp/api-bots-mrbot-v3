"""Servicios de portal portados de V1/V2.

El runtime base (`ArcaSession`) resuelve login, apertura del servicio,
captcha de imagen y cierre. Los plugins de cada bot, en cambio, llaman
acciones propias del portal (`descargar_reporte`, `consultar_cuit`,
`listar_planes`, ...). Este paquete implementa esas acciones portando el
flujo de los bots V1/V2 (`api-bots-mrbot/app/bot/<bot>_bot.py`).

Cada portal se registra por nombre de bot y se pide desde el plugin con
``open_service(nombre, portal="<bot>")``.
"""
from __future__ import annotations

from .base import PortalArca
from .sct import SctPortal
from .srt import SrtPortal

PORTALES: dict[str, type[PortalArca]] = {
    "sct": SctPortal,
    "srt": SrtPortal,
}


def portal_para(bot: str) -> type[PortalArca] | None:
    """Clase de portal registrada para un bot, o ``None`` si no hay port."""
    return PORTALES.get(str(bot or "").strip().lower())


__all__ = ["PORTALES", "PortalArca", "SctPortal", "SrtPortal", "portal_para"]
