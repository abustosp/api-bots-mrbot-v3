"""Plugins de bots del worker (plan 03-worker S7, plan 07 F3).

Cada bot implementa ``BotPlugin`` (manifiesto + ``validate`` +
``execute``) y recibe solo ``BotRuntime``: sin base de datos (W-1), sin
entorno, escribiendo unicamente bajo ``runtime.work_dir`` y sin loguear
credenciales. El error se expresa como categoria + diagnostico.

Los simbolos del registro se exponen de forma **diferida** (PEP 562): el
registro importa todos los plugins y los plugins importan la capa de portales,
que a su vez necesita ``bot_worker.bots.errors``. Importar el registro desde
``__init__`` cerraba ese ciclo (``portals.base`` -> ``bots`` -> ``registry`` ->
plugins -> ``portals``) y rompia cualquier import que empezara por
``bot_worker.runtime.portals``. Con ``__getattr__`` el ciclo desaparece y
``from bot_worker.bots import REGISTRY`` sigue funcionando igual.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

__all__ = [
    "PROTOCOL_VERSION",
    "REGISTRY",
    "ArtifactSpec",
    "BotManifest",
    "BotPlugin",
    "get_plugin",
    "list_manifests",
    "manifest_hash",
]


def _registry() -> ModuleType:
    return importlib.import_module("bot_worker.bots.registry")


def __getattr__(name: str) -> Any:
    if name in __all__:
        return getattr(_registry(), name)
    if name == "registry":
        return _registry()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__, "registry"})
