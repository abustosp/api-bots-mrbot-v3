"""Eventos de job recibidos del worker (plan 02 §7.7).

Tipos: ``started`` (ASIGNADO->CORRIENDO, lease de ejecución), ``progress`` /
``heartbeat_hint`` (renuevan lease con token e intento coincidentes),
``warning`` (solo diagnóstico), ``cancelled`` (CANCELADO + política de
cobro), ``failed_prestart`` (reencola o falla según intentos). Idempotencia
por ``event_id``; token de intento antiguo: ``409 stale_assignment``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from central_api.db import db_configurado, nueva_sesion
from central_api.internal.dependencies import require_worker
from central_api.internal.job_access import job_para_worker
from central_api.scheduler import reaper as reaper_mod
from central_api.store import utcnow

router = APIRouter()


async def _espejar_evento_db(job_id: str, body: JobEventBody) -> tuple[str, bool]:
    """Actualiza jobs y job_events idempotentemente dentro de una transacción."""
    import uuid

    from central_api.repositories.jobs import JobRepository

    async with nueva_sesion() as sesion:
        repo = JobRepository(sesion)  # type: ignore[arg-type]
        job, created = await repo.persist_event(
            uuid.UUID(str(job_id)),
            attempt=body.assignment_attempt,
            event_key=body.event_id,
            event_type=body.event_type,
            message=body.message,
        )
        return str(job.status), not created


SEEN_EVENTS: dict[tuple[str, int, str], tuple[str, str]] = {}

VALID_TYPES = (
    "started", "progress", "warning", "cancelled",
    "failed_prestart", "heartbeat_hint",
)


class JobEventBody(BaseModel):
    event_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=1, max_length=32)
    assignment_attempt: int = Field(ge=0)
    assignment_token: str = Field(default="", max_length=512)
    message: str = Field(default="", max_length=256)


@router.post("/jobs/{job_id}/events")
async def report_event(
    job_id: str, body: JobEventBody, worker_node: str = Depends(require_worker)
) -> dict:
    job = await job_para_worker(job_id, worker_node)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    if body.event_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="tipo de evento inválido")
    event_key = (job.id, body.assignment_attempt, body.event_id)
    signature = (body.event_type, body.message)
    seen = SEEN_EVENTS.get(event_key)
    if seen is not None:
        if seen != signature:
            raise HTTPException(status_code=409, detail="evento duplicado divergente")
        return {"success": True, "job_id": job.id, "status": job.status, "dedup": True}
    lease = reaper_mod.LEASES.get(job_id)
    if body.event_type in ("progress", "heartbeat_hint") and lease:
        if body.assignment_token and body.assignment_token != lease.get("token"):
            raise HTTPException(status_code=409, detail="stale_assignment")
    status_before = job.status
    dedup = False
    if db_configurado():
        from central_api.repositories.base import NotFoundError, RepositoryError
        from central_api.repositories.jobs import (
            IdempotencyConflict,
            PersistenceError,
            StaleAssignmentError,
        )

        try:
            persisted_status, dedup = await _espejar_evento_db(job.id, body)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail="evento duplicado divergente") from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="job no encontrado") from exc
        except StaleAssignmentError as exc:
            raise HTTPException(status_code=409, detail="stale_assignment") from exc
        except PersistenceError as exc:
            raise HTTPException(
                status_code=503,
                detail="no se pudo confirmar el evento; reintente",
                headers={"Retry-After": "3"},
            ) from exc
        except RepositoryError as exc:
            raise HTTPException(status_code=422, detail="evento inválido") from exc
        except Exception as exc:  # noqa: BLE001 - dejar retry ante fallo de DB
            raise HTTPException(
                status_code=503,
                detail="no se pudo confirmar el evento; reintente",
                headers={"Retry-After": "3"},
            ) from exc
        job.status = persisted_status
    if dedup:
        SEEN_EVENTS[event_key] = signature
        return {"success": True, "job_id": job.id, "status": job.status, "dedup": True}
    SEEN_EVENTS[event_key] = signature
    now = utcnow().isoformat().replace("+00:00", "Z")
    if body.event_type == "started" and status_before == "ASIGNADO":
        from central_api.api.dependencies import JOB_META

        job.status = "CORRIENDO"
        job.assignment_attempt = max(job.assignment_attempt, body.assignment_attempt)
        meta = JOB_META.get(job.id)
        if meta is not None and not meta.get("started_at"):
            meta["started_at"] = now
        reaper_mod.renew_lease(job_id)
    elif body.event_type in ("progress", "heartbeat_hint"):
        if job.status in ("ASIGNADO", "CORRIENDO"):
            reaper_mod.renew_lease(job_id)
    elif body.event_type == "cancelled" and status_before in ("ASIGNADO", "CORRIENDO"):
        from central_api.api.dependencies import JOB_META
        from central_api.billing.reservation import confirm_usage

        job.status = "CANCELADO"
        meta = JOB_META.get(job.id)
        if meta is not None:
            meta["finished_at"] = now
            meta["cancel_reason"] = meta.get("cancel_reason") or body.message or None
            meta["cancelled_by"] = meta.get("cancelled_by") or "worker"
        # Cancelado en vuelo: cobro confirmado por defecto (sin reembolso auto).
        confirm_usage(job.id, motivo="cancelado en vuelo")
        reaper_mod.clear_lease(job_id)
    elif body.event_type == "failed_prestart" and status_before == "ASIGNADO":
        job.status = "PENDIENTE"
        job.worker_node = None
        reaper_mod.clear_lease(job_id)
    elif db_configurado():
        # Para progreso/warning, el estado de DB sigue siendo la autoridad.
        job.status = persisted_status
    return {"success": True, "job_id": job.id, "status": job.status}
