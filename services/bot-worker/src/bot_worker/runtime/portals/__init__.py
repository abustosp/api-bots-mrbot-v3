"""Servicios de portal portados de V1/V2.

El runtime base (`ArcaSession`) resuelve login, apertura del servicio,
captcha de imagen y cierre. Los plugins de cada bot, en cambio, llaman
acciones propias del portal (`descargar_reporte`, `consultar_cuit`,
`listar_planes`, ...). Este paquete implementa esas acciones portando el
flujo de los bots V1/V2 (`api-bots-mrbot/app/bot/<bot>_bot.py`).

Registro por descubrimiento: cada módulo ``<bot>.py`` de este paquete
define exactamente una subclase de ``PortalArca`` y queda registrado con
el nombre del módulo. Así varios ports pueden desarrollarse en paralelo
sin editar este archivo. El plugin la pide con
``open_service(nombre, portal="<bot>")``.
"""
from __future__ import annotations

import importlib
import inspect
import pkgutil

from .base import PortalArca

# Módulos de soporte que no son portales de un bot.
_NO_PORTALES = frozenset({"base", "recaptcha"})


def _descubrir() -> dict[str, type[PortalArca]]:
    registro: dict[str, type[PortalArca]] = {}
    for info in pkgutil.iter_modules(__path__):
        nombre = info.name
        if nombre.startswith("_") or nombre in _NO_PORTALES:
            continue
        modulo = importlib.import_module(f"{__name__}.{nombre}")
        clases = [
            obj
            for obj in vars(modulo).values()
            if inspect.isclass(obj)
            and issubclass(obj, PortalArca)
            and obj is not PortalArca
            and obj.__module__ == modulo.__name__
        ]
        if len(clases) != 1:
            raise ImportError(
                f"el módulo de portal {nombre!r} debe definir exactamente una "
                f"subclase de PortalArca y define {len(clases)}"
            )
        registro[nombre] = clases[0]
    return dict(sorted(registro.items()))


PORTALES: dict[str, type[PortalArca]] = _descubrir()

for _clase in PORTALES.values():
    globals()[_clase.__name__] = _clase


def portal_para(bot: str) -> type[PortalArca] | None:
    """Clase de portal registrada para un bot, o ``None`` si no hay port."""
    return PORTALES.get(str(bot or "").strip().lower())


__all__ = ["PORTALES", "PortalArca", "portal_para", *(c.__name__ for c in PORTALES.values())]
