"""Supervisor de capacidad local: como maximo 5 jobs concurrentes (W-2).

La admision reserva bajo candado: el contador `en_ejecucion` incluye las
tareas con permiso incluso si aun preparan credenciales o esperan
Chromium. Sin lugar -> `WorkerSaturated` (HTTP 409 WORKER_SATURADO).
El `asyncio.Semaphore` rodea la vida completa del job; el `finally` del
runner libera el permiso por resultado, excepcion, timeout o cancelacion.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


class WorkerSaturated(Exception):
    """Sin capacidad local disponible."""


class WorkerDraining(Exception):
    """El worker no acepta trabajo nuevo (SIGTERM)."""


class LeaseConflict(Exception):
    """Mismo (job_id, attempt) con otra lease_id."""


@dataclass
class LocalJob:
    job_id: str
    attempt: int
    lease_id: str
    plugin: str
    operation: str
    local_state: str = "EN_COLA"
    accepted_at: str = ""
    started_at: str | None = None
    deadline_at: str | None = None
    cancel_requested: bool = False

    @property
    def key(self) -> tuple[str, int]:
        return (self.job_id, self.attempt)


@dataclass
class Acceptance:
    job: LocalJob
    duplicate: bool = False
    queue_position: int = 0


class JobSupervisor:
    HARD_MAX_CONCURRENCY = 5

    def __init__(self, configured: int = 5, queue_limit: int = 0) -> None:
        if not 1 <= configured <= self.HARD_MAX_CONCURRENCY:
            raise ValueError("WORKER_CONCURRENCY debe estar entre 1 y 5")
        if not 0 <= queue_limit <= self.HARD_MAX_CONCURRENCY:
            raise ValueError("WORKER_LOCAL_QUEUE_LIMIT debe estar entre 0 y 5")
        self.capacity = configured
        self.queue_limit = queue_limit
        # El permiso rodea la vida completa del job: como maximo
        # `configured` ejecuciones reales; el resto espera en EN_COLA.
        self._semaphore = asyncio.Semaphore(configured)
        self._admission_lock = asyncio.Lock()
        self._running: dict[tuple[str, int], LocalJob] = {}
        self._tasks: dict[tuple[str, int], asyncio.Task[None]] = {}
        self._acceptance_cache: dict[tuple[str, int, str], dict[str, Any]] = {}
        self.counters: dict[str, int] = {
            "jobs_completed": 0,
            "jobs_failed": 0,
            "jobs_cancelled": 0,
            "chromium_crashes": 0,
        }
        self.draining = False
        self.peak_in_flight = 0

    @property
    def en_ejecucion(self) -> int:
        return len(self._running)

    @property
    def en_cola(self) -> int:
        return sum(
            1 for j in self._running.values() if j.local_state == "EN_COLA"
        )

    @property
    def available_capacity(self) -> int:
        return max(0, self.capacity - len(self._running))

    async def accept(
        self, job: LocalJob, acceptance_body: dict[str, Any]
    ) -> Acceptance:
        """Admision atomica: deduplica, valida lease y reserva capacidad."""
        async with self._admission_lock:
            if self.draining:
                raise WorkerDraining()
            cached = self._acceptance_cache.get(
                (job.job_id, job.attempt, job.lease_id)
            )
            if cached is not None:
                return Acceptance(job=job, duplicate=True)
            for (jid, att), known in self._running.items():
                if jid == job.job_id and att == job.attempt:
                    raise LeaseConflict()
            if len(self._running) >= self.capacity + self.queue_limit:
                raise WorkerSaturated()
            self._running[job.key] = job
            self._acceptance_cache[(job.job_id, job.attempt, job.lease_id)] = (
                acceptance_body
            )
            self.peak_in_flight = max(self.peak_in_flight, len(self._running))
            return Acceptance(job=job)

    def duplicate_body(
        self, job_id: str, attempt: int, lease_id: str
    ) -> dict[str, Any] | None:
        return self._acceptance_cache.get((job_id, attempt, lease_id))

    def track_task(
        self, key: tuple[str, int], task: asyncio.Task[None]
    ) -> None:
        self._tasks[key] = task

    def get(self, job_id: str, attempt: int | None = None) -> LocalJob | None:
        if attempt is not None:
            return self._running.get((job_id, attempt))
        for (jid, _), job in self._running.items():
            if jid == job_id:
                return job
        return None

    def known_attempts(self, job_id: str) -> list[LocalJob]:
        return [j for (jid, _), j in self._running.items() if jid == job_id]

    def items(self) -> list[LocalJob]:
        return list(self._running.values())

    def request_cancel(self, job: LocalJob) -> None:
        """Pide cancelación cooperativa de un job (no revierte efectos)."""
        job.cancel_requested = True
        if job.local_state not in ("TERMINADO", "CANCELADO"):
            job.local_state = "CANCELANDO"

    def begin_drain(self) -> None:
        """Entra a DRENANDO: rechaza admisión nueva, conserva lo en vuelo."""
        self.draining = True

    def cancel_all(self) -> list[LocalJob]:
        """Marca todos los jobs en vuelo para cancelación cooperativa."""
        marcados = []
        for job in self._running.values():
            if job.local_state not in ("TERMINADO", "CANCELADO"):
                self.request_cancel(job)
                marcados.append(job)
        return marcados

    async def wait_empty(self, timeout_seconds: float = 120.0) -> bool:
        """Espera (sin perder jobs) a que terminen los jobs en vuelo.

        Devuelve True si la mesa quedó vacía antes del plazo; False si
        venció el plazo y aún hay jobs en vuelo (el llamador debe pedir
        cancelación cooperativa y reportar antes de salir).
        """
        import time

        inicio = time.monotonic()
        while self._running:
            if time.monotonic() - inicio >= timeout_seconds:
                return False
            await asyncio.sleep(0.2)
        return True

    def release(self, job: LocalJob, outcome: str) -> None:
        self._running.pop(job.key, None)
        task = self._tasks.pop(job.key, None)
        if (
            task is not None
            and not task.done()
            and task is not asyncio.current_task()
        ):
            task.cancel()
        if outcome == "completado":
            self.counters["jobs_completed"] += 1
        elif outcome == "cancelado":
            self.counters["jobs_cancelled"] += 1
        else:
            self.counters["jobs_failed"] += 1

    def semaphore(self) -> asyncio.Semaphore:
        return self._semaphore
