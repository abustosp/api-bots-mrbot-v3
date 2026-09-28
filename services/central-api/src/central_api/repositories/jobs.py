"""Repositorio de jobs: admision idempotente y claim (plan 01 §11.1).

El claim usa ``FOR UPDATE SKIP LOCKED`` con la guardia ``EXISTS`` sobre la
CTE de la que depende: sin ella, la CTE que modifica ``workers`` se ejecuta
aunque la cola este vacia y fuga un slot por ciclo (§11.1.1). ``RETURNING``
vacio es ``ROLLBACK``, nunca exito parcial.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from central_api.models.base import PROTOCOL_VERSION, new_uuid7
from central_api.models.execution import Job, JobArtifact, JobEvent, JobResult
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

ASSIGN_JOB_SQL = text(
    """
WITH eligible_job AS (
    SELECT id
    FROM jobs
    WHERE id = :job_id AND status = 'PENDIENTE'
    FOR UPDATE
), reserved_worker AS (
    UPDATE workers
    SET running_jobs = running_jobs + 1
    WHERE id = :worker_id
      AND status = 'SANO'
      AND last_heartbeat_at >= CURRENT_TIMESTAMP - INTERVAL '30 seconds'
      AND running_jobs < capacity
      AND EXISTS (SELECT 1 FROM eligible_job)
    RETURNING id, app_version
)
UPDATE jobs AS j
SET status = 'ASIGNADO',
    worker_id = rw.id,
    assigned_at = CURRENT_TIMESTAMP,
    lease_expires_at = CURRENT_TIMESTAMP + make_interval(secs => :lease_seconds),
    attempts = j.attempts + 1,
    app_version = rw.app_version
FROM eligible_job AS ej
JOIN reserved_worker AS rw ON true
WHERE j.id = ej.id
RETURNING j.attempts
"""
)

#: Variante del claim para ejecución forzada desde el panel: exige worker
#: SANO con latido fresco y job PENDIENTE, pero omite el tope
#: ``running_jobs < capacity``. Sigue sumando el slot (la superación queda
#: visible en la flota) y es la única vía que puede exceder el cupo.
ASSIGN_JOB_FORCE_SQL = text(
    """
WITH eligible_job AS (
    SELECT id
    FROM jobs
    WHERE id = :job_id AND status = 'PENDIENTE'
    FOR UPDATE
), reserved_worker AS (
    UPDATE workers
    SET running_jobs = running_jobs + 1
    WHERE id = :worker_id
      AND status = 'SANO'
      AND last_heartbeat_at >= CURRENT_TIMESTAMP - INTERVAL '30 seconds'
      AND EXISTS (SELECT 1 FROM eligible_job)
    RETURNING id, app_version
)
UPDATE jobs AS j
SET status = 'ASIGNADO',
    worker_id = rw.id,
    assigned_at = CURRENT_TIMESTAMP,
    lease_expires_at = CURRENT_TIMESTAMP + make_interval(secs => :lease_seconds),
    attempts = j.attempts + 1,
    app_version = rw.app_version
FROM eligible_job AS ej
JOIN reserved_worker AS rw ON true
WHERE j.id = ej.id
RETURNING j.attempts
"""
)

RELEASE_ASSIGNMENT_SQL = text(
    """
WITH released AS (
    UPDATE jobs
    SET status = 'PENDIENTE',
        worker_id = NULL,
        assigned_at = NULL,
        lease_expires_at = NULL
    WHERE id = :job_id
      AND worker_id = :worker_id
      AND status = 'ASIGNADO'
    RETURNING id
)
UPDATE workers
SET running_jobs = GREATEST(0, running_jobs - 1)
WHERE id = :worker_id
  AND EXISTS (SELECT 1 FROM released)
