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
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

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

        from sqlalchemy import select

        from central_api.models.execution import JobArtifact, JobResult
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
            resultado = (
                await sesion.execute(
                    select(JobResult).where(JobResult.job_id == jid)
                )
            ).scalar_one_or_none()
            artefactos = (
                await sesion.execute(
                    select(JobArtifact)
                    .where(JobArtifact.job_id == jid)
                    .order_by(JobArtifact.created_at, JobArtifact.id)
                )
            ).scalars().all()
    except Exception:  # noqa: BLE001 - fuera de ámbito o sin fila: 404
        return None
    creado = fila.created_at.isoformat() if fila.created_at else None
    ahora = utcnow()
    files = [
        {
            "artifact_id": str(item.id),
            "filename": item.filename,
            "content_type": item.content_type,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "download_url": (
                f"/api/v3/jobs/{jid}/artifacts/{item.id}/download"
            ),
        }
        for item in artefactos
        if item.expires_at is None or item.expires_at > ahora
    ]
    payload = resultado.payload if resultado is not None else {}
    summary = resultado.summary if resultado is not None else {}
    result = {
        "result": resultado.result if resultado is not None else None,
        "error": (summary or {}).get("error"),
    }
    return SimpleNamespace(
        id=str(fila.id), status=str(fila.status), result=result,
        bot=fila.bot, operation=fila.operation, created_at=fila.created_at,
        started_at=fila.started_at, finished_at=fila.finished_at,
        _meta={
            "started_at": fila.started_at.isoformat() if fila.started_at else None,
            "finished_at": fila.finished_at.isoformat() if fila.finished_at else None,
            "cancel_reason": getattr(fila, "cancel_reason", None),
            "cancelled_by": getattr(fila, "cancelled_by", None),
            "files": files,
            "data": {"schema_version": 1, **(payload or {})},
            "error": (summary or {}).get("error"),
        },
        _creado=creado,
    )

TERMINAL = ("COMPLETO", "FALLIDO", "CANCELADO")


class CancelBody(BaseModel):
    motivo: str = Field(default="", max_length=500)


class BatchBody(BaseModel):
    job_ids: list[str] = Field(default_factory=list)


class JobArtifactResponse(BaseModel):
    """Metadata pública de un artefacto, sin URLs internas ni secretos."""

    artifact_id: str
    filename: str
    content_type: str | None = None
    size_bytes: int | None = None
    sha256: str | None = None
    download_url: str | None = None


class JobStatusResponse(BaseModel):
    """Forma V2 compatible para consultar el resultado de un job V3."""

    job_id: str = Field(..., examples=["0190f0c0-7f5b-7b2e-9f7e-123456789abc"])
    status: str = Field(..., examples=["COMPLETO"])
    result: str | None = Field(default=None, examples=["OK"])
    bot: str
    operation: str
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    cancel_reason: str | None = None
    cancelled_by: str | None = None
    error: Any = None
    files: list[JobArtifactResponse] = Field(default_factory=list)
    data: dict[str, Any] | None = None

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "job_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abc",
                "status": "COMPLETO",
                "result": "OK",
                "bot": "mis_comprobantes",
                "operation": "consultar",
                "created_at": "2026-09-26T04:00:00Z",
                "started_at": "2026-09-26T04:00:02Z",
                "finished_at": "2026-09-26T04:00:12Z",
                "cancel_reason": None,
                "cancelled_by": None,
                "error": None,
                "files": [
                    {
                        "artifact_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abd",
                        "filename": "comprobantes-2025.zip",
                        "content_type": "application/zip",
                        "size_bytes": 20480,
                        "sha256": "<sha256>",
                        "download_url": "/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download",
                    }
                ],
                "data": {"schema_version": 1, "items": []},
            }
        }
    )


def _visible_job(job_id: str, principal: ApiPrincipal):
    job = JOBS.get(job_id)
    if job is None:
        return None
    meta = JOB_META.get(job_id, {})
    if not can_read_job(principal, str(meta.get("owner", principal.user_id))):
        return None
    return job


LIVE_STATUSES = ("PENDIENTE", "ASIGNADO", "CORRIENDO")


def _queue_item(job: Any, position: int | None) -> dict[str, Any]:
    return {
        "job_id": str(job.id),
        "status": str(job.status),
        "bot": job.bot,
        "operation": job.operation,
        "operacion": job.operation,
        "position": position,
        "queue_position": position,
    }


def _queue_response(items: list[dict[str, Any]]) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content={"success": True, "jobs": items, "items": items, "count": len(items)},
    )


async def _queue_jobs_db(
    principal: ApiPrincipal,
    *,
    bot: str | None,
    status: str | None,
) -> list[dict[str, Any]]:
    """Read live jobs and global pending positions from PostgreSQL."""
    import uuid

    from sqlalchemy import select

    from central_api.models.execution import Job

    try:
        user_id = uuid.UUID(str(principal.user_id))
    except (ValueError, AttributeError, TypeError):
        return []
    requested_status = status.strip().upper() if status else None
    if requested_status and requested_status not in LIVE_STATUSES:
        return []

    async with nueva_sesion() as session:
        # Queue position is the actual global FIFO rank, independent of the
        # owner's optional bot/status filters.
        pending_ids = (
            await session.execute(
                select(Job.id)
                .where(Job.status == "PENDIENTE")
                .order_by(Job.priority.asc(), Job.created_at.asc(), Job.id.asc())
            )
        ).scalars().all()
        positions = {str(job_id): index for index, job_id in enumerate(pending_ids, 1)}

        statement = select(Job).where(
            Job.user_id == user_id,
            Job.status.in_(LIVE_STATUSES),
        )
        if bot:
            statement = statement.where(Job.bot == bot)
        if requested_status:
            statement = statement.where(Job.status == requested_status)
        rows = (
            await session.execute(
                statement.order_by(
                    Job.priority.asc(), Job.created_at.asc(), Job.id.asc()
                )
            )
        ).scalars().all()
        return [_queue_item(row, positions.get(str(row.id))) for row in rows]


