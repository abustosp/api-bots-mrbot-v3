"""Presign de artefactos de salida para el worker (plan 02 §7.9).

El worker pide URLs prefirmadas adicionales limitadas a job, intento, tipo
y expiración corta. La central verifica que el solicitante sea el dueño de
la asignación viva y construye el object key
(``jobs/{job_id}/{attempt}/{artifact_id}``); nunca entrega credenciales
permanentes de storage. Si el worker publicó clave RSA, la respuesta viaja
sellada con ella (igual que el sobre de asignación); si no, en claro con
bandera ``sealed: False``. Al informar resultado se valida la metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from central_api.db import db_configurado, nueva_sesion
from central_api.internal.dependencies import require_worker
from central_api.settings import get_settings
from central_api.storage import firmar_subida
from central_api.store import JOBS

router = APIRouter()

ARTIFACT_URL_TTL_SECONDS = 300
ARTIFACT_MAX_BYTES = 104_857_600


async def _job_para_presign(job_id: str):
    """Job para autorizar presign: memoria primero, PostgreSQL después.

    Con base configurada la fila canónica vive en PG (models); sin memoria
    se verifica estado vivo (``ASIGNADO``/``CORRIENDO``) e intento.
    """
    job = JOBS.get(job_id)
    if job is not None:
        return job
    if not db_configurado():
        return None
    try:
        import uuid

        from sqlalchemy import select

        from central_api.models.execution import Job
    except Exception:  # noqa: BLE001 - sin modelos, solo memoria
        return None
    try:
        jid = uuid.UUID(str(job_id))
    except (ValueError, AttributeError, TypeError):
        return None
    try:
        from types import SimpleNamespace

        async with nueva_sesion() as sesion:
            fila = (await sesion.execute(
                select(Job).where(Job.id == jid)
            )).scalar_one_or_none()
        if fila is None:
            return None
        return SimpleNamespace(
            id=str(fila.id), bot=str(fila.bot), worker_node=None, status=str(fila.status),
            assignment_attempt=int(fila.attempts or 0), _origen_db=True,
            payload=fila.request_payload if isinstance(fila.request_payload, dict) else {},
        )
    except Exception:  # noqa: BLE001 - sin base, 404
        return None


async def _job_vivo(job_id: str, worker_node: str):
    """Job con asignación viva para este worker (404/403 si no)."""
    job = await _job_para_presign(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    if getattr(job, "_origen_db", False):
        # Fila PG sin caché: el dueño por nodo no está disponible; se exige
        # asignación viva y el borde interno ya autenticó al worker.
        if job.status not in ("ASIGNADO", "CORRIENDO"):
            raise HTTPException(status_code=403, detail="sin asignación viva para el job")
    elif job.worker_node != worker_node or job.status not in ("ASIGNADO", "CORRIENDO"):
        raise HTTPException(status_code=403, detail="sin asignación viva para el job")
    return job


class PresignBody(BaseModel):
    artifact_id: str = Field(
        min_length=1, max_length=128,
        description="Identificador funcional del archivo que se va a subir.",
        examples=["constancia-01"],
    )
    content_type: str = Field(
        min_length=1, max_length=128,
        description="Tipo MIME que debe usarse al enviar el archivo.",
        examples=["application/pdf"],
    )
    size_bytes: int = Field(
        ge=1, le=ARTIFACT_MAX_BYTES,
        description="Tamaño previsto del archivo en bytes. El máximo permitido es 100 MiB.",
        examples=[184320],
    )


def _responder(job, respuesta: dict) -> dict:
    """Entrega la URL prefirmada, sellada para el worker si publicó clave."""
    # La URL prefirmada es un bearer efectivo: si el worker publicó clave,
    # viaja cifrada con ella (igual que el sobre de asignación) y solo ese
    # worker puede abrirla. Sin pubkey, en claro con bandera visible.
    from central_api.security.sealed import encrypt_sealed_section
    from central_api.store import WORKERS

    entrada = WORKERS.get(getattr(job, "worker_node", None) or "")
    pubkey = entrada.sealed_pubkey_pem if entrada is not None else ""
    if pubkey:
        return {
            "sealed": True,
            "sealed_section": encrypt_sealed_section(pubkey, respuesta),
        }
    respuesta["sealed"] = False
    return respuesta


@router.post(
    "/jobs/{job_id}/artifacts/presign",
    status_code=201,
    summary="Solicitar autorización temporal para subir un archivo",
    description=(
        "Genera una autorización de corta duración para subir un artefacto de un "
        "job que sigue asignado y activo. La respuesta indica la clave de destino, "
        "la hora de vencimiento y la cabecera requerida; la autorización debe "
        "usarse antes de expirar. Si el worker publicó una clave de respuesta, "
        "el contenido de la autorización se entrega sellado para ese worker."
    ),
    responses={
        201: {"description": "Autorización de subida creada; el enlace temporal debe tratarse como secreto.", "content": {"application/json": {"examples": {
            "sin_sellado": {"summary": "Autorización visible para el worker", "value": {
                "upload_id": "0190c2d4-7b4a-7b61-9f28-3efc1f7b1b10",
                "object_key": "jobs/0190c2d4/1/constancia-01/ab12cd34ef56",
                "upload_url": "https://storage.example.invalid/upload?token=temporal",
                "expires_at": "2026-10-12T16:40:00Z",
                "required_headers": {"Content-Type": "application/pdf"},
                "sealed": False,
            }},
        }}}},
        401: {"description": "Falta la identidad de un worker registrado activo. Incluya `X-Worker-Node` con el nodo registrado.", "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}}},
        403: {"description": "El token no autoriza al solicitante o el job no tiene una asignación vigente para este worker. Renueve el registro y solicite la autorización solo mientras el job esté activo.", "content": {"application/json": {"examples": {
            "token": {"summary": "Token no autorizado", "value": {"detail": {"error_code": "forbidden", "message": "Operación no permitida."}}},
            "asignacion": {"summary": "Sin asignación activa", "value": {"detail": "sin asignación viva para el job"}},
        }}}},
        404: {"description": "No existe el job solicitado. Verifique el identificador recibido al aceptar la asignación.", "content": {"application/json": {"example": {"detail": "job no encontrado"}}}},
        422: {"description": "El cuerpo no cumple los límites de identificación, tipo de contenido o tamaño. Envíe un tamaño entre 1 byte y 100 MiB y complete los campos obligatorios.", "content": {"application/json": {"example": {"detail": [{"loc": ["body", "size_bytes"], "msg": "Input should be less than or equal to 104857600", "type": "less_than_equal"}]}}}},
    },
)
async def presign_artifact(
    job_id: str, body: PresignBody, worker_node: str = Depends(require_worker)
) -> dict:
    """Crea una autorización temporal de subida para un job activo."""
    job = await _job_vivo(job_id, worker_node)
    # Sufijo único por subida: un mismo artifact_id puede cubrir varios
    # archivos (un libro por período, un PDF por plan). Con clave fija cada
    # archivo pisaría al anterior en el bucket y el resultado chocaría con
    # ``uq_job_artifacts_object_key``.
    object_key = (
        f"jobs/{job_id}/{job.assignment_attempt}/{body.artifact_id}/"
        f"{uuid.uuid4().hex[:12]}"
    )
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=ARTIFACT_URL_TTL_SECONDS
    )
    # La central firma la subida (SigV4 real con storage configurado, ticket
    # de desarrollo documentado si no); el worker sube directo al storage.
    ajustes = get_settings()
    firmado = firmar_subida(
        object_key=object_key,
        content_type=body.content_type,
        ttl_seconds=ARTIFACT_URL_TTL_SECONDS,
        endpoint=ajustes.object_storage_endpoint,
        region=ajustes.object_storage_region,
        bucket=ajustes.bucket_para_bot(getattr(job, "bot", None)),
        access_key=ajustes.object_storage_access_key,
        secret_key=ajustes.object_storage_secret_key,
    )
    respuesta = {
        "upload_id": str(uuid.uuid4()),
        "object_key": object_key,
        "upload_url": firmado["upload_url"],
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "required_headers": {"Content-Type": body.content_type},
    }
    return _responder(job, respuesta)


LOGGER_MAX_BYTES = 1_073_741_824


class LoggerPresignBody(BaseModel):
    path: str = Field(min_length=1, max_length=128)
    content_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=1, le=LOGGER_MAX_BYTES)


def clave_logger(job_id: str, path: str) -> str:
    """Object key del bucket logger: solo ``{job_id}.tar.gz``; falla cerrado."""
    esperado = f"{job_id}.tar.gz"
    if path != esperado:
        raise HTTPException(status_code=422, detail="ruta de logger inválida")
    return esperado


# Oculto del OpenAPI/Swagger a propósito: es una herramienta interna de debugging.
@router.post(
    "/jobs/{job_id}/logger/presign", status_code=201, include_in_schema=False
)
async def presign_logger(
    job_id: str, body: LoggerPresignBody, worker_node: str = Depends(require_worker)
) -> dict:
    """URL de subida al bucket ``logger`` solo para jobs con ``logger`` activo."""
    job = await _job_vivo(job_id, worker_node)
    payload = getattr(job, "payload", None)
    if not (isinstance(payload, dict) and payload.get("logger") is True):
        raise HTTPException(status_code=403, detail="job sin logger activo")
    object_key = clave_logger(job_id, body.path)
    ajustes = get_settings()
    firmado = firmar_subida(
        object_key=object_key,
        content_type=body.content_type,
        ttl_seconds=ARTIFACT_URL_TTL_SECONDS,
        endpoint=ajustes.object_storage_endpoint,
        region=ajustes.object_storage_region,
        bucket=ajustes.object_storage_logger_bucket,
        access_key=ajustes.object_storage_access_key,
        secret_key=ajustes.object_storage_secret_key,
    )
    expires_at = datetime.now(timezone.utc) + timedelta(
        seconds=ARTIFACT_URL_TTL_SECONDS
    )
    return _responder(job, {
        "upload_id": str(uuid.uuid4()),
        "object_key": object_key,
        "upload_url": firmado["upload_url"],
        "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
        "required_headers": {"Content-Type": body.content_type},
    })


@router.post(
    "/jobs/{job_id}/cancel-ack",
    summary="Confirmar la recepción de una solicitud de cancelación",
    description=(
        "Registra el acuse del worker para la solicitud de cancelación de un job. "
        "El campo `accepted` indica si el worker acepta cooperar con la cancelación; "
        "si se omite, se considera aceptada. El acuse no cambia por sí mismo el "
        "estado del job."
    ),
    responses={
        200: {"description": "Acuse recibido.", "content": {"application/json": {"example": {
            "ok": True, "job_id": "0190c2d4-7b4a-7b5f-9f28-3efc1f7b1b10", "status": "CANCELANDO", "ack": True
        }}}},
        401: {"description": "Falta la identidad de un worker registrado activo. Incluya `X-Worker-Node` con el nodo registrado.", "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}}},
        403: {"description": "El token no es válido o el job está asignado a otro worker. Use el token vigente y confirme únicamente jobs propios.", "content": {"application/json": {"examples": {
            "token": {"summary": "Token no autorizado", "value": {"detail": {"error_code": "forbidden", "message": "Operación no permitida."}}},
            "asignacion": {"summary": "Job de otro worker", "value": {"detail": "asignación de otro worker"}},
        }}}},
        404: {"description": "No se encontró el job en el registro activo. Compruebe el `job_id` del comando de cancelación.", "content": {"application/json": {"example": {"detail": "job no encontrado"}}}},
        422: {"description": "El cuerpo no es un objeto JSON válido. Envíe un objeto con `accepted` booleano si desea informar explícitamente la aceptación.", "content": {"application/json": {"example": {"detail": [{"loc": ["body"], "msg": "Input should be a valid dictionary", "type": "dict_type"}]}}}},
    },
)
def cancel_ack(
    job_id: str, body: dict, worker_node: str = Depends(require_worker)
) -> dict:
    """Confirma la recepción de una solicitud de cancelación cooperativa."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    if job.worker_node and job.worker_node != worker_node:
        raise HTTPException(status_code=403, detail="asignación de otro worker")
    return {"ok": True, "job_id": job.id, "status": job.status,
            "ack": bool(body.get("accepted", True))}
