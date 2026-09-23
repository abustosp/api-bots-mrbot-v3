"""Circuit breaker por worker (plan 02 §6.9).

Ventana móvil de 60 s: tres fallos consecutivos de entrega o >20% en cinco
intentos marcan ``DEGRADADO`` (sin jobs nuevos, termina los activos); cinco
fallos en 120 s marcan ``CAIDO`` hasta heartbeat sano y revisión. Semiabierto
a los 30 s con un único probe; dos heartbeats sanos + un dispatch exitoso
devuelven ``SANO``. Un heartbeat sano no borra el historial al instante.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

from central_api.store import WORKERS, utcnow

WINDOW_SECONDS = 60
CAIDO_WINDOW_SECONDS = 120
DEGRADE_CONSECUTIVE = 3
CAIDO_TOTAL = 5
HALF_OPEN_SECONDS = 30

FAILURES: dict[str, deque[datetime]] = {}
CONSECUTIVE: dict[str, int] = {}
SUCCESS_STREAK: dict[str, int] = {}
HALF_OPEN_AT: dict[str, datetime] = {}


def _prune(node: str, now: datetime, window: int) -> deque[datetime]:
    marks = FAILURES.setdefault(node, deque())
    while marks and marks[0] < now - timedelta(seconds=window):
        marks.popleft()
    return marks


def record_success(node: str) -> None:
    """Éxito de entrega: corta la racha de fallos y suma rehabilitación."""
    CONSECUTIVE[node] = 0
    SUCCESS_STREAK[node] = SUCCESS_STREAK.get(node, 0) + 1
    entry = WORKERS.get(node)
    if entry is not None and entry.status == "DEGRADADO":
        if SUCCESS_STREAK[node] >= 1 and node in HALF_OPEN_AT:
            entry.status = "SANO"
            HALF_OPEN_AT.pop(node, None)


def record_failure(node: str, now: datetime | None = None) -> str:
    """Registra un fallo de entrega y aplica transiciones; devuelve estado."""
    now = now or utcnow()
    marks = _prune(node, now, WINDOW_SECONDS)
    marks.append(now)
    CONSECUTIVE[node] = CONSECUTIVE.get(node, 0) + 1
    SUCCESS_STREAK[node] = 0
    entry = WORKERS.get(node)
    recent = list(_prune(node, now, CAIDO_WINDOW_SECONDS))
    if entry is None:
        return "DESCONOCIDO"
    if len(recent) >= CAIDO_TOTAL:
        entry.status = "CAIDO"
    elif CONSECUTIVE[node] >= DEGRADE_CONSECUTIVE or (
        len(marks) >= 5 and len(marks) / 5 > 0.2 and len(marks) >= 3
    ):
        if entry.status == "SANO":
            entry.status = "DEGRADADO"
            HALF_OPEN_AT[node] = now + timedelta(seconds=HALF_OPEN_SECONDS)
    return entry.status


def record_heartbeat(node: str, healthy: bool) -> None:
    """Heartbeat sano suma rehabilitación sin borrar el historial de golpe."""
    if not healthy:
        return
    SUCCESS_STREAK[node] = SUCCESS_STREAK.get(node, 0) + 1
    entry = WORKERS.get(node)
    if (
        entry is not None
        and entry.status == "DEGRADADO"
        and SUCCESS_STREAK.get(node, 0) >= 2
        and HALF_OPEN_AT.get(node, utcnow()) <= utcnow()
    ):
        entry.status = "SANO"
        HALF_OPEN_AT.pop(node, None)


def probe_allowed(node: str, now: datetime | None = None) -> bool:
    """Un único probe controlado cuando el circuito está semiabierto."""
    now = now or utcnow()
    ready_at = HALF_OPEN_AT.get(node)
    if ready_at is None:
        return True
    if now >= ready_at:
        HALF_OPEN_AT[node] = now + timedelta(seconds=HALF_OPEN_SECONDS)
        return True
    return False


__all__ = [
    "WINDOW_SECONDS",
    "CAIDO_WINDOW_SECONDS",
    "record_success",
    "record_failure",
    "record_heartbeat",
    "probe_allowed",
]
