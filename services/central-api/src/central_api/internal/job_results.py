"""Reporte de resultado terminal idempotente (plan 02 §7.8).

``POST /internal/v1/jobs/{job_id}/result`` valida el sobre, persiste
resultado y artefactos en una unidad lógica, confirma o libera la reserva de
billing según la matriz de cobro y marca el job terminal. Idempotente por
``(job_id, assignment_attempt)``: el replay idéntico responde 200 con lo
persistido; un segundo resultado distinto es conflicto auditado.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from central_api.db import db_configurado, nueva_sesion
from central_api.internal.dependencies import require_worker
from central_api.scheduler import reaper as reaper_mod
from central_api.store import JOBS, utcnow

router = APIRouter()

TERMINAL_OK = ("OK", "PARCIAL")


async def _espejar_resultado_db(
    job_id: str, resultado: str, intento: int, datos: dict
) -> None:
    """Persiste el resultado terminal en ``job_results`` (1:0..1 por job).

    Espejo best-effort: con base configurada la fila canónica vive en
    PostgreSQL; el flujo en memoria no se bloquea si la base no responde.
    """
    try:
        import uuid

        from central_api.models.execution import JobResult
        from central_api.repositories.jobs import JobRepository
    except Exception:  # noqa: BLE001 - sin modelos, solo memoria
        return
    try:
        jid = uuid.UUID(str(job_id))
        estado = "COMPLETO" if resultado in TERMINAL_OK else "FALLIDO"
        valor = resultado if resultado in TERMINAL_OK else "ERROR"
    except (ValueError, AttributeError, TypeError):
        return
    try:
        async with nueva_sesion() as sesion:
            repo = JobRepository(sesion)  # type: ignore[arg-type]
            try:
                await repo.transition(
                    jid, expect=("ASIGNADO", "CORRIENDO", "PENDIENTE"),
                    status=estado, result=valor,
                )
            except Exception:  # noqa: BLE001 - ya terminal en PG, sigue
                pass
            sesion.add(JobResult(
                job_id=jid, attempt=max(1, intento), result=valor,
                payload=dict(datos or {}), summary={},
            ))
    except Exception:  # noqa: BLE001 - espejo best-effort, no bloquea
        return


class ResultBody(BaseModel):
    result: str = "OK"
    data: dict = {}
    error: dict | None = None
    assignment_attempt: int = 0
    event_id: str = ""
    error_category: str | None = None


@router.post("/jobs/{job_id}/result")
async def report_result(
    job_id: str, body: ResultBody, worker_node: str = Depends(require_worker)
) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    if job.worker_node and job.worker_node != worker_node:
        raise HTTPException(status_code=403, detail="asignación de otro worker")
    if job.result is not None:
        if job.result.get("attempt", 0) == body.assignment_attempt:
            return {"success": True, "job_id": job.id, "status": job.status, "dedup": True}
        raise HTTPException(status_code=409, detail="resultado duplicado divergente")
    if job.status in ("COMPLETO", "FALLIDO", "CANCELADO") and job.result is not None:
        return {"success": True, "job_id": job.id, "status": job.status, "dedup": True}
    from central_api.api.dependencies import JOB_META
    from central_api.billing.reservation import confirm_usage, refund_usage

    job.result = {
        "result": body.result, "data": body.data, "error": body.error,
        "attempt": body.assignment_attempt, "event_id": body.event_id,
    }
    terminal_ok = body.result in TERMINAL_OK
    job.status = "COMPLETO" if terminal_ok else "FALLIDO"
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
    entry = reaper_mod.WORKER_LOAD.get(worker_node)
    if entry is not None and entry > 0:
        reaper_mod.WORKER_LOAD[worker_node] = entry - 1
    if db_configurado():
        await _espejar_resultado_db(
            job.id, body.result, body.assignment_attempt, body.data
        )
    return {"success": True, "job_id": job.id, "status": job.status}
