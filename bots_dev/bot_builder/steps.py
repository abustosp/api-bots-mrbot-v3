"""Decorador `step` para instrumentar funciones del flujo sin ruido.

Uso:
    async def mi_paso(ctx, page):
        ...

    # dentro de run_flow:
    await mi_paso(ctx, page)
"""
from __future__ import annotations

import functools
import logging
import time
from typing import Any, Awaitable, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])

_log = logging.getLogger("bot_builder.steps")


def step(name: str | None = None) -> Callable[[F], F]:
    """Loggea inicio/fin y mide duracion. Re-raise con contexto."""
    def deco(fn: F) -> F:
        label = name or fn.__name__

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            t0 = time.monotonic()
            _log.info("→ %s", label)
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                _log.exception("✗ %s: %s", label, exc)
                raise
            _log.info("✓ %s (%.2fs)", label, time.monotonic() - t0)
            return result

        return wrapper  # type: ignore[return-value]

    return deco
