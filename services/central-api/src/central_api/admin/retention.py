"""Limpieza explícita y purga por retención de la historia de jobs.

La purga trabaja sólo sobre jobs terminales y sus tablas técnicas dependientes.
Nunca toca billing, pagos, ledgers ni auditoría financiera. Toda ejecución deja
auditoría, y el modo predeterminado es dry-run.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.db import db_configurado, nueva_sesion
from central_api.settings import get_settings
from central_api.storage import delete_object_bytes
from central_api.store import JOBS

router = APIRouter()
TERMINAL = ("COMPLETO", "FALLIDO", "CANCELADO")


class RetentionPurgeBody(BaseModel):
    retention_days: int = Field(
        default=90,
        ge=1,
        le=3650,
        description="Antigüedad mínima de los registros terminales que se consideran, entre 1 y 3650 días.",
        examples=[90],
    )
    batch_size: int = Field(
        default=500,
        ge=1,
        le=1000,
        description="Máximo de jobs incluidos en esta operación, entre 1 y 1000.",
        examples=[500],
    )
    dry_run: bool = Field(
        default=True,
        description="Si es true, informa los jobs afectados sin borrarlos; si es false, los elimina.",
        examples=[True],
    )
    motivo: str = Field(
        default="",
        description="Motivo de auditoría; obligatorio al ejecutar la eliminación y, si se envía, debe tener entre 10 y 500 caracteres.",
        examples=["Depuración de historial antiguo"],
    )


_AUTH_RESPONSES = {
    401: {
        "description": "Falta una credencial administrativa Bearer. Envíe Authorization: Bearer <ADMIN_TOKEN>.",
        "content": {"application/json": {"example": {"detail": "Falta Bearer [REDACTED]"}}},
    },
    403: {
        "description": "La credencial administrativa es incorrecta o no está configurada. Verifique el token y habilite la administración antes de reintentar.",
        "content": {
            "application/json": {
                "examples": {
                    "token_invalido": {"value": {"detail": "ADMIN_TOKEN inválido"}},
                    "administracion_no_configurada": {"value": {"detail": "ADMIN_TOKEN no configurado"}},
                }
            }
        },
    },
}

_SERVICE_UNAVAILABLE = {
    503: {
        "description": "No se pudo completar la lectura o eliminación del historial. Compruebe la disponibilidad del servicio y vuelva a intentarlo más tarde.",
        "content": {"application/json": {"example": {"detail": "Servicio no disponible"}}},
    }
}

_VALIDATION_RESPONSE = {
    422: {
        "description": (
            "No se pudo interpretar un parámetro de la solicitud antes de "
            "ejecutar la operación. Revise la estructura y el formato de los "
            "parámetros o cabeceras; una confirmación ausente o incorrecta "
            "se informa como error 400."
        ),
        "content": {
            "application/json": {
                "example": {
                    "detail": [
                        {"loc": ["header", "authorization"], "msg": "Input should be a valid string", "type": "string_type"}
                    ]
                }
            }
        },
    }
}


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


async def _cleanup_expired_uploads(batch_size: int, *, dry_run: bool) -> dict[str, Any]:
    """Borra objetos expirados de uploads sin hacer fallar la purga de jobs."""
    now = datetime.now(timezone.utc)
    more = False
    if db_configurado():
        from sqlalchemy import text

        async with nueva_sesion() as session:
            result = await session.execute(
                text(
                    "SELECT object_key FROM upload_tickets WHERE expires_at <= :now "
                    "ORDER BY expires_at, object_key LIMIT :limit"
                ),
                {"now": now, "limit": batch_size + 1},
            )
            rows = result.mappings().all()
        keys = [str(row["object_key"]) for row in rows[:batch_size]]
        more = len(rows) > batch_size
    else:
        from central_api.api.uploads import TICKETS

        expired: list[tuple[datetime, str]] = []
        for key, ticket in TICKETS.items():
            expires_at = ticket.get("expires_at")
            if isinstance(expires_at, str):
                try:
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                except ValueError:
                    continue
            if not isinstance(expires_at, datetime):
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                expired.append((expires_at, str(key)))
        expired.sort()
        keys = [key for _, key in expired[:batch_size]]
        more = len(expired) > batch_size

    if dry_run:
        return {
            "considered": len(keys),
            "deleted": 0,
            "failed": 0,
            "has_more": more,
            "dry_run": True,
        }

    settings = get_settings()
    deleted_keys: list[str] = []
    failed = 0
    for key in keys:
        try:
            await asyncio.to_thread(
                delete_object_bytes,
                endpoint=settings.object_storage_endpoint,
                region=settings.object_storage_region,
                bucket=settings.object_storage_uploads_bucket,
                access_key=settings.object_storage_access_key,
                secret_key=settings.object_storage_secret_key,
                object_key=key,
            )
            deleted_keys.append(key)
        except Exception:
            failed += 1

    if deleted_keys and db_configurado():
        from sqlalchemy import text

        try:
            async with nueva_sesion() as session:
                await session.execute(
                    text(
                        "DELETE FROM upload_tickets WHERE object_key = ANY(:keys) "
                        "AND expires_at <= :now"
                    ),
                    {"keys": deleted_keys, "now": now},
                )
        except Exception:
            # El DELETE remoto es idempotente y el ticket seguirá siendo elegible
            # en la próxima ejecución si no se pudo retirar de la base.
            failed += len(deleted_keys)
    elif deleted_keys:
        from central_api.api.uploads import TICKETS

        for key in deleted_keys:
            TICKETS.pop(key, None)

    return {
        "considered": len(keys),
        "deleted": len(deleted_keys),
        "failed": failed,
        "has_more": more,
        "dry_run": False,
        "error_code": "storage_unavailable" if failed else None,
    }


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


@router.post(
    "/tables/clear-consulta-logs",
    summary="Eliminar el historial de jobs terminales",
    description=(
        "Elimina hasta 1000 jobs terminales, sin límite de antigüedad, y sus "
        "registros de detalle. No afecta jobs activos ni modifica pagos o "
        "auditoría financiera. Para confirmar, envíe confirmation_text con "
        "el valor ELIMINAR, en mayúsculas o minúsculas. La operación requiere "
        "autorización administrativa Bearer y queda registrada."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {"confirmation_text": {"type": "string", "description": "Debe contener ELIMINAR para confirmar."}},
                        "required": ["confirmation_text"],
                    },
                    "example": {"confirmation_text": "ELIMINAR"},
                },
                "application/x-www-form-urlencoded": {
                    "schema": {
                        "type": "object",
                        "properties": {"confirmation_text": {"type": "string", "description": "Debe contener ELIMINAR para confirmar."}},
                        "required": ["confirmation_text"],
                    },
                    "example": "confirmation_text=ELIMINAR",
                },
            },
        }
    },
    responses={
        200: {
            "description": "Confirma cuántos jobs terminales se eliminaron y si se alcanzó el máximo por llamada.",
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": {"success": True, "deleted_jobs": 12, "batch_limited": False},
                }
            },
        },
        400: {
            "description": "La frase de confirmación falta o no coincide con ELIMINAR. Envíe confirmation_text=ELIMINAR y repita la operación.",
            "content": {"application/json": {"example": {"detail": "Para eliminar los logs debes escribir ELIMINAR."}}},
        },
        **_AUTH_RESPONSES,
        **_SERVICE_UNAVAILABLE,
        **_VALIDATION_RESPONSE,
    },
)
async def clear_consulta_logs(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Borra jobs terminales previa confirmación explícita.

    Los jobs activos nunca se purgan para conservar su procesamiento. La
    operación se limita a 1.000 filas por invocación.
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


@router.post(
    "/retention/purge",
    summary="Previsualizar o ejecutar la purga por antigüedad",
    description=(
        "Selecciona jobs terminales (COMPLETO, FALLIDO o CANCELADO) con una "
        "antigüedad superior al plazo indicado y procesa hasta batch_size por "
        "llamada. Por seguridad, dry_run es true de forma predeterminada y "
        "solo informa los identificadores; para borrar, establezca dry_run en "
        "false e indique un motivo. Requiere autorización administrativa "
        "Bearer. También previsualiza o elimina objetos del bucket de cargas "
        "cuya fecha de vencimiento pasó, según UPLOADS_RETENTION_SECONDS "
        "(24 horas por defecto). Los errores de almacenamiento se informan en "
        "uploads.failed y no impiden purgar los jobs. No modifica pagos ni "
        "registros de auditoría."
    ),
    responses={
        200: {
            "description": "Resultado de la previsualización o eliminación, con corte e identificadores incluidos.",
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": {
                        "success": True,
                        "dry_run": True,
                        "retention_days": 90,
                        "cutoff": "2026-07-14T13:30:00Z",
                        "affected_jobs": 2,
                        "job_ids": ["job_a1", "job_b2"],
                        "has_more": False,
                        "uploads": {"considered": 1, "deleted": 0, "failed": 0, "has_more": False, "dry_run": True},
                    },
                }
            },
        },
        400: {
            "description": (
                "El motivo de una ejecución real falta o no tiene entre 10 y "
                "500 caracteres. Añada un motivo descriptivo de esa longitud; "
                "en una previsualización puede dejarlo vacío."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "motivo_obligatorio": {"value": {"detail": "motivo obligatorio"}},
                        "motivo_corto": {"value": {"detail": "motivo debe tener entre 10 y 500 caracteres"}},
                    }
                }
            },
        },
        422: {
            "description": (
                "El cuerpo no es válido: retention_days debe estar entre 1 y "
                "3650, batch_size entre 1 y 1000, y dry_run debe ser booleano. "
                "Corrija los campos señalados y vuelva a enviar el cuerpo."
            ),
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {"loc": ["body", "retention_days"], "msg": "Input should be greater than or equal to 1", "type": "greater_than_equal"}
                        ]
                    }
                }
            },
        },
        **_AUTH_RESPONSES,
        **_SERVICE_UNAVAILABLE,
    },
)
@router.post(
    "/tables/purge-consulta-logs",
    summary="Previsualizar o ejecutar la purga por antigüedad (ruta compatible)",
    description=(
        "Realiza la misma previsualización o purga de jobs terminales que "
        "POST /retention/purge. Por defecto no elimina datos; la eliminación "
        "requiere dry_run=false y un motivo de entre 10 y 500 caracteres. "
        "También limpia objetos del bucket de cargas vencidos según "
        "UPLOADS_RETENTION_SECONDS (24 horas por defecto); los errores se "
        "informan en uploads.failed y no bloquean la purga de jobs. Requiere "
        "autorización administrativa Bearer."
    ),
    responses={
        200: {
            "description": "Resultado de la previsualización o eliminación, con corte e identificadores incluidos.",
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": {
                        "success": True,
                        "dry_run": True,
                        "retention_days": 90,
                        "cutoff": "2026-07-14T13:30:00Z",
                        "affected_jobs": 2,
                        "job_ids": ["job_a1", "job_b2"],
                        "has_more": False,
                        "uploads": {"considered": 1, "deleted": 0, "failed": 0, "has_more": False, "dry_run": True},
                    },
                }
            },
        },
        400: {
            "description": "El motivo de la ejecución no cumple la longitud requerida. Incluya un motivo de 10 a 500 caracteres o use dry_run=true para previsualizar.",
            "content": {"application/json": {"example": {"detail": "motivo obligatorio"}}},
        },
        422: {
            "description": "El cuerpo de entrada falta o contiene valores fuera de rango o de tipo incorrecto. Corrija los campos señalados y vuelva a enviar la solicitud.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": [
                            {"loc": ["body", "batch_size"], "msg": "Input should be less than or equal to 1000", "type": "less_than_equal"}
                        ]
                    }
                }
            },
        },
        **_AUTH_RESPONSES,
        **_SERVICE_UNAVAILABLE,
    },
)
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
    try:
        uploads_cleanup = await _cleanup_expired_uploads(body.batch_size, dry_run=body.dry_run)
    except Exception:
        # La limpieza de archivos es complementaria. Un fallo al consultar o
        # borrar objetos nunca revierte ni bloquea la retención de jobs.
        uploads_cleanup = {
            "considered": 0,
            "deleted": 0,
            "failed": 1,
            "has_more": False,
            "dry_run": body.dry_run,
            "error_code": "cleanup_unavailable",
        }
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
            "expired_upload_objects": uploads_cleanup["considered"],
            "upload_objects_deleted": uploads_cleanup["deleted"],
            "upload_cleanup_failures": uploads_cleanup["failed"],
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
        "uploads": uploads_cleanup,
    }
