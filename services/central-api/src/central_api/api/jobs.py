"""Lectura, cancelación, lote y listado de jobs (plan 02 §3.6-§3.9).

- ``GET /jobs/{job_id}`` conserva la forma V2 ``JobStatusResponse`` y aplica
  ownership (sin enumeración ajena); la lectura nunca consume cuota.
- ``POST /jobs/{job_id}/cancelar``: en ``PENDIENTE`` cancela y libera la
  reserva en la misma unidad lógica; en ``ASIGNADO``/``CORRIENDO`` registra
  la solicitud cooperativa (el reaper/dispatcher la entrega al worker).
- ``POST /jobs/estado:lote``: máximo 200 IDs, error por item, sin consumo.
- ``GET /jobs``: cursor opaco sobre UUIDv7 (``job_id DESC``), sin offsets.
"""

from __future__ import annotations

import base64
import json

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from central_api.api.dependencies import (
    JOB_META,
    correlation_id,
    ensure_meta,
    project_job,
    require_api_principal,
)
from central_api.billing.reservation import confirm_usage, release_usage
from central_api.db import db_configurado, nueva_sesion
from central_api.security.permissions import can_cancel, can_read_job
from central_api.security.principals import ApiPrincipal
from central_api.security.secret_redaction import public_error
from central_api.store import JOBS, utcnow

router = APIRouter()


async def _visible_job_db(job_id: str, principal: ApiPrincipal):
    """Lee el job desde PostgreSQL; ``None`` si hay que usar memoria.

    Adapta la fila ORM a la forma que espera ``project_job`` (sin exponer
    columnas internas ni secretos).
    """
    from types import SimpleNamespace

    try:
        import uuid

        from central_api.repositories.jobs import JobRepository
    except Exception:  # noqa: BLE001 - sin repos, fallback de desarrollo
        return None
    try:
        jid, uid = uuid.UUID(str(job_id)), uuid.UUID(str(principal.user_id))
    except (ValueError, AttributeError, TypeError):
        return None
    try:
        async with nueva_sesion() as sesion:
            repo = JobRepository(sesion)  # type: ignore[arg-type]
            fila = await repo.get_scoped(jid, uid)
    except Exception:  # noqa: BLE001 - fuera de ámbito o sin fila: 404
        return None
    creado = fila.created_at.isoformat() if fila.created_at else None
    return SimpleNamespace(
        id=str(fila.id), status=str(fila.status), result={},
        bot=fila.bot, operation=fila.operation, created_at=fila.created_at,
        _creado=creado,
    )

TERMINAL = ("COMPLETO", "FALLIDO", "CANCELADO")


class CancelBody(BaseModel):
    motivo: str = Field(default="", max_length=500)


class BatchBody(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


def _visible_job(job_id: str, principal: ApiPrincipal):
    job = JOBS.get(job_id)
    if job is None:
        return None
    meta = JOB_META.get(job_id, {})
    if not can_read_job(principal, str(meta.get("owner", principal.user_id))):
        return None
    return job


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str, principal: ApiPrincipal = Depends(require_api_principal)
) -> JSONResponse:
    job = _visible_job(job_id, principal)
    if job is None and db_configurado():
        job = await _visible_job_db(job_id, principal)
    if job is None:
        return JSONResponse(status_code=404, content=public_error("not_found"))
    return JSONResponse(status_code=200, content=project_job(job))


@router.post("/jobs/{job_id}/cancelar")
def cancel_job(
    job_id: str, body: CancelBody,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    job = JOBS.get(job_id)
    meta = JOB_META.get(job_id, {})
    if job is None or not can_cancel(principal, str(meta.get("owner", principal.user_id))):
        return JSONResponse(status_code=404, content=public_error("not_found"))
    ensure_meta(job.id, str(meta.get("owner", principal.user_id)))
    if job.status in TERMINAL:
        return JSONResponse(status_code=200, content=project_job(job))
    now = utcnow().isoformat().replace("+00:00", "Z")
    meta = JOB_META[job.id]
    meta["cancel_reason"] = body.motivo or None
    meta["cancelled_by"] = "user"
    if job.status == "PENDIENTE":
        job.status = "CANCELADO"
        meta["finished_at"] = now
        release_usage(job.id, motivo="cancelado en PENDIENTE")
    else:
        # ASIGNADO/CORRIENDO: solicitud cooperativa; el worker confirma o el
        # reaper cierra al vencer la lease. Sin reembolso automático.
        meta["cancel_requested_at"] = now
    return JSONResponse(status_code=200, content=project_job(job))


@router.post("/jobs/estado:lote")
def batch_status(
    body: BatchBody, principal: ApiPrincipal = Depends(require_api_principal)
) -> JSONResponse:
    _ = correlation_id  # la correlación viaja en middleware/respuesta global
    if len(body.job_ids) > 200:
        return JSONResponse(status_code=400, content=public_error("validation"))
    items: list[dict] = []
    for raw in body.job_ids:
        job = JOBS.get(raw)
        meta = JOB_META.get(raw, {})
        if job is None or not can_read_job(
            principal, str(meta.get("owner", principal.user_id))
        ):
            items.append({"job_id": raw, "error": {
                "error_code": "not_found", "message": "Job no encontrado"}})
            continue
        items.append(project_job(job))
    return JSONResponse(status_code=200, content={"items": items})


def _encode_cursor(job_id: str) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"v": 1, "job_id": job_id}).encode("utf-8")
    ).decode("ascii")


@router.get("/jobs")
def list_jobs(
    status: str | None = None,
    bot: str | None = None,
    operacion: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """Listado propio por cursor UUIDv7 (``job_id DESC``), sin offsets."""
    limit = max(1, min(100, limit))
    owned = [
        job for job in JOBS.values()
        if str(JOB_META.get(job.id, {}).get("owner", principal.user_id))
        == principal.user_id
    ]
    if status:
        owned = [j for j in owned if j.status == status]
    if bot:
        owned = [j for j in owned if j.bot == bot]
    if operacion:
        owned = [j for j in owned if j.operation == operacion]
    owned.sort(key=lambda j: j.id, reverse=True)
    if cursor:
        try:
            border = json.loads(
                base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
            )["job_id"]
            owned = [j for j in owned if j.id < border]
        except (ValueError, KeyError, TypeError):
            return JSONResponse(status_code=400, content=public_error("validation"))
    page = owned[:limit]
    rest = owned[limit:]
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "jobs": [
                {"job_id": j.id, "status": j.status, "bot": j.bot,
                 "operacion": j.operation} for j in page
            ],
            "items": [project_job(j) for j in page],
            "next_cursor": _encode_cursor(page[-1].id) if rest and page else None,
            "has_more": bool(rest),
        },
    )