@router.get(
    "/jobs/cola",
    summary="Listar jobs activos propios y su posición en cola",
)
async def list_queue(
    bot: str | None = None,
    status: str | None = None,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """List the authenticated user's active jobs, preferring PostgreSQL."""
    if status and status.strip().upper() not in LIVE_STATUSES:
        return _queue_response([])
    if db_configurado():
        try:
            return _queue_response(
                await _queue_jobs_db(principal, bot=bot, status=status)
            )
        except Exception:  # noqa: BLE001 - no degradar a memoria cuando hay DB
            return JSONResponse(
                status_code=503,
                content=public_error("service_not_enabled"),
                headers={"Retry-After": "3"},
            )

    pending = sorted(
        (job for job in JOBS.values() if job.status == "PENDIENTE"),
        key=lambda job: (job.created_at, job.id),
    )
    positions = {job.id: index for index, job in enumerate(pending, 1)}
    owned = [
        job
        for job in JOBS.values()
        if job.status in LIVE_STATUSES
        and can_read_job(
            principal,
            str(JOB_META.get(job.id, {}).get("owner", principal.user_id)),
        )
        and (not bot or job.bot == bot)
        and (not status or job.status == status.strip().upper())
    ]
    owned.sort(key=lambda job: (job.created_at, job.id))
    return _queue_response(
        [_queue_item(job, positions.get(job.id)) for job in owned]
    )


@router.get(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Consultar estado y resultado de un job",
)
async def get_job(
    job_id: str, principal: ApiPrincipal = Depends(require_api_principal)
) -> JSONResponse:
    job = None
    if db_configurado():
        job = await _visible_job_db(job_id, principal)
    else:
        job = _visible_job(job_id, principal)
    if job is None:
        return JSONResponse(status_code=404, content=public_error("not_found"))
    return JSONResponse(
        status_code=200,
        content=project_job(job, getattr(job, "_meta", None)),
    )


async def _cancel_job_db(
    job_id: str,
    body: CancelBody,
    principal: ApiPrincipal,
) -> JSONResponse:
    """Cancel a PostgreSQL job using a scoped row lock and durable update."""
    import uuid
    from types import SimpleNamespace

    from sqlalchemy import select

    from central_api.models.execution import Job

    try:
        parsed_id = uuid.UUID(str(job_id))
    except (ValueError, TypeError, AttributeError):
        return JSONResponse(status_code=404, content=public_error("not_found"))

    release_reservation = False
    async with nueva_sesion() as session:
        row = (
            await session.execute(
                select(Job).where(Job.id == parsed_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or not can_cancel(principal, str(row.user_id)):
            return JSONResponse(status_code=404, content=public_error("not_found"))

        now = utcnow()
        if row.status not in TERMINAL:
            row.cancel_reason = body.motivo or None
            row.cancelled_by = "USER"
            if row.status == "PENDIENTE":
                row.status = "CANCELADO"
                row.finished_at = now
                row.result = None
                release_reservation = True

        meta = {
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "cancel_reason": row.cancel_reason,
            "cancelled_by": row.cancelled_by,
            "error": row.error_message,
            "files": [],
            "data": None,
        }
        projected = project_job(
            SimpleNamespace(
                id=str(row.id),
                status=str(row.status),
                result={"result": row.result} if row.result else None,
                bot=row.bot,
                operation=row.operation,
                created_at=row.created_at,
            ),
            meta,
        )

    if release_reservation:
        release_usage(job_id, motivo="cancelado en PENDIENTE")
    mirror = JOBS.get(job_id)
    if mirror is not None:
        mirror.status = projected["status"]
    mirror_meta = ensure_meta(job_id, principal.user_id)
    mirror_meta["cancel_reason"] = projected["cancel_reason"]
    mirror_meta["cancelled_by"] = projected["cancelled_by"]
    if projected["finished_at"]:
        mirror_meta["finished_at"] = projected["finished_at"]
    return JSONResponse(status_code=200, content=projected)


@router.post(
    "/jobs/{job_id}/cancelar",
    response_model=JobStatusResponse,
    summary="Cancelar un job",
)
async def cancel_job(
    job_id: str, body: CancelBody,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    if db_configurado():
        try:
            return await _cancel_job_db(job_id, body, principal)
        except Exception:  # noqa: BLE001 - no degradar a memoria cuando hay DB
            return JSONResponse(
                status_code=503,
                content=public_error("service_not_enabled"),
                headers={"Retry-After": "3"},
            )

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


@router.delete(
    "/jobs/{job_id}",
    response_model=JobStatusResponse,
    summary="Cancelar un job por ID (alias DELETE)",
)
async def delete_job(
    job_id: str,
    request: Request,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """REST-style alias for the existing POST cancellation endpoint."""
    motivo = "cancelado desde DELETE"
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            raw = await request.json()
        except (ValueError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            motivo = str(raw.get("motivo") or motivo)[:500]
    return await cancel_job(job_id, CancelBody(motivo=motivo), principal)


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
