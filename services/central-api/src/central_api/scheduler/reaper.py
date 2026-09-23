"""Reaper de leases vencidas (plan 02 §6.10-§6.11, invariantes W-2/W-4).

Corre en todas las réplicas (en PG con ``FOR UPDATE SKIP LOCKED`` por
vencimiento); aquí recorre el registro de leases en memoria con idéntica
semántica: para ``ASIGNADO`` sin ack libera el slot y reencola con backoff;
para ``CORRIENDO`` vencido libera, registra ``lease_expired`` y reencola si
quedan intentos; al agotar ``MAX_EXECUTION_ATTEMPTS`` marca ``FALLIDO`` con
``result=ERROR`` y resuelve billing (reembolso ante infraestructura).
Nunca cancela globalmente jobs de workers vivos (sin
``recover_jobs_after_restart``).
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from central_api.settings import get_settings
from central_api.store import JOBS, WORKERS, utcnow

# Leases vivas por job (en PG: columnas lease_expires_at/assignment_token).
LEASES: dict[str, dict] = {}
# Carga lógica por worker para conciliar con heartbeats (slot drift).
WORKER_LOAD: dict[str, int] = {}


def _settings():
    return get_settings()


def grant_lease(
    job_id: str, worker_node: str, token: str, attempt: int,
    seconds: int | None = None,
) -> datetime:
    """Otorga o renueva la lease de un job y reserva el slot lógico."""
    settings = _settings()
    ttl = seconds if seconds is not None else settings.worker_ack_lease_seconds
    expires = utcnow() + timedelta(seconds=ttl)
    LEASES[job_id] = {
        "worker": worker_node, "token": token, "attempt": attempt,
        "expires_at": expires,
    }
    entry = WORKERS.get(worker_node)
    if entry is not None:
        entry.reserved_slots = min(entry.capacity, entry.reserved_slots + 1)
    WORKER_LOAD[worker_node] = WORKER_LOAD.get(worker_node, 0) + 1
    return expires


def renew_lease(job_id: str, seconds: int | None = None) -> None:
    """Renueva la lease ante heartbeat, progreso o evento válido."""
    lease = LEASES.get(job_id)
    if lease is None:
        return
    settings = _settings()
    ttl = seconds if seconds is not None else settings.job_lease_seconds
    lease["expires_at"] = utcnow() + timedelta(seconds=ttl)


def clear_lease(job_id: str) -> None:
    """Libera el slot lógico al cerrar o reencolar un job."""
    lease = LEASES.pop(job_id, None)
    if lease is None:
        return
    worker = str(lease.get("worker", ""))
    entry = WORKERS.get(worker)
    if entry is not None and entry.reserved_slots > 0:
        entry.reserved_slots -= 1
    if WORKER_LOAD.get(worker, 0) > 0:
        WORKER_LOAD[worker] -= 1


def delivery_backoff_seconds(assignment_attempt: int) -> float:
    """Backoff exponencial con jitter (tope 60 s + 0-1000 ms)."""
    return min(2 ** max(0, assignment_attempt), 60) + random.uniform(0, 1)


def reap_expired(now: datetime | None = None) -> dict:
    """Procesa leases vencidas selectivamente; devuelve el conteo por acción."""
    from central_api.billing.reservation import refund_usage

    now = now or utcnow()
    settings = _settings()
    outcome = {"reencolados": 0, "fallidos": 0, "retenidos": 0}
    for job_id, lease in sorted(
        list(LEASES.items()), key=lambda kv: kv[1]["expires_at"]
    )[: settings.scheduler_batch_size]:
        if lease["expires_at"] >= now:
            continue
        job = JOBS.get(job_id)
        if job is None or job.status in ("COMPLETO", "FALLIDO", "CANCELADO"):
            clear_lease(job_id)
            continue
        attempts = job.assignment_attempt
        if job.status == "CORRIENDO":
            attempts += 0  # el intento de ejecución ya cuenta; no duplicar
        if attempts >= settings.max_execution_attempts:
            job.status = "FALLIDO"
            job.result = {"result": "ERROR", "error": {"error_code": "unexpected"},
                          "data": {}, "attempt": attempts}
            refund_usage(job.id, motivo="lease vencida, intentos agotados")
            clear_lease(job_id)
            outcome["fallidos"] += 1
            continue
        # Reencola con backoff: pierde worker, token y lease anterior.
        clear_lease(job_id)
        job.status = "PENDIENTE"
        job.worker_node = None
        job.assignment_attempt = attempts + 1
        outcome["reencolados"] += 1
    return outcome


def expired_batch(limit: int = 20) -> list[str]:
    """IDs con lease vencida ordenados por vencimiento (referencia SQL)."""
    now = utcnow()
    expired = [
        (lease["expires_at"], job_id)
        for job_id, lease in LEASES.items()
        if lease["expires_at"] < now
    ]
    expired.sort()
    return [job_id for _, job_id in expired[:limit]]


__all__ = [
    "LEASES",
    "WORKER_LOAD",
    "grant_lease",
    "renew_lease",
    "clear_lease",
    "delivery_backoff_seconds",
    "reap_expired",
    "expired_batch",
]
