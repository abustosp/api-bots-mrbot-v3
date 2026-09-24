"""Repositorio de jobs: admision idempotente y claim (plan 01 §11.1).

El claim usa ``FOR UPDATE SKIP LOCKED`` con la guardia ``EXISTS`` sobre la
CTE de la que depende: sin ella, la CTE que modifica ``workers`` se ejecuta
aunque la cola este vacia y fuga un slot por ciclo (§11.1.1). ``RETURNING``
vacio es ``ROLLBACK``, nunca exito parcial.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from central_api.models.base import PROTOCOL_VERSION, new_uuid7
from central_api.models.execution import Job
from central_api.repositories.base import (
    NotFoundError,
    RepositoryError,
    assert_no_secretos,
)

#: Reclamo atomico job + slot de worker sano (una sola sentencia).
CLAIM_SQL = text(
    """
WITH next_job AS (
    SELECT id
    FROM jobs
    WHERE status = 'PENDIENTE'
    ORDER BY priority ASC, created_at ASC, id ASC
    FOR UPDATE SKIP LOCKED
    LIMIT 1
), reserved_worker AS (
    UPDATE workers
    SET running_jobs = running_jobs + 1
    WHERE id = :worker_id
      AND status = 'SANO'
      AND last_heartbeat_at >= CURRENT_TIMESTAMP - INTERVAL '30 seconds'
      AND running_jobs < capacity
      AND EXISTS (SELECT 1 FROM next_job)
    RETURNING id
)
UPDATE jobs j
SET status = 'ASIGNADO',
    worker_id = rw.id,
    assigned_at = CURRENT_TIMESTAMP,
    lease_expires_at = CURRENT_TIMESTAMP + make_interval(secs => :lease_seconds),
    attempts = j.attempts + 1,
    app_version = :worker_app_version,
    protocol_version = :protocol_version
FROM next_job nj
JOIN reserved_worker rw ON true
WHERE j.id = nj.id
RETURNING j.id AS job_id
"""
)

#: Vencidos para el reaper central (lease por intento, sin tocar sanos).
REAP_EXPIRED_SQL = text(
    """
SELECT id AS job_id
FROM jobs
WHERE status IN ('ASIGNADO', 'CORRIENDO')
  AND lease_expires_at < CURRENT_TIMESTAMP
ORDER BY lease_expires_at ASC
FOR UPDATE SKIP LOCKED
LIMIT :batch_size
"""
)


class JobRepository:
    """Persistencia de la agregacion ``jobs`` con ambito por usuario."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: UUID,
        bot: str,
        operation: str,
        request_payload: Mapping[str, Any],
        credential_ciphertext: str | None = None,
        idempotency_key: str | None = None,
        priority: int = 100,
        max_attempts: int = 3,
    ) -> Job:
        """Crea un job ``PENDIENTE``; con clave repetida retorna el original."""
        assert_no_secretos(request_payload, "request_payload")
        if idempotency_key:
            existing = await self.get_by_idempotency(
                user_id, bot, operation, idempotency_key
            )
            if existing is not None:
                return existing
        job = Job(
            id=new_uuid7(),
            user_id=user_id,
            bot=bot,
            operation=operation,
            status="PENDIENTE",
            request_payload=dict(request_payload),
            credential_ciphertext=credential_ciphertext,
            idempotency_key=idempotency_key,
            priority=priority,
            max_attempts=max_attempts,
            protocol_version=PROTOCOL_VERSION,
        )
        self._session.add(job)
        try:
            await self._session.flush()
        except Exception as exc:
            raise RepositoryError(f"no se pudo crear el job: {type(exc).__name__}") from exc
        return job

    async def get_by_idempotency(
        self, user_id: UUID, bot: str, operation: str, idempotency_key: str
    ) -> Job | None:
        """Busca por la unicidad parcial ``(usuario, bot, operacion, clave)``."""
        rows = (
            await self._session.execute(
                text(
                    "SELECT id FROM jobs WHERE user_id = :user_id AND bot = :bot"
                    " AND operation = :operation AND idempotency_key = :key"
                ),
                {
                    "user_id": str(user_id),
                    "bot": bot,
                    "operation": operation,
                    "key": idempotency_key,
                },
            )
        ).first()
        if rows is None:
            return None
        return await self._session.get(Job, rows[0])

    async def get_scoped(self, job_id: UUID, user_id: UUID) -> Job:
        """Retorna el job solo si pertenece al usuario (frontera de auth)."""
        job = await self._session.get(Job, job_id)
        if job is None or UUID(str(job.user_id)) != user_id:
            raise NotFoundError("job no encontrado en el ambito del usuario")
        return job

    async def claim_next(
        self,
        *,
        worker_id: UUID,
        worker_app_version: str,
        lease_seconds: int = 90,
    ) -> UUID | None:
        """Reclama un ``PENDIENTE`` para el worker o retorna ``None``.

        ``None`` exige ``ROLLBACK`` del llamante: puede ser cola vacia o
        contencion de ``SKIP LOCKED`` (el scheduler debe ciclar, no dormir).
        """
        rows = (
            await self._session.execute(
                CLAIM_SQL,
                {
                    "worker_id": str(worker_id),
                    "worker_app_version": worker_app_version,
                    "protocol_version": PROTOCOL_VERSION,
                    "lease_seconds": lease_seconds,
                },
            )
        ).first()
        if rows is None:
            return None
        return UUID(str(rows[0]))

    async def transition(
        self, job_id: UUID, *, expect: tuple[str, ...], **changes: Any
    ) -> Job:
        """Aplica ``UPDATE ... WHERE status IN (...)`` (transicion legal)."""
        job = await self._session.get(Job, job_id)
        if job is None:
            raise NotFoundError("job no encontrado")
        if str(job.status) not in expect:
            raise RepositoryError(
                f"transicion ilegal desde {job.status}: se esperaba {expect}"
            )
        for field, value in changes.items():
            setattr(job, field, value)
        await self._session.flush()
        return job

    async def expired_leases(self, *, batch_size: int = 100) -> list[UUID]:
        """Lista jobs con lease vencido para recuperacion condicionada."""
        rows = (
            await self._session.execute(
                REAP_EXPIRED_SQL, {"batch_size": batch_size}
            )
        ).all()
        return [UUID(str(r[0])) for r in rows]


__all__ = ["CLAIM_SQL", "REAP_EXPIRED_SQL", "JobRepository"]
