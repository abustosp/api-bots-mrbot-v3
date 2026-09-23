"""Plugins de bots del worker (plan 03-worker S7, plan 07 F3).

Cada bot implementa ``BotPlugin`` (manifiesto + ``validate`` +
``execute``) y recibe solo ``BotRuntime``: sin base de datos (W-1), sin
entorno, escribiendo unicamente bajo ``runtime.work_dir`` y sin loguear
credenciales. El error se expresa como categoria + diagnostico.
"""

from bot_worker.bots.registry import (
    PROTOCOL_VERSION,
    REGISTRY,
    ArtifactSpec,
    BotManifest,
    BotPlugin,
    get_plugin,
    list_manifests,
    manifest_hash,
)

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
