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
from central_api.scheduler import reaper as reaper_mod
from central_api.store import JOBS, utcnow

router = APIRouter()


async def _espejar_evento_db(job_id: str, body: object) -> None:
    """Persiste el evento en ``job_events`` (idempotente por clave).

    Espejo best-effort: con base configurada la fila canónica vive en
    PostgreSQL (models + restricción única); el flujo en memoria no se
    bloquea si la base no responde.
    """
    try:
        import uuid

        from sqlalchemy import select

        from central_api.models.base import new_uuid7
        from central_api.models.execution import JobEvent
    except Exception:  # noqa: BLE001 - sin modelos, solo memoria
        return
    mapa_tipos = {
        "started": "INICIADO", "progress": "PROGRESO",
        "heartbeat_hint": "LEASE_RENOVADO", "warning": "PROGRESO",
        "cancelled": "CANCELADO", "failed_prestart": "REINTENTO",
    }
    try:
        jid = uuid.UUID(str(job_id))
        intento = int(getattr(body, "assignment_attempt", 0) or 0)
        clave = str(getattr(body, "event_id", "") or "")[:128]
        tipo = mapa_tipos.get(str(getattr(body, "event_type", "") or ""), "PROGRESO")
    except (ValueError, AttributeError, TypeError):
        return
    try:
        async with nueva_sesion() as sesion:
            existe = (await sesion.execute(
                select(JobEvent.id).where(
                    JobEvent.job_id == jid,
                    JobEvent.attempt == intento,
                    JobEvent.event_key == clave,
                )
            )).scalar_one_or_none()
            if existe is None:
                sesion.add(JobEvent(
                    id=new_uuid7(), job_id=jid, attempt=intento,
                    event_type=tipo, event_key=clave,
                    payload={},
                ))
    except Exception:  # noqa: BLE001 - espejo best-effort, no bloquea
        return

SEEN_EVENTS: set[str] = set()

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
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    if job.worker_node and job.worker_node != worker_node:
        raise HTTPException(status_code=403, detail="asignación de otro worker")
    if body.event_id in SEEN_EVENTS:
        return {"success": True, "job_id": job.id, "status": job.status, "dedup": True}
    if body.event_type not in VALID_TYPES:
        raise HTTPException(status_code=400, detail="tipo de evento inválido")
    lease = reaper_mod.LEASES.get(job_id)
    if body.event_type in ("progress", "heartbeat_hint") and lease:
        if body.assignment_token and body.assignment_token != lease.get("token"):
            raise HTTPException(status_code=409, detail="stale_assignment")
    SEEN_EVENTS.add(body.event_id)
    now = utcnow().isoformat().replace("+00:00", "Z")
    if body.event_type == "started" and job.status == "ASIGNADO":
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
    elif body.event_type == "cancelled" and job.status in ("ASIGNADO", "CORRIENDO"):
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
    elif body.event_type == "failed_prestart" and job.status == "ASIGNADO":
        job.status = "PENDIENTE"
        job.worker_node = None
        reaper_mod.clear_lease(job_id)
    if db_configurado():
        await _espejar_evento_db(job_id, body)
    return {"success": True, "job_id": job.id, "status": job.status}
