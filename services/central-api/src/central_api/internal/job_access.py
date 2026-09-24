"""Acceso autenticado a jobs para callbacks internos, incluso tras reinicio."""

from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select

from central_api.db import db_configurado, nueva_sesion
from central_api.internal.dependencies import resolve_worker_uuid
from central_api.models.execution import Job as JobRow
from central_api.store import JOBS, Job


async def job_para_worker(job_id: str, worker_node: str) -> Job | None:
    """Obtiene job cacheado o hidratado, verificando su worker_id canónico.

    En modo PostgreSQL la identidad autenticada del worker debe coincidir con
    ``jobs.worker_id``; conocer un UUID de job o estar registrado no basta.
    """
    cached = JOBS.get(job_id)
    if not db_configurado():
        if cached is None:
            return None
        if cached.worker_node and cached.worker_node != worker_node:
            raise HTTPException(status_code=403, detail="asignación de otro worker")
        return cached

    try:
        job_uuid = uuid.UUID(str(job_id))
    except (ValueError, TypeError, AttributeError):
        return None
    worker_uuid = resolve_worker_uuid(worker_node)
    if worker_uuid is None:
        raise HTTPException(status_code=403, detail="worker sin identidad canónica")

    try:
        async with nueva_sesion() as session:
            row = (
                await session.execute(
                    select(JobRow).where(JobRow.id == job_uuid)
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.worker_id is None or str(row.worker_id) != str(worker_uuid):
                raise HTTPException(status_code=403, detail="asignación de otro worker")
            if cached is not None:
                if cached.worker_node and cached.worker_node != worker_node:
                    raise HTTPException(status_code=403, detail="asignación de otro worker")
                cached.worker_node = worker_node
                cached.status = str(row.status)
                cached.assignment_attempt = int(row.attempts or 0)
                return cached
            hydrated = Job(
                id=str(row.id),
                bot=str(row.bot),
                operation=str(row.operation),
                payload=dict(row.request_payload or {}),
                credential_metadata=dict(row.credential_metadata or {}),
                status=str(row.status),
                worker_node=worker_node,
                assignment_attempt=int(row.attempts or 0),
                created_at=row.created_at,
            )
            JOBS[hydrated.id] = hydrated
            return hydrated
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - no simular cache en fallo de DB
        raise HTTPException(
            status_code=503,
            detail="no se pudo verificar la asignación del job",
            headers={"Retry-After": "3"},
        ) from exc


__all__ = ["job_para_worker"]
