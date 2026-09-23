"""Selección de worker sano por IP configurada (plan §6.5, versión esqueleto).

Reglas del esqueleto:
- Solo participan nodos presentes en ``WORKER_NODES`` (``"ip:port"``).
- Solo workers con estado ``SANO`` y latido fresco (umbral configurable).
- Solo workers con capacidad libre (``running + reserved < capacity``).
- Least-loaded normalizado por capacidad; desempate por carga absoluta y
  hash estable de ``job_id + node`` (evita favorecer siempre el primer nodo).
La salud viva se confirma por HTTP contra el worker (ver ``dispatcher``);
este selector decide con el estado guardado, que SOLO contiene la IP.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

from central_api.store import Job, WorkerEntry, utcnow


def stable_hash(job_id: str, node: str) -> int:
    digest = hashlib.sha256(f"{job_id}+{node}".encode("utf-8")).hexdigest()
    return int(digest[:16], 16)


def is_fresh(entry: WorkerEntry, now=None, degraded_after_seconds: int = 15) -> bool:
    now = now or utcnow()
    if entry.last_heartbeat_at is None:
        return False
    return entry.last_heartbeat_at >= now - timedelta(seconds=degraded_after_seconds)


def select_worker(
    job: Job,
    workers: list[WorkerEntry],
    allowed_nodes: list[str] | None = None,
    degraded_after_seconds: int = 15,
) -> WorkerEntry | None:
    now = utcnow()
    candidates = [
        w
        for w in workers
        if (allowed_nodes is None or w.node in allowed_nodes)
        and w.status == "SANO"
        and w.running_jobs + w.reserved_slots < w.capacity
        and w.last_heartbeat_at is not None
        and w.last_heartbeat_at >= now - timedelta(seconds=degraded_after_seconds)
        and (not job.bot or not w.capabilities or f"{job.bot}/{job.operation}" in w.capabilities)
    ]
    if not candidates:
        return None

    def score(w: WorkerEntry) -> tuple[float, int, int]:
        load = w.running_jobs + w.reserved_slots
        return (load / w.capacity, load, stable_hash(job.id, w.node))

    return min(candidates, key=score)
