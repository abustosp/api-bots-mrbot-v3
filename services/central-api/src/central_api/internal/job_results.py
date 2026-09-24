"""Reporte de resultado terminal idempotente (plan 02 §7.8).

``POST /internal/v1/jobs/{job_id}/result`` valida el sobre, persiste
resultado y artefactos en una unidad lógica, confirma o libera la reserva de
billing según la matriz de cobro y marca el job terminal. Idempotente por
``(job_id, assignment_attempt)``: el replay idéntico responde 200 con lo
persistido; un segundo resultado distinto es conflicto auditado.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from central_api.db import db_configurado, nueva_sesion
from central_api.internal.dependencies import require_worker
from central_api.internal.job_access import job_para_worker
from central_api.scheduler import reaper as reaper_mod
from central_api.store import utcnow

router = APIRouter()

TERMINAL_OK = ("OK", "PARCIAL")


class ArtifactBody(BaseModel):
    """Descriptor de salida, nunca acepta URLs firmadas ni campos extra."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str | None = Field(default=None, max_length=128)
    object_key: str = Field(min_length=1, max_length=1024)
    name: str = Field(min_length=1, max_length=255)
    size_bytes: int = Field(ge=0)
    content_type: str = Field(min_length=1, max_length=128)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class ResultBody(BaseModel):
    result: str = Field(default="OK", pattern=r"^(OK|PARCIAL|ERROR|CANCELADO)$")
    data: dict = Field(default_factory=dict)
    error: dict | None = None
    artifacts: list[ArtifactBody] = Field(default_factory=list, max_length=100)
    assignment_attempt: int = Field(default=1, ge=1)
    event_id: str = Field(default="", max_length=128)
    error_category: str | None = Field(default=None, max_length=80)


async def _espejar_resultado_db(job_id: str, body: ResultBody) -> tuple[str, bool]:
    """Guarda estado, response y artifacts en una transacción canónica.

    La actualización de ``jobs`` y los INSERT de ``job_results`` y
    ``job_artifacts`` disparan la proyección física por bot dentro de la misma
    transacción. Cualquier error se propaga para que el worker pueda reintentar.
    """
    import uuid

    from central_api.repositories.jobs import JobRepository

    jid = uuid.UUID(str(job_id))
    artifacts = [
        item.model_dump(exclude_none=True)
        for item in body.artifacts
    ]
    summary = {
        "error": dict(body.error or {}),
        "event_id": body.event_id,
        "error_category": body.error_category,
    }
    async with nueva_sesion() as sesion:
        repo = JobRepository(sesion)  # type: ignore[arg-type]
        job, created = await repo.persist_result(
            jid,
            attempt=body.assignment_attempt,
            result=body.result,
            payload=dict(body.data or {}),
            summary=summary,
            artifacts=artifacts,
        )
        return str(job.status), not created


@router.post("/jobs/{job_id}/result")
async def report_result(
    job_id: str, body: ResultBody, worker_node: str = Depends(require_worker)
) -> dict:
    job = await job_para_worker(job_id, worker_node)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    artifacts = [item.model_dump(exclude_none=True) for item in body.artifacts]
    callback_result = {
        "result": body.result,
        "data": body.data,
        "error": body.error,
        "attempt": body.assignment_attempt,
        "event_id": body.event_id,
        "error_category": body.error_category,
        "artifacts": artifacts,
    }
    dedup = False
    if db_configurado():
        from central_api.repositories.base import NotFoundError, RepositoryError
        from central_api.repositories.jobs import (
            IdempotencyConflict,
            InvalidArtifactError,
            PersistenceError,
            StaleAssignmentError,
        )

        try:
            persisted_status, dedup = await _espejar_resultado_db(job.id, body)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail="resultado duplicado divergente") from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="job no encontrado") from exc
        except StaleAssignmentError as exc:
            raise HTTPException(status_code=409, detail="stale_assignment") from exc
        except InvalidArtifactError as exc:
            raise HTTPException(status_code=422, detail="metadata de artefacto inválida") from exc
        except PersistenceError as exc:
            raise HTTPException(
                status_code=503,
                detail="no se pudo confirmar el resultado; reintente",
                headers={"Retry-After": "3"},
            ) from exc
        except RepositoryError as exc:
            raise HTTPException(status_code=422, detail="resultado inválido") from exc
        except Exception as exc:  # noqa: BLE001 - fallo de DB debe permitir retry
            raise HTTPException(
                status_code=503,
                detail="no se pudo confirmar el resultado; reintente",
                headers={"Retry-After": "3"},
            ) from exc
        job.status = persisted_status
    elif job.result is not None:
        if job.result == callback_result:
            return {
                "success": True, "job_id": job.id,
                "status": job.status, "dedup": True,
            }
        raise HTTPException(status_code=409, detail="resultado duplicado divergente")
    from central_api.api.dependencies import JOB_META
    from central_api.billing.reservation import confirm_usage, refund_usage

    job.result = callback_result
    terminal_ok = body.result in TERMINAL_OK
    if not db_configurado():
        job.status = (
            "COMPLETO" if terminal_ok
            else "CANCELADO" if body.result == "CANCELADO"
            else "FALLIDO"
        )
    now = utcnow().isoformat().replace("+00:00", "Z")
    meta = JOB_META.get(job.id)
    if meta is not None:
        meta["finished_at"] = now
        if terminal_ok:
            meta["data"] = {"schema_version": 1, **(body.data or {})}
        else:
            meta["error"] = body.error or {"error_code": "unexpected"}
    if terminal_ok or (body.error_category or "") != "INFRASTRUCTURE_ERROR":
        # COMPLETO o error de negocio: cobro confirmado por defecto.
        confirm_usage(job.id)
    else:
        # Fallo de infraestructura: reembolso propuesto (libera la reserva).
        refund_usage(job.id, motivo="INFRASTRUCTURE_ERROR")
    reaper_mod.clear_lease(job.id)
    if not dedup:
        entry = reaper_mod.WORKER_LOAD.get(worker_node)
        if entry is not None and entry > 0:
            reaper_mod.WORKER_LOAD[worker_node] = entry - 1
    response = {"success": True, "job_id": job.id, "status": job.status}
    if dedup:
        response["dedup"] = True
    return response
