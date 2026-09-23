"""Claim transaccional de la cola (plans/02-central-api/plan.md §6.2).

El scheduler NO mantiene el lock de base mientras hace HTTP al worker:
reclama y marca ``ASIGNADO`` en una transacción corta, y recién después
despacha por HTTP (ver ``dispatcher``). ``FOR UPDATE SKIP LOCKED`` permite
N réplicas sin doble asignación: un claim vacío por contención NO es cola
vacía (ver ``has_schedulable_work`` en el plan §6.6).
"""

from __future__ import annotations

CLAIM_NEXT_JOB_SQL = """\
WITH candidate AS (
    SELECT j.id
    FROM jobs AS j
    WHERE j.status = 'PENDIENTE'
      AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= now())
      AND j.cancel_requested_at IS NULL
    ORDER BY j.created_at ASC, j.id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
SELECT j.*
FROM jobs AS j
JOIN candidate AS c ON c.id = j.id;
"""

# Reserva de slot + marca ASIGNADO dentro de la misma transacción (§6.2).
CLAIM_AND_RESERVE_SQL = """\
WITH selected_worker AS (
    SELECT w.id
    FROM workers AS w
    WHERE w.id = :worker_id
      AND w.status = 'SANO'
      AND w.running_jobs + w.reserved_slots < w.capacity
    FOR UPDATE
), assigned AS (
    UPDATE jobs AS j
    SET status = 'ASIGNADO',
        worker_id = (SELECT id FROM selected_worker),
        assignment_token = :assignment_token,
        assignment_attempt = j.assignment_attempt + 1,
        assigned_at = now(),
        lease_expires_at = now() + make_interval(secs => :ack_lease_seconds),
        next_attempt_at = NULL
    WHERE j.id = :job_id
      AND j.status = 'PENDIENTE'
      AND EXISTS (SELECT 1 FROM selected_worker)
    RETURNING j.id
)
UPDATE workers AS w
SET reserved_slots = reserved_slots + 1,
    updated_at = now()
WHERE w.id = (SELECT id FROM selected_worker)
  AND EXISTS (SELECT 1 FROM assigned)
RETURNING w.id;
"""

REAPER_EXPIRED_SQL = """\
WITH expired AS (
    SELECT id
    FROM jobs
    WHERE status IN ('ASIGNADO', 'CORRIENDO')
      AND lease_expires_at < now()
    ORDER BY lease_expires_at ASC
    FOR UPDATE SKIP LOCKED
    LIMIT :batch_size
)
SELECT j.*
FROM jobs AS j
JOIN expired AS e ON e.id = j.id;
"""


async def reclamar_siguiente_db(
    sesion, *, worker_id, worker_app_version: str,
    lease_seconds: int = 90,
):
    """Reclama un ``PENDIENTE`` para el worker desde PostgreSQL.

    Delega en ``JobRepository.claim_next`` (``FOR UPDATE SKIP LOCKED`` con
    guardia ``EXISTS``): ``None`` exige ``ROLLBACK`` del llamante, sea cola
    vacía o contención (el scheduler debe ciclar, no dormir).
    """
    from uuid import UUID

    from central_api.repositories.jobs import JobRepository

    repo = JobRepository(sesion)
    return await repo.claim_next(
        worker_id=UUID(str(worker_id)),
        worker_app_version=worker_app_version,
        lease_seconds=lease_seconds,
    )


async def leases_vencidas_db(sesion, *, batch_size: int = 100) -> list:
    """IDs con lease vencido en PostgreSQL para el reaper central."""
    from central_api.repositories.jobs import JobRepository

    repo = JobRepository(sesion)
    return await repo.expired_leases(batch_size=batch_size)
