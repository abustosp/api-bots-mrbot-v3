"""MrBot bot-worker: ejecutor remoto efimero, sin estado persistente (plano de datos).

Toda la configuracion llega por variables de entorno (ver config.py).
Prohibido en este servicio: DSN/drivers/ORM de base de datos, clave
privada RSA, claves de bucket, SMTP, MercadoPago y credenciales admin.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # solo tipado; import perezoso en runtime
    from bot_worker.config import WorkerSettings, load_settings

__all__ = ["WorkerSettings", "load_settings"]


def __getattr__(name: str):  # import perezoso: evita pydantic al importar
    if name in __all__:
        from bot_worker import config as _config

        return getattr(_config, name)
    raise AttributeError(name)
