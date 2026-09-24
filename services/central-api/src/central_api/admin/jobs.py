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
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from central_api.admin._common import redactar_metadata, require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.db import db_configurado, nueva_sesion
from central_api.security.rsa_credentials import (
    CredentialDecryptionError,
    decrypt_configured_credential,
)
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
        from central_api.models.identity import User
    except Exception:  # noqa: BLE001 - sin modelos, solo memoria
        return None
    try:
        async with nueva_sesion() as sesion:
            filas = (await sesion.execute(
                select(Job).order_by(Job.created_at.desc(), Job.id.desc()).limit(500)
            )).scalars().all()
            user_emails = {
                str(user_id): email
                for user_id, email in (await sesion.execute(
                    select(User.id, User.email).where(
                        User.id.in_([fila.user_id for fila in filas])
                    )
                )).all()
            } if filas else {}
    except Exception:  # noqa: BLE001 - sin base, solo memoria
        return None
    adaptados = []
    for fila in filas:
        adaptados.append(SimpleNamespace(
            id=str(fila.id), status=str(fila.status), bot=fila.bot,
            operation=fila.operation,
            payload={"user_id": str(fila.user_id)},
            user_email=user_emails.get(str(fila.user_id)),
            worker_node=str(fila.worker_id) if fila.worker_id else None,
            assignment_attempt=int(fila.attempts or 0),
            created_at=fila.created_at, result=None,
            credential_metadata=dict(fila.credential_metadata or {}),
            credentials_available=bool(fila.credential_ciphertext),
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


def _iso(value: object) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _registro_memoria(job) -> dict:
    resultado = _sanear_resultado(job)
    return {
        "job": {
            "id": job.id,
            "bot": job.bot,
            "operacion": job.operation,
            "estado": job.status,
            "intento": job.assignment_attempt,
            "worker": job.worker_node,
            "creado_en": _iso(job.created_at),
            "finalizado_en": None,
        },
        "request": redactar_metadata(dict(job.payload or {})),
        "credentials": {
            "available": bool(getattr(job, "credentials", None)),
            "fields": list((getattr(job, "credential_metadata", {}) or {}).get("fields", [])),
            "context": dict((getattr(job, "credential_metadata", {}) or {}).get("context", {})),
        },
        "result": redactar_metadata(resultado) if resultado else None,
        "artifacts": [],
        "events": [],
        "source": "memoria",
    }


async def _ejecuciones_db(
    *, job_id: str | None = None, bot: str = "", estado: str = "", limit: int = 100
) -> list[dict] | None:
    """Lee el agregado completo de ejecución y sus tablas relacionadas."""
    if not db_configurado():
        return None
    try:
        from sqlalchemy import select

        from central_api.models.execution import Job, JobArtifact, JobEvent, JobResult
        from central_api.models.identity import User
    except Exception:  # noqa: BLE001 - desarrollo sin SQLAlchemy
        return None
    try:
        async with nueva_sesion() as sesion:
            stmt = select(Job).order_by(Job.created_at.desc(), Job.id.desc())
            if job_id:
                stmt = stmt.where(Job.id == uuid.UUID(job_id))
            if bot:
                stmt = stmt.where(Job.bot == bot)
            if estado:
                stmt = stmt.where(Job.status == estado)
            filas = list((await sesion.execute(stmt.limit(max(1, min(limit, 500))))).scalars())
            ids = [fila.id for fila in filas]
            if not ids:
                return []
            user_emails = {
                str(user_id): email
                for user_id, email in (await sesion.execute(
                    select(User.id, User.email).where(
                        User.id.in_([fila.user_id for fila in filas])
                    )
                )).all()
            }
            resultados = {
                str(fila.job_id): fila
                for fila in (await sesion.execute(
                    select(JobResult).where(JobResult.job_id.in_(ids))
                )).scalars()
            }
            artefactos_por_job: dict[str, list] = {}
            for fila in (await sesion.execute(
                select(JobArtifact).where(JobArtifact.job_id.in_(ids))
            )).scalars():
                artefactos_por_job.setdefault(str(fila.job_id), []).append(fila)
            eventos_por_job: dict[str, list] = {}
            for fila in (await sesion.execute(
                select(JobEvent).where(JobEvent.job_id.in_(ids)).order_by(JobEvent.occurred_at)
            )).scalars():
                eventos_por_job.setdefault(str(fila.job_id), []).append(fila)
    except Exception:  # noqa: BLE001 - la vista no debe romper el panel
        return None

    salida = []
    for fila in filas:
        key = str(fila.id)
        resultado = resultados.get(key)
        salida.append({
            "job": {
                "id": key,
                "user_id": str(fila.user_id),
                "usuario": user_emails.get(str(fila.user_id)),
                "usuario_email": user_emails.get(str(fila.user_id)),
                "bot": fila.bot,
                "operacion": fila.operation,
                "estado": fila.status,
                "resultado": fila.result,
                "intento": int(fila.attempts or 0),
                "worker_id": str(fila.worker_id) if fila.worker_id else None,
                "creado_en": _iso(fila.created_at),
                "asignado_en": _iso(fila.assigned_at),
                "iniciado_en": _iso(fila.started_at),
                "finalizado_en": _iso(fila.finished_at),
            },
            "credentials": {
                "available": bool(fila.credential_ciphertext),
                "fields": list(
                    (dict(fila.credential_metadata or {})).get("fields", [])
                ),
                "context": dict(
                    (dict(fila.credential_metadata or {})).get("context", {})
                ),
            },
            "request": redactar_metadata(dict(fila.request_payload or {})),
            "result": (
                {
                    "attempt": resultado.attempt,
                    "result": resultado.result,
                    "payload": redactar_metadata(dict(resultado.payload or {})),
                    "summary": redactar_metadata(dict(resultado.summary or {})),
                    "received_at": _iso(resultado.received_at),
                }
                if resultado is not None else None
            ),
            "artifacts": [
                {
                    "id": str(artifact.id),
                    "kind": artifact.kind,
                    "name": artifact.filename,
                    "filename": artifact.filename,
                    "content_type": artifact.content_type,
                    "size_bytes": artifact.size_bytes,
                    "sha256": artifact.sha256,
                    "created_at": _iso(artifact.created_at),
                    "expires_at": _iso(artifact.expires_at),
                }
                for artifact in artefactos_por_job.get(key, [])
            ],
            "events": [
                {
                    "id": str(event.id),
                    "attempt": event.attempt,
                    "type": event.event_type,
                    "key": event.event_key,
                    "payload": redactar_metadata(dict(event.payload or {})),
                    "occurred_at": _iso(event.occurred_at),
                }
                for event in eventos_por_job.get(key, [])
            ],
            "source": "postgresql",
        })
    return salida


def _registros_por_tabla(registros: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Descompone el agregado en las tablas operativas que reemplazan V1/V2."""
    tablas = {
        "jobs": [],
        "job_results": [],
        "job_artifacts": [],
        "job_events": [],
    }
    for registro in registros:
        job = dict(registro.get("job") or {})
        job_id = job.get("id")
        tablas["jobs"].append({
            **job,
            "credentials": dict(registro.get("credentials") or {}),
            "request": dict(registro.get("request") or {}),
        })
        resultado = registro.get("result")
        if resultado is not None:
            tablas["job_results"].append({"job_id": job_id, **dict(resultado)})
        for artifact in registro.get("artifacts") or []:
            tablas["job_artifacts"].append({"job_id": job_id, **dict(artifact)})
        for event in registro.get("events") or []:
            tablas["job_events"].append({"job_id": job_id, **dict(event)})
    return tablas

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
        "usuario_email": getattr(job, "user_email", None),
        "worker": job.worker_node,
        "intento": job.assignment_attempt,
        "creado_en": job.created_at.isoformat() if job.created_at else None,
        "claves_payload": sorted((job.payload or {}).keys()),
        "credenciales": {
            "available": bool(
                getattr(job, "credentials_available", False)
                or getattr(job, "credentials", None)
            ),
            "fields": list(
                (getattr(job, "credential_metadata", {}) or {}).get("fields", [])
            ),
        },
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


@router.get("/executions")
async def listar_ejecuciones_admin(
    authorization: str | None = Header(default=None),
    bot: str = "",
    estado: str = "",
    limit: int = 100,
) -> dict:
    """Lista el registro unificado de ejecuciones de todos los bots.

    El registro reúne ``jobs``, ``job_results``, ``job_artifacts`` y
    ``job_events``. Esto reemplaza la vista fragmentada de las tablas
    ``consulta_*_logs`` de V2 sin perder sus datos operativos.
    """
    require_admin(authorization)
    registros = await _ejecuciones_db(bot=bot, estado=estado, limit=limit)
    if registros is None:
        registros = [
            _registro_memoria(job)
            for job in sorted(
                JOBS.values(),
                key=lambda item: (str(item.created_at), item.id),
                reverse=True,
            )
            if (not bot or job.bot == bot) and (not estado or job.status == estado)
        ][: max(1, min(limit, 500))]
    return {
        "success": True,
        "total": len(registros),
        "records": registros,
        "fuente": "postgresql" if db_configurado() else "memoria",
    }


@router.get("/executions/{job_id}")
async def ver_ejecucion_admin(
    job_id: str,
    authorization: str | None = Header(default=None),
) -> dict:
    """Devuelve el agregado completo de una ejecución."""
    require_admin(authorization)
    job_id = _validar_uuid(job_id)
    registros = await _ejecuciones_db(job_id=job_id, limit=1)
    if registros is None:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job no encontrado")
        return {"success": True, "record": _registro_memoria(job)}
    if not registros:
        raise HTTPException(status_code=404, detail="job no encontrado")
    return {"success": True, "record": registros[0]}


@router.get("/records")
async def listar_registros_tablas_admin(
    authorization: str | None = Header(default=None),
    tabla: str = "all",
    job_id: str | None = None,
    bot: str = "",
    estado: str = "",
    limit: int = 100,
) -> dict:
    """Lista cada tabla de ejecución con una forma compatible con V1/V2.

    ``jobs``, ``job_results``, ``job_artifacts`` y ``job_events`` se mantienen
    separados en ``tables``. Los artefactos exponen únicamente nombre y
    metadatos, nunca URLs prefirmadas ni ``object_key``.
    """
    require_admin(authorization)
    allowed = {"all", "jobs", "job_results", "job_artifacts", "job_events"}
    if tabla not in allowed:
        raise HTTPException(status_code=400, detail="tabla no válida")
    registros = await _ejecuciones_db(
        job_id=job_id, bot=bot, estado=estado, limit=limit
    )
    if registros is None:
        registros = [
            _registro_memoria(job)
            for job in sorted(
                JOBS.values(),
                key=lambda item: (str(item.created_at), item.id),
                reverse=True,
            )
            if (not bot or job.bot == bot) and (not estado or job.status == estado)
        ][: max(1, min(limit, 500))]
    tables = _registros_por_tabla(registros)
    selected = registros if tabla == "all" else tables[tabla]
    return {
        "success": True,
        "tabla": tabla,
        "total": len(selected),
        "records": selected,
        "tables": tables,
        "fuente": "postgresql" if db_configurado() else "memoria",
    }


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


@router.get("/jobs/{job_id}/credentials")
async def revelar_credencial_admin(
    job_id: str,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> JSONResponse:
    """Revela temporalmente una clave descifrada y deja auditoría.

    PostgreSQL solo contiene el ciphertext RSA. El texto claro se crea en la
    respuesta una vez validado el token administrativo y nunca se escribe en
    logs, eventos, resultados ni payloads.
    """
    actor = require_admin(authorization)
    job_id = _validar_uuid(job_id)
    ciphertext: str | None = None
    metadata: dict[str, Any] = {}
    if db_configurado():
        try:
            from sqlalchemy import select

            from central_api.models.execution import Job

            async with nueva_sesion() as sesion:
                fila = (await sesion.execute(
                    select(Job).where(Job.id == uuid.UUID(job_id))
                )).scalar_one_or_none()
            if fila is None:
                raise HTTPException(status_code=404, detail="job no encontrado")
            ciphertext = fila.credential_ciphertext
            metadata = dict(fila.credential_metadata or {})
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=503, detail="registro no disponible") from None
    else:
        job = JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job no encontrado")
        credentials = dict(job.credentials or {})
        metadata = dict(getattr(job, "credential_metadata", {}) or {})
        clave = credentials.get("clave")
        if not isinstance(clave, str) or not clave:
            raise HTTPException(status_code=404, detail="job sin credencial custodiada")
        log_event(
            "job.credentials.revealed", actor_id=actor, target_type="job",
            target_id=job_id, request_id=request_id or "", result="success",
            metadata={"fields": ["clave"], "source": "memoria"},
        )
        return JSONResponse(
            {
                "success": True,
                "job_id": job_id,
                "credentials": {"clave": clave},
                "credential_metadata": metadata,
            },
            headers={"Cache-Control": "private, no-store"},
        )
    if not ciphertext:
        raise HTTPException(status_code=404, detail="job sin credencial custodiada")
    try:
        clave = decrypt_configured_credential(ciphertext)
    except (CredentialDecryptionError, RuntimeError, ValueError):
        raise HTTPException(status_code=503, detail="credencial no disponible") from None
    log_event(
        "job.credentials.revealed", actor_id=actor, target_type="job",
        target_id=job_id, request_id=request_id or "", result="success",
        metadata={"fields": ["clave"], "source": "postgresql"},
    )
    return JSONResponse(
        {
            "success": True,
            "job_id": job_id,
            "credentials": {"clave": clave},
            "credential_metadata": metadata,
        },
        headers={"Cache-Control": "private, no-store"},
    )


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
