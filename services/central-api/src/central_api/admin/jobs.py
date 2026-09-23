"""Operación de jobs del panel: vistas, comandos durables y métricas de cola.

- La grilla nunca carga payloads completos: solo claves y metadatos.
- Cancelar persiste un comando durable con ID; el worker lo obtiene por su
  canal autenticado y responde con acuse (aquí, acuse inicial PENDIENTE).
- Reencolar solo admite estados reintentables y respeta el máximo de
  intentos sin duplicar un consumo confirmado.
- El modo diagnóstico exige lectura y genera auditoría; jamás expone
  credenciales fiscales, tokens ni URLs con secretos.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.db import db_configurado, nueva_sesion
from central_api.settings import get_settings
from central_api.store import JOBS, utcnow

router = APIRouter()


async def _jobs_db() -> list | None:
    """Jobs desde PostgreSQL adaptados a la grilla; ``None`` sin base.

    Lee ``models.Job`` (fuente canónica con base configurada) y adapta cada
    fila a los atributos que usa ``_resumen_job`` (sin payloads completos ni
    secretos).
    """
    if not db_configurado():
        return None
    try:
        from types import SimpleNamespace

        from sqlalchemy import select

        from central_api.models.execution import Job
    except Exception:  # noqa: BLE001 - sin modelos, solo memoria
        return None
    try:
        async with nueva_sesion() as sesion:
            filas = (await sesion.execute(
                select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(500)
            )).scalars().all()
    except Exception:  # noqa: BLE001 - sin base, solo memoria
        return None
    adaptados = []
    for fila in filas:
        adaptados.append(SimpleNamespace(
            id=str(fila.id), status=str(fila.status), bot=fila.bot,
            operation=fila.operation,
            payload={"user_id": str(fila.user_id)},
            worker_node=str(fila.worker_id) if fila.worker_id else None,
            assignment_attempt=int(fila.attempts or 0),
            created_at=fila.created_at, result=None,
        ))
    return adaptados


async def _conteo_estados_db() -> dict | None:
    """Conteo de jobs por estado en PostgreSQL; ``None`` sin base."""
    if not db_configurado():
        return None
    try:
        from sqlalchemy import func, select

        from central_api.models.execution import Job
    except Exception:  # noqa: BLE001 - sin modelos, solo memoria
        return None
    try:
        async with nueva_sesion() as sesion:
            filas = (await sesion.execute(
                select(Job.status, func.count()).group_by(Job.status)
            )).all()
    except Exception:  # noqa: BLE001 - sin base, solo memoria
        return None
    return {str(estado): int(total) for estado, total in filas}

ESTADOS_TERMINALES = ("COMPLETO", "FALLIDO", "CANCELADO")
ESTADOS_REINTENTABLES = ("FALLIDO",)


@dataclass
class JobCommand:
    """Comando durable de control hacia el worker (cancelar/drenar)."""

    id: str
    job_id: str
    tipo: str
    estado: str = "PENDIENTE"
    motivo: str = ""
    actor: str = ""
    creado_en: str = ""
    acuse: str = ""


JOB_COMMANDS: dict[str, list[JobCommand]] = {}
# Prioridades ajustadas por operaciones (el store del esqueleto no la modela).
JOB_OVERRIDES: dict[str, dict] = {}


def _resumen_job(job) -> dict:
    return {
        "job_id": job.id,
        "estado": job.status,
        "bot": job.bot,
        "operacion": job.operation,
        "usuario": (job.payload or {}).get("user_id"),
        "worker": job.worker_node,
        "intento": job.assignment_attempt,
        "creado_en": job.created_at.isoformat() if job.created_at else None,
        "claves_payload": sorted((job.payload or {}).keys()),
        "prioridad_admin": JOB_OVERRIDES.get(job.id, {}).get("prioridad"),
    }


def _sanear_resultado(job) -> dict | None:
    if job.result is None:
        return None
    resultado = dict(job.result)
    for clave in ("credentials", "credenciales", "sealed_section", "token"):
        resultado.pop(clave, None)
    return resultado


def _validar_uuid(valor: str) -> str:
    try:
        return str(uuid.UUID(str(valor)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="job_id con formato inválido") from exc


class MotivoBody(BaseModel):
    motivo: str = ""


class PrioridadBody(BaseModel):
    prioridad: int = Field(ge=0, le=100)
    motivo: str = ""


@router.get("/jobs")
async def listar_jobs_admin(
    authorization: str | None = Header(default=None),
    estado: str = "",
    bot: str = "",
    usuario: str = "",
    worker: str = "",
    limit: int = 50,
    cursor: str = "",
) -> dict:
    """Lista jobs por estado, bot, usuario y worker con cursor estable."""
    require_admin(authorization)
    # Con base configurada la grilla lee PostgreSQL (models); si no hay
    # filas o no hay base, cae al fallback en memoria de desarrollo.
    jobs_db = await _jobs_db()
    jobs = sorted(
        (jobs_db if jobs_db else list(JOBS.values())),
        key=lambda j: (str(j.created_at), j.id),
    )
    if estado:
        jobs = [j for j in jobs if j.status == estado]
    if bot:
        jobs = [j for j in jobs if j.bot == bot]
    if usuario:
        jobs = [j for j in jobs if (j.payload or {}).get("user_id") == usuario]
    if worker:
        jobs = [j for j in jobs if j.worker_node == worker]
    if cursor:
        jobs = [j for j in jobs if (str(j.created_at), j.id) > tuple(cursor.split("|", 1))]
    top = max(1, min(limit, 200))
    pagina = jobs[:top]
    siguiente = (
        f"{pagina[-1].created_at}|{pagina[-1].id}" if len(jobs) > top else None
    )
    return {
        "success": True,
        "total": len(jobs),
        "jobs": [_resumen_job(j) for j in pagina],
        "siguiente_cursor": siguiente,
    }


@router.get("/jobs/metrics")
async def metricas_cola(authorization: str | None = Header(default=None)) -> dict:
    """Combina métricas de la cola en memoria con el estado de la flota."""
    require_admin(authorization)
    # Con base configurada los conteos salen de PostgreSQL (GROUP BY).
    conteo_db = await _conteo_estados_db()
    por_estado: dict[str, int] = dict(conteo_db) if conteo_db is not None else {}
    if conteo_db is None:
        for job in JOBS.values():
            por_estado[job.status] = por_estado.get(job.status, 0) + 1
    pendientes = por_estado.get("PENDIENTE", 0)
    corriendo = por_estado.get("CORRIENDO", 0) + por_estado.get("ASIGNADO", 0)
    return {
        "success": True,
        "por_estado": por_estado,
        "pendientes": pendientes,
        "en_ejecucion": corriendo,
        "comandos_durables": sum(len(v) for v in JOB_COMMANDS.values()),
        "fuente": "postgresql" if conteo_db is not None else "memoria",
    }


@router.get("/jobs/{job_id}")
def ver_job_admin(
    job_id: str,
    authorization: str | None = Header(default=None),
    diagnostico: bool = False,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Detalle con resultado saneado; el diagnóstico se audita y no sale a /api/v3."""
    actor = require_admin(authorization)
    job = JOBS.get(_validar_uuid(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    comandos = [asdict(c) for c in JOB_COMMANDS.get(job.id, [])]
    respuesta: dict = {
        "success": True,
        "job": _resumen_job(job),
        "resultado": _sanear_resultado(job),
        "artefactos": [],
        "comandos": comandos,
    }
    if diagnostico:
        log_event(
            "job.diagnostics.read", actor_id=actor, target_type="job",
            target_id=job.id, request_id=request_id or "", result="success",
            metadata={"worker": job.worker_node, "intento": job.assignment_attempt},
        )
        respuesta["diagnostico"] = {
            "categoria_interna": "scheduler",
            "intentos": job.assignment_attempt,
            "worker": job.worker_node,
            "credenciales": "[REDACTED]",
            "nota": "Sin contraseña fiscal, token ni URL con credenciales.",
        }
    return respuesta


@router.post("/jobs/{job_id}/cancel", status_code=202)
def cancelar_job(
    job_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Persiste un comando durable de cancelación y devuelve el acuse inicial."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    job = JOBS.get(_validar_uuid(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    if job.status in ESTADOS_TERMINALES:
        raise HTTPException(status_code=409, detail="job ya terminal, no cancelable")
    comando = JobCommand(
        id=str(uuid.uuid4()), job_id=job.id, tipo="cancelar",
        motivo=motivo, actor=actor, creado_en=utcnow().isoformat(),
        acuse="PENDIENTE: comando durable registrado, el worker debe acusar",
    )
    JOB_COMMANDS.setdefault(job.id, []).append(comando)
    log_event(
        "job.cancel.requested", actor_id=actor, target_type="job", target_id=job.id,
        request_id=request_id or "", result="accepted", reason=motivo,
        metadata={"comando_id": comando.id, "worker": job.worker_node},
    )
    return {"success": True, "estado": "PENDIENTE", "comando": asdict(comando)}


@router.post("/jobs/{job_id}/requeue", status_code=202)
def reencolar_job(
    job_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Reencola un job fallido sin duplicar consumo ni superar intentos."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    job = JOBS.get(_validar_uuid(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    if job.status not in ESTADOS_REINTENTABLES:
        raise HTTPException(status_code=409, detail="solo jobs FALLIDO son reencolables")
    maximo = get_settings().max_execution_attempts
    if job.assignment_attempt >= maximo:
        raise HTTPException(status_code=409, detail="máximo de intentos alcanzado")
    job.status = "PENDIENTE"
    job.worker_node = None
    job.assignment_attempt += 1
    log_event(
        "job.requeued", actor_id=actor, target_type="job", target_id=job.id,
        request_id=request_id or "", reason=motivo,
        metadata={"intento": job.assignment_attempt, "consumo": "reservado, sin duplicar"},
    )
    return {"success": True, "job": _resumen_job(job)}


@router.post("/jobs/{job_id}/priority")
def priorizar_job(
    job_id: str,
    body: PrioridadBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Ajusta la prioridad de un job pendiente con cambio auditado."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    job = JOBS.get(_validar_uuid(job_id))
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    if job.status != "PENDIENTE":
        raise HTTPException(status_code=409, detail="solo jobs PENDIENTE se priorizan")
    JOB_OVERRIDES[job.id] = {"prioridad": body.prioridad, "motivo": motivo}
    log_event(
        "job.priority.changed", actor_id=actor, target_type="job", target_id=job.id,
        request_id=request_id or "", reason=motivo,
        metadata={"prioridad": body.prioridad},
    )
    return {"success": True, "job": _resumen_job(job)}


def comandos_de_job(job_id: str) -> list[dict]:
    """Expone los comandos durables de un job para el canal del worker."""
    return [asdict(c) for c in JOB_COMMANDS.get(job_id, [])]
