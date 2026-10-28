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
from datetime import datetime
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


def _openapi_error(
    description: str,
    error_code: str,
    message: str,
    *,
    headers: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construye la documentación común de un error público sanitizado."""
    response = {
        "description": description,
        "content": {
            "application/json": {
                "example": {
                    "detail": {
                        "error_code": error_code,
                        "message": message,
                    }
                }
            }
        },
    }
    if headers:
        response["headers"] = headers
    return response


ARTIFACT_URL_TTL_SECONDS = 3600


def _url_directa(object_key: str | None, filename: str | None = None) -> str | None:
    """URL temporal de descarga directa al almacenamiento, o ``None``.

    Devuelve ``None`` si el almacenamiento no está configurado: el cliente
    puede usar siempre ``download_url``, que genera el enlace al pedirlo.
    """
    from central_api.settings import get_settings
    from central_api.storage import presign_get_url

    if not object_key:
        return None
    cfg = get_settings()
    try:
        return presign_get_url(
            endpoint=cfg.object_storage_public_endpoint or cfg.object_storage_endpoint,
            region=cfg.object_storage_region,
            bucket=cfg.object_storage_bucket,
            access_key=cfg.object_storage_access_key,
            secret_key=cfg.object_storage_secret_key,
            object_key=str(object_key),
            expires_seconds=ARTIFACT_URL_TTL_SECONDS,
            filename=filename,
        )
    except ValueError:
        return None


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
            "name": item.filename,
            "size": item.size_bytes,
            "artifact_id": str(item.id),
            "filename": item.filename,
            "content_type": item.content_type,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "download_url": (
                f"/api/v3/jobs/{jid}/artifacts/{item.id}/download"
            ),
            "url": (direct_url := _url_directa(
                getattr(item, "object_key", None), item.filename
            )),
            "url_expires_in": ARTIFACT_URL_TTL_SECONDS if direct_url else None,
        }
        for item in artefactos
        if item.expires_at is None or item.expires_at > ahora
    ]
    payload = resultado.payload if resultado is not None else None
    if isinstance(payload, dict):
        # Mis Comprobantes con carga_json: el JSON no está en la base, se
        # reconstruye desde el ZIP guardado en S4 y no se persiste.
        from central_api import mc_json

        payload = await mc_json.reconstruir(
            bot=fila.bot,
            operacion=fila.operation,
            payload=getattr(fila, "request_payload", None),
            datos=payload,
            artefactos=[
                {"object_key": getattr(a, "object_key", None)} for a in artefactos
            ],
        )
        payload = {"schema_version": 1, **payload}
    summary = resultado.summary if resultado is not None else {}
    result = {
        "result": resultado.result if resultado is not None else None,
        "error": (summary or {}).get("error") or getattr(fila, "error_message", None),
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
            "data": payload,
            "error": (summary or {}).get("error") or getattr(fila, "error_message", None),
        },
        _creado=creado,
    )

TERMINAL = ("COMPLETO", "FALLIDO", "CANCELADO")


class CancelBody(BaseModel):
    motivo: str = Field(
        default="",
        max_length=500,
        description="Motivo opcional que quedará asociado a la solicitud de cancelación.",
        examples=["El usuario ya no necesita el resultado"],
    )


class BatchBody(BaseModel):
    job_ids: list[str] = Field(
        default_factory=list,
        description="Identificadores de jobs a consultar; se permiten hasta 200 por solicitud.",
        examples=[["0190f0c0-7f5b-7b2e-9f7e-123456789abc"]],
    )


class JobArtifactResponse(BaseModel):
    """Archivo con sus campos V2 y la metadata de descarga añadida en V3."""

    name: str = Field(
        ..., description="Nombre original del archivo, conservado para clientes V2.",
        examples=["5 - MCE - 01012024 - 31012024 - 20123 - Empresa.zip"],
    )
    url: str = Field(
        default="",
        description=(
            "URL prefirmada directa al archivo. Conserva el campo V2, vence a los "
            "3600 segundos indicados por `url_expires_in` y entrega el nombre original "
            "en Content-Disposition. Si no hay URL directa, conserva el tipo string de V2 y devuelve `url` vacío."
        ),
        examples=["https://archivos.ejemplo/archivo?X-Amz-Expires=3600&X-Amz-Signature=...", ""],
    )
    size: int | None = Field(
        default=None, description="Tamaño del archivo en bytes, conservado para clientes V2.",
        examples=[20480, None],
    )
    artifact_id: str | None = Field(
        default=None, description="Identificador V3 del archivo generado.",
        examples=["0190f0c0-7f5b-7b2e-9f7e-123456789abd", None],
    )
    filename: str = Field(
        ..., description="Nombre original del archivo, también disponible como `name`.",
        examples=["comprobantes-2025.zip"],
    )
    content_type: str | None = Field(
        default=None, description="Tipo de contenido del archivo.", examples=["application/zip", None]
    )
    size_bytes: int | None = Field(
        default=None, description="Tamaño del archivo en bytes, también disponible como `size`.",
        examples=[20480, None],
    )
    sha256: str | None = Field(
        default=None, description="Huella SHA-256 del contenido del archivo.",
        examples=["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef", None],
    )
    download_url: str | None = Field(
        default=None,
        description=(
            "Ruta autenticada para solicitar una redirección a una URL temporal nueva. "
            "Úsela si la URL directa `url` venció."
        ),
        examples=["/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download", None],
    )
    url_expires_in: int | None = Field(
        default=None, description="Vigencia de `url` en segundos: 3600 segundos.",
        examples=[3600, None],
    )


class JobErrorDetail(BaseModel):
    """Detalle estructurado y sanitizado de un error del job."""

    error_code: str = Field(..., description="Clasificación pública del error.", examples=["authentication"])
    cause: str = Field(..., description="Causa explicada en lenguaje claro, sin datos internos.", examples=["Los datos de acceso no fueron aceptados."])
    message: str = Field(..., description="Acción recomendada para resolver o reintentar.", examples=["Revise las credenciales de la cuenta e inténtelo nuevamente."])
    correlation_id: str | None = Field(default=None, description="Identificador de seguimiento, si está disponible.", examples=["corr-0190f0c0", None])


_EXAMPLE_JOB_ID = "0190f0c0-7f5b-7b2e-9f7e-123456789abc"
_EXAMPLE_JOB_FILE = {
    "name": "5 - MCE - 01012024 - 31012024 - 20123 - Empresa.zip",
    "url": "https://archivos.ejemplo/archivo?X-Amz-Expires=3600&X-Amz-Signature=...",
    "size": 20480,
    "artifact_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abd",
    "filename": "5 - MCE - 01012024 - 31012024 - 20123 - Empresa.zip",
    "content_type": "application/zip",
    "size_bytes": 20480,
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "download_url": f"/api/v3/jobs/{_EXAMPLE_JOB_ID}/artifacts/0190f0c0-7f5b-7b2e-9f7e-123456789abd/download",
    "url_expires_in": 3600,
}


def _status_example(
    status: str, status_detail: str, result: str | None = None,
    *, error: list[str] | None = None, error_detail: dict | None = None,
    files: list[dict] | None = None, cancel_reason: str | None = None,
    cancelled_by: str | None = None,
) -> dict[str, Any]:
    return {
        "job_id": _EXAMPLE_JOB_ID,
        "status": status,
        "status_detail": status_detail,
        "result": result,
        "bot": "mis_comprobantes",
        "operation": "consultar",
        "operation_legacy": "consulta",
        "created_at": "2026-09-26T04:00:00Z",
        "started_at": None if status_detail in ("PENDIENTE", "ASIGNADO") else "2026-09-26T04:00:02Z",
        "finished_at": "2026-09-26T04:00:12Z" if result in ("OK", "PARCIAL", "ERROR") or status_detail == "CANCELADO" else None,
        "cancel_reason": cancel_reason,
        "cancelled_by": cancelled_by,
        "error": error,
        "error_detail": error_detail,
        "files": files or [],
        "data": {"items": []} if result in ("OK", "PARCIAL") else None,
    }


JOB_STATUS_EXAMPLES: dict[str, dict[str, Any]] = {
    "pendiente": _status_example("PENDIENTE", "PENDIENTE"),
    "asignado": _status_example("PENDIENTE", "ASIGNADO"),
    "corriendo": _status_example("CORRIENDO", "CORRIENDO"),
    "completo_ok": _status_example("COMPLETO", "COMPLETO", "OK", files=[_EXAMPLE_JOB_FILE]),
    "completo_parcial": _status_example(
        "COMPLETO", "COMPLETO", "PARCIAL",
        error=["Parte de la información no pudo procesarse. Revise los datos y vuelva a intentarlo."],
        error_detail={"error_code": "processing", "cause": "Parte de la información no pudo procesarse.", "message": "Revise los datos enviados e inténtelo nuevamente."},
    ),
    "completo_error": _status_example(
        "COMPLETO", "FALLIDO", "ERROR",
        error=["Revise las credenciales de la cuenta e inténtelo nuevamente."],
        error_detail={"error_code": "authentication", "cause": "Los datos de acceso no fueron aceptados.", "message": "Revise las credenciales de la cuenta e inténtelo nuevamente.", "correlation_id": "corr-0190f0c0"},
    ),
    "cancelado": _status_example(
        "CANCELADO", "CANCELADO", cancel_reason="Cancelado por el usuario.", cancelled_by="user"
    ),
}


class JobStatusResponse(BaseModel):
    """Estado con campos V2 sin cambios semánticos y metadata adicional V3."""

    job_id: str = Field(..., description="Identificador estable del job.", examples=[_EXAMPLE_JOB_ID])
    status: str = Field(
        ..., description="Estado compatible con V2: PENDIENTE, CORRIENDO, COMPLETO o CANCELADO. Los fallos son COMPLETO con result=ERROR.",
        examples=["PENDIENTE", "CORRIENDO", "COMPLETO", "CANCELADO"],
    )
    status_detail: str = Field(
        ..., description="Estado real V3: PENDIENTE, ASIGNADO, CORRIENDO, COMPLETO, FALLIDO o CANCELADO.",
        examples=["PENDIENTE", "ASIGNADO", "CORRIENDO", "COMPLETO", "FALLIDO", "CANCELADO"],
    )
    result: str | None = Field(
        default=None, description="Resultado final: OK, PARCIAL o ERROR. Un fallo V3 se expresa como COMPLETO + ERROR.",
        examples=["OK", "PARCIAL", "ERROR", None],
    )
    bot: str = Field(..., description="Nombre del bot que procesa el job.", examples=["mis_comprobantes"])
    operation: str = Field(
        ..., description="Operación canónica en infinitivo, igual a la publicada por el catálogo V3.",
        examples=["consultar"],
    )
    operation_legacy: str | None = Field(
        default=None, description="Nombre histórico V2 de la operación; se incluye solo cuando difiere del nombre canónico.",
        examples=["consulta", None],
    )
    created_at: datetime = Field(..., description="Fecha de creación en ISO-8601 UTC.", examples=["2026-09-26T04:00:00Z"])
    started_at: datetime | None = Field(default=None, description="Fecha de inicio en ISO-8601 UTC, si comenzó.", examples=["2026-09-26T04:00:02Z", None])
    finished_at: datetime | None = Field(default=None, description="Fecha de finalización en ISO-8601 UTC; null si no hay una fecha persistida.", examples=["2026-09-26T04:00:12Z", None])
    cancel_reason: str | None = Field(default=None, description="Motivo informado al solicitar la cancelación.", examples=["Cancelado por el usuario.", None])
    cancelled_by: str | None = Field(default=None, description="Origen de la cancelación, por ejemplo user, admin o system_restart.", examples=["user", "admin", "system_restart", None])
    error: list[str] | str | None = Field(
        default=None, description="Mensajes legibles en español; null si el resultado es OK.",
        examples=[["Revise las credenciales de la cuenta e inténtelo nuevamente."], None],
    )
    error_detail: JobErrorDetail | None = Field(default=None, description="Detalle adicional del error, sanitizado y accionable.")
    files: list[JobArtifactResponse] = Field(default_factory=list, description="Lista de archivos, vacía si no hay archivos disponibles.", examples=[[], [_EXAMPLE_JOB_FILE]])
    data: Any = Field(default=None, description="Resultado específico del bot: objeto, lista o null.", examples=[{"items": []}, [], None])

    model_config = ConfigDict(json_schema_extra={
        "example": JOB_STATUS_EXAMPLES["completo_ok"],
        "examples": list(JOB_STATUS_EXAMPLES.values()),
    })


class BatchJobNotFoundResponse(BaseModel):
    """Error individual de batch con el valor V2 y el detalle estructurado V3."""

    job_id: str = Field(..., description="Identificador solicitado.", examples=[_EXAMPLE_JOB_ID])
    error: str = Field(
        ..., description="Error compatible con V2 para un job inexistente o no visible.",
        examples=["not_found"],
    )
    error_detail: JobErrorDetail = Field(
        ..., description="Explicación en español y recomendación para el cliente.",
        examples=[{
            "error_code": "not_found",
            "cause": "El job no existe o no está disponible para esta cuenta.",
            "message": "Compruebe el identificador y la cuenta utilizada.",
        }],
    )


class BatchStatusResponse(BaseModel):
    """Resultados de batch, en el orden solicitado, con proyección completa por job."""

    items: list[JobStatusResponse | BatchJobNotFoundResponse] = Field(
        ..., description="Estado completo por job o error individual si no es visible."
    )


class JobListSummary(BaseModel):
    """Resumen V3 histórico conservado en jobs, más estado detallado V3."""

    job_id: str = Field(..., description="Identificador estable del job.", examples=[_EXAMPLE_JOB_ID])
    status: str = Field(..., description="Estado compatible con V2.", examples=["PENDIENTE", "CORRIENDO", "COMPLETO", "CANCELADO"])
    status_detail: str = Field(..., description="Estado interno detallado de V3.", examples=["PENDIENTE", "ASIGNADO", "CORRIENDO", "COMPLETO", "FALLIDO", "CANCELADO"])
    bot: str = Field(..., description="Bot que procesa el job.", examples=["mis_comprobantes"])
    operation: str = Field(..., description="Verbo canónico de la operación.", examples=["consultar"])
    operacion: str = Field(..., description="Alias histórico conservado en el resumen de V3.", examples=["consultar"])
    operation_legacy: str | None = Field(default=None, description="Nombre V2 de la operación si difiere del canónico.", examples=["consulta", None])


class JobListResponse(BaseModel):
    """Página de resultados con resumen legado y el objeto íntegro por job."""

    success: bool = Field(..., description="Indica que el listado fue generado.", examples=[True])
    jobs: list[JobListSummary] = Field(..., description="Resúmenes de jobs, con claves históricas y estados V2/V3.")
    items: list[JobStatusResponse] = Field(..., description="Objetos completos con todas las claves V2 y metadata V3.")
    next_cursor: str | None = Field(default=None, description="Cursor opaco para la próxima página, o null si terminó.", examples=[None, "eyJ2IjoxLCJqb2JfaWQiOiIwMTkwZjBjMC03ZjViLTdiMmUtOWY3ZS0xMjM0NTY3ODlhYmMifQ=="])
    has_more: bool = Field(..., description="Indica si hay otra página disponible.", examples=[False, True])


class JobQueueItem(BaseModel):
    """Job activo de la cola con operation canónica y estado legado."""

    job_id: str = Field(..., description="Identificador estable del job.", examples=[_EXAMPLE_JOB_ID])
    status: str = Field(..., description="Estado compatible con V2.", examples=["PENDIENTE", "CORRIENDO"])
    status_detail: str = Field(..., description="Estado interno detallado de V3.", examples=["PENDIENTE", "ASIGNADO", "CORRIENDO"])
    bot: str = Field(..., description="Bot que procesa el job.", examples=["mis_comprobantes"])
    operation: str = Field(..., description="Verbo canónico de la operación.", examples=["consultar"])
    operacion: str = Field(..., description="Alias histórico conservado en el resumen de V3.", examples=["consultar"])
    position: int | None = Field(default=None, description="Posición actual si el job está pendiente.", examples=[2, None])
    queue_position: int | None = Field(default=None, description="Alias de posición conservado por V3.", examples=[2, None])
    operation_legacy: str | None = Field(default=None, description="Nombre V2 si difiere del canónico.", examples=["consulta", None])


class JobQueueResponse(BaseModel):
    """Jobs activos visibles para la cuenta y su posición en cola."""

    success: bool = Field(..., description="Indica que la cola fue consultada.", examples=[True])
    jobs: list[JobQueueItem] = Field(..., description="Jobs activos de la cuenta autenticada.")
    items: list[JobQueueItem] = Field(..., description="Alias de jobs conservado por V3.")
    count: int = Field(..., ge=0, description="Cantidad de jobs devueltos.", examples=[1])


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
    public = project_job(job)
    item = {
        "job_id": public["job_id"],
        "status": public["status"],
        "status_detail": public["status_detail"],
        "bot": public["bot"],
        "operation": public["operation"],
        "operacion": public["operation"],
        "position": position,
        "queue_position": position,
    }
    if "operation_legacy" in public:
        item["operation_legacy"] = public["operation_legacy"]
    return item


def _job_list_summary(public: dict[str, Any]) -> dict[str, Any]:
    """Resumen de listado que conserva nombres V3 existentes y los armoniza."""
    summary = {
        "job_id": public["job_id"],
        "status": public["status"],
        "status_detail": public["status_detail"],
        "bot": public["bot"],
        "operation": public["operation"],
        "operacion": public["operation"],
    }
    if "operation_legacy" in public:
        summary["operation_legacy"] = public["operation_legacy"]
    return summary


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
    response_model=JobQueueResponse,
    summary="Listar jobs activos propios y su posición en cola",
    description=(
        "Devuelve los jobs activos de la cuenta autenticada. Puede filtrar por "
        "bot y estado; los jobs pendientes incluyen su posición actual. Un "
        "estado no reconocido produce una lista vacía."
    ),
    responses={
        200: {
            "description": "Lista de jobs activos y cantidad devuelta.",
            "content": {"application/json": {"example": {
                "success": True,
                "jobs": [{"job_id": _EXAMPLE_JOB_ID, "status": "PENDIENTE", "status_detail": "ASIGNADO", "bot": "mis_comprobantes", "operation": "consultar", "operacion": "consultar", "operation_legacy": "consulta", "position": 2, "queue_position": 2}],
                "items": [{"job_id": _EXAMPLE_JOB_ID, "status": "PENDIENTE", "status_detail": "ASIGNADO", "bot": "mis_comprobantes", "operation": "consultar", "operacion": "consultar", "operation_legacy": "consulta", "position": 2, "queue_position": 2}],
                "count": 1,
            }}},
        },
        401: _openapi_error("La clave de acceso falta, no es válida, venció o pertenece a una cuenta deshabilitada. Envíe una clave activa con el método de autenticación admitido.", "authentication", "Credenciales inválidas."),
        503: _openapi_error(
            "No se pudo consultar la cola porque el servicio de datos no está disponible. Espere el intervalo indicado y reintente.",
            "service_not_enabled",
            "Servicio temporalmente no disponible.",
            headers={"Retry-After": {"description": "Segundos sugeridos antes de reintentar.", "schema": {"type": "string", "example": "3"}}},
        ),
    },
)
async def list_queue(
    bot: str | None = None,
    status: str | None = None,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """Lista jobs activos de la cuenta autenticada y su posición pendiente."""
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
    description=(
        "Devuelve todas las claves V2 con el mismo significado, además de los "
        "detalles V3. `status` usa PENDIENTE/CORRIENDO/COMPLETO/CANCELADO; "
        "`status_detail` muestra el estado real, incluido ASIGNADO o FALLIDO. "
        "Las operaciones se expresan con el nombre canónico y `operation_legacy` "
        "solo aparece si difiere. Los errores se presentan en español y con "
        "detalle sanitizado. Un job ajeno o inexistente se informa como no encontrado."
    ),
    responses={
        200: {
            "description": "Estado del job con las claves V2 y campos nuevos V3. Los ejemplos por estado están en JobStatusResponse.",
            "content": {"application/json": {"examples": {
                key: {"summary": key.replace("_", " ").capitalize(), "value": value}
                for key, value in JOB_STATUS_EXAMPLES.items()
            }}},
        },
        401: _openapi_error("La clave de acceso falta, no es válida, venció o pertenece a una cuenta deshabilitada. Envíe una clave activa con el método admitido.", "authentication", "Credenciales inválidas."),
        404: _openapi_error("No se encontró un job disponible con ese identificador. Compruebe que el ID esté escrito correctamente. Por privacidad, la respuesta también se usa para jobs que no son visibles para esta cuenta.", "not_found", "Recurso no encontrado."),
    },
)
async def get_job(
    job_id: str, principal: ApiPrincipal = Depends(require_api_principal)
) -> JSONResponse:
    """Consulta estado, resultado y archivos de un job propio."""
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

    from central_api.models.billing import BillingUsage
    from central_api.models.execution import Job

    try:
        parsed_id = uuid.UUID(str(job_id))
    except (ValueError, TypeError, AttributeError):
        return JSONResponse(status_code=404, content=public_error("not_found"))

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
                usage_id = (
                    await session.execute(
                        select(BillingUsage.id)
                        .where(BillingUsage.job_id == row.id)
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if usage_id is not None:
                    from central_api.billing.postgres import settle_in_session

                    await settle_in_session(
                        session,
                        row.id,
                        status="LIBERADO",
                        reason="cancelado en PENDIENTE",
                    )

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
    description=(
        "Solicita la cancelación del job y devuelve su estado actualizado. "
        "Un job pendiente se cancela de inmediato; si ya comenzó, la solicitud "
        "se procesa cuando sea posible. Los jobs terminales se devuelven sin "
        "cambios. El motivo es opcional y admite hasta 500 caracteres."
    ),
    responses={
        200: {"description": "Estado actual del job después de registrar la solicitud de cancelación."},
        401: _openapi_error("La clave de acceso falta, no es válida, venció o pertenece a una cuenta deshabilitada. Envíe una clave activa con el método admitido.", "authentication", "Credenciales inválidas."),
        404: _openapi_error("No se encontró un job disponible con ese identificador. Compruebe que el ID esté escrito correctamente. Por privacidad, la respuesta también se usa para jobs que no son visibles para esta cuenta.", "not_found", "Recurso no encontrado."),
        422: {
            "description": "El cuerpo JSON no tiene el formato esperado o el motivo supera 500 caracteres. Envíe un objeto JSON con un motivo de hasta 500 caracteres.",
            "content": {"application/json": {"example": {
                "detail": [{
                    "type": "string_too_long",
                    "loc": ["body", "motivo"],
                    "msg": "String should have at most 500 characters",
                    "input": "Motivo demasiado extenso",
                }]
            }}},
        },
        503: _openapi_error("No se pudo registrar la cancelación por indisponibilidad temporal del servicio de datos. Espere el intervalo indicado y reintente.", "service_not_enabled", "Servicio temporalmente no disponible.", headers={"Retry-After": {"description": "Segundos sugeridos antes de reintentar.", "schema": {"type": "string", "example": "3"}}}),
    },
)
async def cancel_job(
    job_id: str, body: CancelBody,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """Solicita la cancelación de un job y devuelve su estado resultante."""
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
    description=(
        "Alias REST para solicitar la cancelación de un job. Acepta un motivo "
        "opcional en JSON; si el cuerpo no es JSON o no contiene motivo, se "
        "utiliza el motivo predeterminado. Devuelve el estado actual del job."
    ),
    responses={
        200: {"description": "Estado actual del job después de procesar la solicitud."},
        401: _openapi_error("La clave de acceso falta, no es válida, venció o pertenece a una cuenta deshabilitada. Envíe una clave activa con el método admitido.", "authentication", "Credenciales inválidas."),
        404: _openapi_error("No se encontró un job disponible con ese identificador. Compruebe que el ID esté escrito correctamente. Por privacidad, la respuesta también se usa para jobs que no son visibles para esta cuenta.", "not_found", "Recurso no encontrado."),
        503: _openapi_error("No se pudo registrar la cancelación por indisponibilidad temporal del servicio de datos. Espere el intervalo indicado y reintente.", "service_not_enabled", "Servicio temporalmente no disponible.", headers={"Retry-After": {"description": "Segundos sugeridos antes de reintentar.", "schema": {"type": "string", "example": "3"}}}),
    },
)
async def delete_job(
    job_id: str,
    request: Request,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """Alias DELETE para solicitar la cancelación de un job."""
    motivo = "cancelado desde DELETE"
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            raw = await request.json()
        except (ValueError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            motivo = str(raw.get("motivo") or motivo)[:500]
    return await cancel_job(job_id, CancelBody(motivo=motivo), principal)


@router.post(
    "/jobs/estado:lote",
    response_model=BatchStatusResponse,
    summary="Consultar en lote el estado de jobs",
    description=(
        "Consulta hasta 200 identificadores en una sola solicitud. Para cada job, "
        "`status`, `result`, `operation`, `error` y `files` conservan el contrato V2; "
        "se agregan `status_detail`, `operation_legacy` cuando corresponda y `error_detail`. La respuesta "
        "mantiene el orden solicitado; cada elemento contiene el estado del job "
        "o `error=not_found` y `error_detail` si no existe o no es visible para esta cuenta."
    ),
    responses={
        200: {
            "description": "Resultado por identificador; los errores individuales no cambian el código HTTP. Incluye ejemplos de cada estado V2/V3.",
            "content": {"application/json": {"examples": {
                **{key: {
                    "summary": key.replace("_", " ").capitalize(),
                    "value": {"items": [value]},
                }
                for key, value in JOB_STATUS_EXAMPLES.items()},
                "not_found": {
                    "summary": "Job inexistente o no visible",
                    "value": {"items": [{
                        "job_id": _EXAMPLE_JOB_ID,
                        "error": "not_found",
                        "error_detail": {
                            "error_code": "not_found",
                            "cause": "El job no existe o no está disponible para esta cuenta.",
                            "message": "Compruebe el identificador y la cuenta utilizada.",
                        },
                    }]},
                },
            }}},
        },
        400: _openapi_error("La solicitud incluye más de 200 identificadores. Divida la consulta en grupos de hasta 200.", "validation", "Datos de entrada inválidos."),
        401: _openapi_error("La clave de acceso falta, no es válida, venció o pertenece a una cuenta deshabilitada. Envíe una clave activa con el método admitido.", "authentication", "Credenciales inválidas."),
        422: {
            "description": "El cuerpo falta o no contiene una lista job_ids válida. Envíe un objeto JSON con job_ids como lista de identificadores.",
            "content": {"application/json": {"example": {
                "detail": [{
                    "type": "list_type",
                    "loc": ["body", "job_ids"],
                    "msg": "Input should be a valid list",
                    "input": "no-es-una-lista",
                }]
            }}},
        },
    },
)
async def batch_status(
    body: BatchBody, principal: ApiPrincipal = Depends(require_api_principal)
) -> JSONResponse:
    """Devuelve el estado o el error individual de hasta 200 jobs."""
    _ = correlation_id  # la correlación viaja en middleware/respuesta global
    if len(body.job_ids) > 200:
        return JSONResponse(status_code=400, content=public_error("validation"))
    items: list[dict] = []
    for raw in body.job_ids:
        if db_configurado():
            job = await _visible_job_db(raw, principal)
            meta = getattr(job, "_meta", None) if job is not None else None
        else:
            job = JOBS.get(raw)
            meta = JOB_META.get(raw, {})
            if job is not None and not can_read_job(
                principal, str(meta.get("owner", principal.user_id))
            ):
                job = None
        if job is None:
            items.append({
                "job_id": raw,
                "error": "not_found",
                "error_detail": {
                    "error_code": "not_found",
                    "cause": "El job no existe o no está disponible para esta cuenta.",
                    "message": "Compruebe el identificador y la cuenta utilizada.",
                },
            })
            continue
        items.append(project_job(job, meta))
    return JSONResponse(status_code=200, content={"items": items})


def _encode_cursor(job_id: str) -> str:
    return base64.urlsafe_b64encode(
        json.dumps({"v": 1, "job_id": job_id}).encode("utf-8")
    ).decode("ascii")


def _decode_cursor(cursor: str) -> str | None:
    """Decodifica y valida el borde UUIDv7 opaco del listado."""
    try:
        import uuid

        value = json.loads(
            base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        )["job_id"]
        parsed = uuid.UUID(str(value))
        if parsed.version != 7:
            return None
        return str(parsed)
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return None


async def _list_jobs_db(
    principal: ApiPrincipal,
    *,
    status: str | None,
    bot: str | None,
    operacion: str | None,
    limit: int,
    cursor: str | None,
) -> tuple[list[dict[str, Any]], bool] | None:
    """Lista jobs propios desde PostgreSQL con el mismo cursor UUIDv7 que memoria.

    ``id DESC`` es el orden contractual. UUIDv7 hace que el predicado ``id <
    cursor`` sea estable para la paginación sin offsets, incluso cuando llegan
    nuevos jobs entre páginas.
    """
    if not db_configurado():
        return None
    try:
        import uuid

        from sqlalchemy import select

        from central_api.models.execution import Job

        owner = uuid.UUID(str(principal.user_id))
        border = uuid.UUID(_decode_cursor(cursor)) if cursor else None
        async with nueva_sesion() as session:
            statement = select(Job).where(Job.user_id == owner)
            if status:
                statement = statement.where(Job.status == status)
            if bot:
                statement = statement.where(Job.bot == bot)
            if operacion:
                statement = statement.where(Job.operation == operacion)
            if border is not None:
                statement = statement.where(Job.id < border)
            rows = (
                await session.execute(statement.order_by(Job.id.desc()).limit(limit + 1))
            ).scalars().all()
    except Exception:
        # DATABASE_URL es fuente canónica: no sustituir un error de base por un
        # store de proceso que podría omitir jobs creados por otra instancia.
        return None

    has_more = len(rows) > limit
    page = rows[:limit]
    items: list[dict[str, Any]] = []
    for row in page:
        # Use the same scoped artifact/result hydration as GET /jobs/{job_id};
        # list responses therefore keep the V2 meaning of ``data`` and ``files``.
        hydrated = await _visible_job_db(str(row.id), principal)
        if hydrated is None:
            return None
        items.append(project_job(hydrated, getattr(hydrated, "_meta", None)))
    return items, has_more


@router.get(
    "/jobs",
    response_model=JobListResponse,
    summary="Listar jobs propios",
    description=(
        "Devuelve una página de jobs de la cuenta autenticada, ordenados del "
        "más reciente al más antiguo. Permite filtrar por estado, bot y "
        "operación. Use next_cursor para solicitar la página siguiente; limit "
        "se ajusta al intervalo de 1 a 100."
    ),
    responses={
        200: {
            "description": "Página de resultados con cursor opaco y señal de páginas restantes. Cada elemento completo mantiene los campos V2 y añade el detalle V3.",
            "content": {"application/json": {"examples": {
                **{key: {
                    "summary": key.replace("_", " ").capitalize(),
                    "value": {
                        "success": True,
                        "jobs": [_job_list_summary(value)],
                        "items": [value],
                        "next_cursor": None,
                        "has_more": False,
                    },
                }
                for key, value in JOB_STATUS_EXAMPLES.items()},
                "sin_resultados": {
                    "summary": "Listado vacío",
                    "value": {
                        "success": True, "jobs": [], "items": [],
                        "next_cursor": None, "has_more": False,
                    },
                },
            }}},
        },
        400: _openapi_error("El cursor está mal formado o no representa un identificador de página válido. Use exactamente el next_cursor devuelto por la página anterior; si no dispone de uno, omita cursor.", "validation", "Datos de entrada inválidos."),
        401: _openapi_error("La clave de acceso falta, no es válida, venció o pertenece a una cuenta deshabilitada. Envíe una clave activa con el método admitido.", "authentication", "Credenciales inválidas."),
        422: {
            "description": "Un parámetro no tiene el tipo esperado, por ejemplo limit no es un entero. Corrija los parámetros de consulta.",
            "content": {"application/json": {"example": {
                "detail": [{
                    "type": "int_parsing",
                    "loc": ["query", "limit"],
                    "msg": "Input should be a valid integer",
                    "input": "muchos",
                }]
            }}},
        },
        503: _openapi_error("El servicio no pudo obtener la lista actualizada de jobs. Reintente más tarde sin sustituir los resultados por una lista parcial.", "unavailable", "Ocurrió un error interno."),
    },
)
async def list_jobs(
    status: str | None = None,
    bot: str | None = None,
    operacion: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """Lista jobs propios con filtros opcionales y paginación por cursor."""
    limit = max(1, min(100, limit))
    if cursor and _decode_cursor(cursor) is None:
        return JSONResponse(status_code=400, content=public_error("validation"))
    db_page = await _list_jobs_db(
        principal,
        status=status,
        bot=bot,
        operacion=operacion,
        limit=limit,
        cursor=cursor,
    )
    if db_configurado() and db_page is not None:
        items, has_more = db_page
        return JSONResponse(
            status_code=200,
            content={
                "success": True,
                "jobs": [_job_list_summary(item) for item in items],
                "items": items,
                "next_cursor": _encode_cursor(items[-1]["job_id"])
                if has_more and items else None,
                "has_more": has_more,
            },
        )
    if db_configurado():
        return JSONResponse(status_code=503, content=public_error("unavailable"))
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
        border = _decode_cursor(cursor)
        owned = [j for j in owned if j.id < border]
    page = owned[:limit]
    rest = owned[limit:]
    return JSONResponse(
        status_code=200,
        content={
            "success": True,
            "jobs": [_job_list_summary(project_job(j)) for j in page],
            "items": [project_job(j) for j in page],
            "next_cursor": _encode_cursor(page[-1].id) if rest and page else None,
            "has_more": bool(rest),
        },
    )
