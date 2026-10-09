"""Limpieza explícita y purga por retención de la historia de jobs.

La purga trabaja sólo sobre jobs terminales y sus tablas técnicas dependientes.
Nunca toca billing, pagos, ledgers ni auditoría financiera. Toda ejecución deja
auditoría, y el modo predeterminado es dry-run.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.db import db_configurado, nueva_sesion
from central_api.store import JOBS

router = APIRouter()
TERMINAL = ("COMPLETO", "FALLIDO", "CANCELADO")


class RetentionPurgeBody(BaseModel):
    retention_days: int = Field(default=90, ge=1, le=3650)
    batch_size: int = Field(default=500, ge=1, le=1000)
    dry_run: bool = True
    motivo: str = ""


async def _clear_terminal_db(cutoff: datetime, batch_size: int, *, all_jobs: bool) -> list[str]:
    from sqlalchemy import text

    where = "" if all_jobs else "AND COALESCE(finished_at, created_at) < :cutoff"
    select_sql = text(
        "SELECT id FROM jobs WHERE status IN ('COMPLETO', 'FALLIDO', 'CANCELADO') "
        f"{where} ORDER BY COALESCE(finished_at, created_at), id LIMIT :batch_size"
    )
    async with nueva_sesion() as session:
        selected = (await session.execute(select_sql, {"cutoff": cutoff, "batch_size": batch_size})).scalars().all()
        ids = list(selected)
        if not ids:
            return []
        # Las tablas de detalle se eliminan primero. Las FKs de V3 conservan
        # integridad y no se usa SQL dinámico de nombres de tabla/vista legacy.
        for table in ("job_artifacts", "job_events", "job_results"):
            await session.execute(text(f"DELETE FROM {table} WHERE job_id = ANY(:ids)"), {"ids": ids})
        await session.execute(text("DELETE FROM jobs WHERE id = ANY(:ids)"), {"ids": ids})
    return [str(job_id) for job_id in ids]


def _clear_terminal_memory(cutoff: datetime, batch_size: int, *, all_jobs: bool) -> list[str]:
    from central_api.api.dependencies import JOB_META

    candidates = []
    for job in JOBS.values():
        created = getattr(job, "created_at", None)
        if created is not None and created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if job.status not in TERMINAL:
            continue
        if not all_jobs and (created is None or created >= cutoff):
            continue
        candidates.append(job)
    candidates.sort(key=lambda job: (str(getattr(job, "created_at", "")), job.id))
    selected = candidates[:batch_size]
    for job in selected:
        JOBS.pop(job.id, None)
        JOB_META.pop(job.id, None)
    return [job.id for job in selected]


async def _extract_confirmation(request: Request) -> str:
    """Acepta la forma V1 urlencoded y JSON para clientes del panel V3."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            value = (await request.json()).get("confirmation_text", "")
        except (ValueError, AttributeError):
            value = ""
    else:
        try:
            value = (await request.form()).get("confirmation_text", "")
        except Exception:
            # ``python-multipart`` no es necesario para el formulario V1
            # urlencoded. Mantener este parser mínimo evita que el endpoint
            # dependa de una instalación opcional.
            try:
                parsed = parse_qs((await request.body()).decode("utf-8"))
                value = parsed.get("confirmation_text", [""])[0]
            except (UnicodeDecodeError, ValueError):
                value = ""
    return str(value)


@router.post("/tables/clear-consulta-logs")
async def clear_consulta_logs(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Equivalente V1: exige escribir ``ELIMINAR`` y borra historia terminal.

    Los jobs activos nunca son purgados para no romper leases ni reservas. La
    operación se limita a 1.000 filas por invocación para conservar el servicio.
    """
    actor = require_admin(authorization)
    if (await _extract_confirmation(request)).strip().upper() != "ELIMINAR":
        raise HTTPException(status_code=400, detail="Para eliminar los logs debes escribir ELIMINAR.")
    cutoff = datetime.now(timezone.utc)
    try:
        deleted = await _clear_terminal_db(cutoff, 1000, all_jobs=True) if db_configurado() else _clear_terminal_memory(cutoff, 1000, all_jobs=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Servicio no disponible") from exc
    log_event(
        "logs.cleared",
        actor_id=actor,
        target_type="job_history",
        target_id="consulta_logs",
        reason="confirmación ELIMINAR",
        metadata={"deleted_jobs": len(deleted), "batch_limited": len(deleted) == 1000},
    )
    return {"success": True, "deleted_jobs": len(deleted), "batch_limited": len(deleted) == 1000}


@router.post("/retention/purge")
@router.post("/tables/purge-consulta-logs")
async def purge_retention(
    body: RetentionPurgeBody,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Previsualiza o ejecuta una purga acotada de jobs terminales por edad."""
    actor = require_admin(authorization)
    reason = validar_motivo(body.motivo, obligatorio=not body.dry_run)
    cutoff = datetime.now(timezone.utc) - timedelta(days=body.retention_days)
    if body.dry_run:
        if db_configurado():
            from sqlalchemy import text

            try:
                async with nueva_sesion() as session:
                    ids = (await session.execute(
                        text(
                            "SELECT id FROM jobs WHERE status IN ('COMPLETO', 'FALLIDO', 'CANCELADO') "
                            "AND COALESCE(finished_at, created_at) < :cutoff "
                            "ORDER BY COALESCE(finished_at, created_at), id LIMIT :batch_size"
                        ),
                        {"cutoff": cutoff, "batch_size": body.batch_size},
                    )).scalars().all()
                selected = [str(item) for item in ids]
            except Exception as exc:
                raise HTTPException(status_code=503, detail="Servicio no disponible") from exc
        else:
            from central_api.api.dependencies import JOB_META

            selected = [
                job.id for job in sorted(JOBS.values(), key=lambda item: (str(item.created_at), item.id))
                if job.status in TERMINAL and job.created_at < cutoff
            ][:body.batch_size]
            _ = JOB_META
    else:
        try:
            selected = await _clear_terminal_db(cutoff, body.batch_size, all_jobs=False) if db_configurado() else _clear_terminal_memory(cutoff, body.batch_size, all_jobs=False)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Servicio no disponible") from exc
    log_event(
        "retention.purge_previewed" if body.dry_run else "retention.purged",
        actor_id=actor,
        target_type="job_history",
        target_id="terminal_jobs",
        reason=reason,
        metadata={
            "retention_days": body.retention_days,
            "cutoff": cutoff.isoformat(),
            "batch_size": body.batch_size,
            "dry_run": body.dry_run,
            "affected_jobs": len(selected),
        },
    )
    return {
        "success": True,
        "dry_run": body.dry_run,
        "retention_days": body.retention_days,
        "cutoff": cutoff.isoformat().replace("+00:00", "Z"),
        "affected_jobs": len(selected),
        "job_ids": selected,
        "has_more": len(selected) == body.batch_size,
    }
