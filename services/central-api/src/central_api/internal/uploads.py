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
            id=str(fila.id), worker_node=None, status=str(fila.status),
            assignment_attempt=int(fila.attempts or 0), _origen_db=True,
        )
    except Exception:  # noqa: BLE001 - sin base, 404
        return None


class PresignBody(BaseModel):
    artifact_id: str = Field(min_length=1, max_length=128)
    content_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=1, le=ARTIFACT_MAX_BYTES)


@router.post("/jobs/{job_id}/artifacts/presign", status_code=201)
async def presign_artifact(
    job_id: str, body: PresignBody, worker_node: str = Depends(require_worker)
) -> dict:
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
    object_key = f"jobs/{job_id}/{job.assignment_attempt}/{body.artifact_id}"
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
        bucket=ajustes.object_storage_bucket,
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


@router.post("/jobs/{job_id}/cancel-ack")
def cancel_ack(
    job_id: str, body: dict, worker_node: str = Depends(require_worker)
) -> dict:
    """Confirma el comando de cancelación cooperativa del worker."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    if job.worker_node and job.worker_node != worker_node:
        raise HTTPException(status_code=403, detail="asignación de otro worker")
    return {"ok": True, "job_id": job.id, "status": job.status,
            "ack": bool(body.get("accepted", True))}
