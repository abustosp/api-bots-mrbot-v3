"""bot_builder: helpers minimos para crear bots Playwright + ARCA.

Convenciones tomadas de AGENTS.md y del patron real del repositorio:
- dos variantes por bot (logger / clean) generadas desde un mismo flujo,
- evidencias en `./temp/{bot}_{ts}/{html,screenshots,descargas,har,logs}`,
- HAR embebido desde la creacion del context, flush al cerrar.
"""
from .debug import DebugCapture, NullDebug
from .steps import step
from .filenames import build_filename
from .env import load_env, get_env

__all__ = [
    "run_bot",
    "BotConfig",
    "DebugCapture",
    "NullDebug",
    "step",
    "build_filename",
    "load_env",
    "get_env",
]


def __getattr__(name: str):  # lazy import: requiere playwright
    if name in ("run_bot", "BotConfig"):
        from . import runner
        return getattr(runner, name)
    raise AttributeError(name)