"""
)


def _sin_nul(valor: Any) -> Any:
    """Quita U+0000 de claves y textos anidados (JSONB lo rechaza)."""
    if isinstance(valor, str):
        return valor.replace("\x00", "")
    if isinstance(valor, dict):
        return {_sin_nul(k): _sin_nul(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_sin_nul(v) for v in valor]
    return valor


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
        credential_metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        priority: int = 100,
        max_attempts: int = 3,
    ) -> Job:
        """Crea un job ``PENDIENTE``; con clave repetida retorna el original."""
        job, _created = await self.create_idempotently(
            user_id=user_id,
            bot=bot,
            operation=operation,
            request_payload=request_payload,
            credential_ciphertext=credential_ciphertext,
            credential_metadata=credential_metadata,
            idempotency_key=idempotency_key,
            priority=priority,
            max_attempts=max_attempts,
        )
        return job

    async def create_idempotently(
        self,
        *,
        user_id: UUID,
        bot: str,
        operation: str,
        request_payload: Mapping[str, Any],
        credential_ciphertext: str | None = None,
        credential_metadata: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        job_id: UUID | None = None,
        priority: int = 100,
        max_attempts: int = 3,
    ) -> tuple[Job, bool]:
        """Inserta el job y dispara su proyección en la misma transacción.

        La unicidad parcial de PostgreSQL arbitra reintentos concurrentes. Si
        otra petición ya insertó la misma clave, retorna su fila y ``False``;
        un uso de clave con otro payload es conflicto, no una nueva admisión.
        """
        assert_no_secretos(request_payload, "request_payload")
        if idempotency_key:
            existing = await self.get_by_idempotency(
                user_id, bot, operation, idempotency_key
            )
            if existing is not None:
                self._assert_same_request(existing, request_payload, credential_metadata)
                return existing, False
        job = Job(
            id=job_id or new_uuid7(),
            user_id=user_id,
            bot=bot,
            operation=operation,
            status="PENDIENTE",
            request_payload=dict(request_payload),
            credential_ciphertext=credential_ciphertext,
            credential_metadata=dict(credential_metadata or {}),
            idempotency_key=idempotency_key,
            priority=priority,
            max_attempts=max_attempts,
            protocol_version=PROTOCOL_VERSION,
        )
        try:
            # La savepoint permite recuperar una colisión de idempotencia sin
            # dejar la transacción exterior en estado fallido.
            async with self._session.begin_nested():
                self._session.add(job)
                await self._session.flush()
        except IntegrityError as exc:
            if idempotency_key:
                existing = await self.get_by_idempotency(
                    user_id, bot, operation, idempotency_key
                )
                if existing is not None:
                    self._assert_same_request(
                        existing, request_payload, credential_metadata
                    )
                    return existing, False
            raise RepositoryError(
                f"no se pudo crear el job: {type(exc).__name__}"
            ) from exc
        except Exception as exc:
            raise RepositoryError(f"no se pudo crear el job: {type(exc).__name__}") from exc
        return job, True

    @staticmethod
    def _assert_same_request(
        job: Job,
        request_payload: Mapping[str, Any],
        credential_metadata: Mapping[str, Any] | None,
    ) -> None:
        if dict(job.request_payload or {}) != dict(request_payload):
            raise IdempotencyConflict("clave de idempotencia reutilizada con otro request")
        if dict(job.credential_metadata or {}) != dict(credential_metadata or {}):
            raise IdempotencyConflict("clave de idempotencia reutilizada con otro contexto")

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

    async def assign_for_dispatch(
        self,
        *,
        job_id: UUID,
        worker_id: UUID,
        lease_seconds: int = 90,
    ) -> int | None:
        """Graba worker_id y attempts de la asignación antes del HTTP dispatch."""
        row = (
            await self._session.execute(
                ASSIGN_JOB_SQL,
                {
                    "job_id": str(job_id),
                    "worker_id": str(worker_id),
                    "lease_seconds": lease_seconds,
                },
            )
        ).first()
        return int(row[0]) if row is not None else None

    async def assign_for_dispatch_force(
        self,
        *,
        job_id: UUID,
        worker_id: UUID,
        lease_seconds: int = 90,
    ) -> int | None:
        """Claim forzado del panel: omite el tope de capacidad del worker.

        Única vía que puede superar el cupo (ver ``ASSIGN_JOB_FORCE_SQL``).
        """
        row = (
            await self._session.execute(
                ASSIGN_JOB_FORCE_SQL,
                {
                    "job_id": str(job_id),
                    "worker_id": str(worker_id),
                    "lease_seconds": lease_seconds,
                },
            )
        ).first()
        return int(row[0]) if row is not None else None

    async def release_assignment(self, *, job_id: UUID, worker_id: UUID) -> None:
        """Libera asignación rechazada y el slot SQL en una única sentencia."""
        await self._session.execute(
            RELEASE_ASSIGNMENT_SQL,
            {"job_id": str(job_id), "worker_id": str(worker_id)},
        )

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

    async def persist_result(
        self,
        job_id: UUID,
        *,
        attempt: int,
        result: str,
        payload: Mapping[str, Any],
        summary: Mapping[str, Any] | None = None,
        artifacts: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]] = (),
    ) -> tuple[Job, bool]:
        """Persiste estado, resultado y metadatos en una única transacción.

        El row lock serializa callbacks concurrentes. Los reintentos idénticos
        son no-op; un segundo resultado divergente para el mismo job produce
        ``IdempotencyConflict``. Los triggers de tablas por bot corren dentro
        de esta misma transacción al actualizar ``jobs``, ``job_results`` y
        ``job_artifacts``.
        """
        if attempt < 1:
            raise RepositoryError("assignment_attempt debe ser mayor que cero")
        # JSONB no admite U+0000: un bot que devuelva bytes binarios en su
        # JSON dejaría el callback en 503 perpetuo y el job colgado.
        payload = _sin_nul(dict(payload))
        assert_no_secretos(payload, "job_results.payload")
        summary_value = _sin_nul(dict(summary or {}))
        assert_no_secretos(summary_value, "job_results.summary")
        prepared_artifacts = [self._prepare_artifact(item) for item in artifacts]
        expected_prefix = f"jobs/{job_id}/{attempt}/"
        if any(
            not item["object_key"].startswith(expected_prefix)
            for item in prepared_artifacts
        ):
            raise InvalidArtifactError("artefacto fuera del job/intento asignado")
        signatures = [self._artifact_signature(item) for item in prepared_artifacts]
        if len(set(signatures)) != len(signatures):
            raise RepositoryError("artefactos duplicados en el resultado")

        job = (
            await self._session.execute(
                select(Job).where(Job.id == job_id).with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            raise NotFoundError("job no encontrado")

        previous = (
            await self._session.execute(
                select(JobResult).where(JobResult.job_id == job_id).with_for_update()
            )
        ).scalar_one_or_none()
        if previous is not None:
            previous_artifacts = (
                await self._session.execute(
                    select(JobArtifact)
                    .where(JobArtifact.job_id == job_id)
                    .order_by(JobArtifact.object_key)
                )
            ).scalars().all()
            previous_signatures = sorted(
                (
                    self._artifact_signature(
                        {
                            "kind": item.kind,
                            "object_key": item.object_key,
                            "filename": item.filename,
                            "content_type": item.content_type,
                            "size_bytes": item.size_bytes,
                            "sha256": item.sha256,
                        }
                    )
                    for item in previous_artifacts
                ),
                key=repr,
            )
            if (
                int(previous.attempt) == attempt
                and str(previous.result) == ("ERROR" if result == "CANCELADO" else result)
                and dict(previous.payload or {}) == dict(payload)
                and dict(previous.summary or {}) == summary_value
                and previous_signatures == sorted(signatures, key=repr)
            ):
                return job, False
            raise IdempotencyConflict("resultado duplicado divergente")

        cancellation_already_recorded = (
            job.status == "CANCELADO" and result == "CANCELADO"
        )
        if job.status not in ("ASIGNADO", "CORRIENDO") and not cancellation_already_recorded:
            raise StaleAssignmentError("job ya terminal")
        if int(job.attempts or 0) != attempt:
            raise StaleAssignmentError("resultado de una asignación obsoleta")
        if result not in ("OK", "PARCIAL", "ERROR", "CANCELADO"):
            raise RepositoryError("resultado terminal inválido")

        now = datetime.now(timezone.utc)
        if not cancellation_already_recorded:
            if result in ("OK", "PARCIAL"):
                job.status = "COMPLETO"
                job.result = result
            elif result == "CANCELADO":
                # El shape de jobs exige result NULL en cancelaciones terminales.
                job.status = "CANCELADO"
                job.result = None
                job.cancelled_by = job.cancelled_by or "SYSTEM"
            else:
                job.status = "FALLIDO"
                job.result = "ERROR"
            job.finished_at = now
            if job.worker_id is not None:
                await self._session.execute(
                    text(
                        "UPDATE workers SET running_jobs = GREATEST(0, running_jobs - 1)"
                        " WHERE id = :worker_id"
                    ),
                    {"worker_id": str(job.worker_id)},
                )

        self._session.add(
            JobResult(
                job_id=job_id,
                attempt=attempt,
                result=result if result != "CANCELADO" else "ERROR",
                payload=dict(payload),
                summary=summary_value,
            )
        )
        for item in prepared_artifacts:
            self._session.add(JobArtifact(id=new_uuid7(), job_id=job_id, **item))
        try:
            await self._session.flush()
        except Exception as exc:
            raise PersistenceError(
                f"no se pudo persistir resultado: {type(exc).__name__}"
            ) from exc
        return job, True

    async def persist_event(
        self,
        job_id: UUID,
        *,
        attempt: int,
        event_key: str,
        event_type: str,
        message: str = "",
    ) -> tuple[Job, bool]:
        """Aplica un callback de estado y su evento en una sola transacción."""
        event_types = {
            "started": "INICIADO",
            "progress": "PROGRESO",
            "heartbeat_hint": "LEASE_RENOVADO",
            "warning": "PROGRESO",
            "cancelled": "CANCELADO",
            "failed_prestart": "REINTENTO",
        }
        persisted_type = event_types.get(event_type)
        if persisted_type is None or not event_key or attempt < 1:
            raise RepositoryError("evento de job inválido")
        # El texto libre del worker puede incluir credenciales por accidente.
        # El evento conserva tipo/clave/intento; no se guarda mensaje crudo.
        payload: dict[str, Any] = {}

        job = (
            await self._session.execute(
                select(Job).where(Job.id == job_id).with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:
            raise NotFoundError("job no encontrado")
        previous = (
            await self._session.execute(
                select(JobEvent).where(
                    JobEvent.job_id == job_id,
                    JobEvent.attempt == attempt,
                    JobEvent.event_key == event_key,
                ).with_for_update()
            )
        ).scalar_one_or_none()
        if previous is not None:
            if (
                previous.event_type == persisted_type
                and dict(previous.payload or {}) in ({}, payload)
            ):
                return job, False
            raise IdempotencyConflict("evento duplicado divergente")
        if int(job.attempts or 0) != attempt:
            raise StaleAssignmentError("evento de una asignación obsoleta")
        if job.status not in ("ASIGNADO", "CORRIENDO"):
            raise StaleAssignmentError("evento fuera de una asignación activa")

        now = datetime.now(timezone.utc)
        released_worker = False
        if event_type == "started" and job.status == "ASIGNADO":
            job.status = "CORRIENDO"
            job.started_at = job.started_at or now
        elif event_type == "cancelled" and job.status in ("ASIGNADO", "CORRIENDO"):
            job.status = "CANCELADO"
            job.result = None
            job.finished_at = now
            job.cancelled_by = job.cancelled_by or "SYSTEM"
            job.cancel_reason = job.cancel_reason or "cancelado por worker"
            job.lease_expires_at = None
            released_worker = job.worker_id is not None
        elif event_type == "failed_prestart" and job.status == "ASIGNADO":
            job.status = "PENDIENTE"
            # Se conserva temporalmente el dueño para autenticar el replay
            # idempotente del mismo callback. La próxima asignación sustituye
            # worker_id; los resultados no se aceptan mientras status=PENDIENTE.
            job.assigned_at = None
            job.lease_expires_at = None
            released_worker = job.worker_id is not None

        if released_worker:
            await self._session.execute(
                text(
                    "UPDATE workers SET running_jobs = GREATEST(0, running_jobs - 1)"
                    " WHERE id = :worker_id"
                ),
                {"worker_id": str(job.worker_id)},
            )

        self._session.add(
            JobEvent(
                id=new_uuid7(),
                job_id=job_id,
                attempt=attempt,
                event_type=persisted_type,
                event_key=event_key[:128],
                payload=payload,
            )
        )
        try:
            await self._session.flush()
        except Exception as exc:
            raise PersistenceError(
                f"no se pudo persistir evento: {type(exc).__name__}"
            ) from exc
        return job, True

    @staticmethod
    def _prepare_artifact(artifact: Mapping[str, Any]) -> dict[str, Any]:
        assert_no_secretos(artifact, "job_artifacts")
        object_key = str(artifact.get("object_key") or "").strip()
        filename = str(artifact.get("filename") or artifact.get("name") or "").strip()
        content_type = str(artifact.get("content_type") or "").strip() or None
        kind = str(artifact.get("kind") or "ARCHIVO").upper()
        size_bytes = artifact.get("size_bytes")
        sha256 = artifact.get("sha256")
        if (
            not object_key
            or object_key.startswith("/")
            or "://" in object_key
            or ".." in object_key.split("/")
            or not filename
            or "/" in filename
            or "\\" in filename
            or len(filename) > 1024
            or kind not in ("ARCHIVO", "CAPTURA", "TRACE", "OTRO")
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or size_bytes < 0
        ):
            raise InvalidArtifactError("metadata de artefacto inválida")
        if content_type is not None and len(content_type) > 255:
            raise InvalidArtifactError("content_type de artefacto inválido")
        if sha256 is not None:
            sha256 = str(sha256).lower()
            if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
                raise InvalidArtifactError("sha256 de artefacto inválido")
        return {
            "kind": kind,
            "object_key": object_key,
            "filename": filename,
            "content_type": content_type,
            "size_bytes": size_bytes,
            "sha256": sha256,
        }

    @staticmethod
    def _artifact_signature(artifact: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            str(artifact.get("kind") or "ARCHIVO"),
            str(artifact.get("object_key") or ""),
            str(artifact.get("filename") or artifact.get("name") or ""),
            artifact.get("content_type"),
            artifact.get("size_bytes"),
            artifact.get("sha256"),
        )

    async def expired_leases(self, *, batch_size: int = 100) -> list[UUID]:
        """Lista jobs con lease vencido para recuperacion condicionada."""
        rows = (
            await self._session.execute(
                REAP_EXPIRED_SQL, {"batch_size": batch_size}
            )
        ).all()
        return [UUID(str(r[0])) for r in rows]


class IdempotencyConflict(RepositoryError):
    """La misma clave de replay se usó con un request/resultado divergente."""


class StaleAssignmentError(RepositoryError):
    """El callback no corresponde al intento actualmente asignado."""


class InvalidArtifactError(RepositoryError):
    """Los metadatos del artifact no corresponden al contrato seguro."""


class PersistenceError(RepositoryError):
    """Falló la persistencia canónica y el callback debe poder reintentarse."""


__all__ = [
    "ASSIGN_JOB_SQL",
    "CLAIM_SQL",
    "REAP_EXPIRED_SQL",
    "RELEASE_ASSIGNMENT_SQL",
    "IdempotencyConflict",
    "InvalidArtifactError",
    "JobRepository",
    "PersistenceError",
    "StaleAssignmentError",
]
