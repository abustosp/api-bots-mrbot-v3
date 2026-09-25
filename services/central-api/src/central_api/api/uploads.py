"""Carga directa prefirmada de archivos de entrada (plan 02 §3.12).

``POST /uploads`` emite un ticket temporal (URL ``PUT`` + ``object_key``
opaco) para un campo declarado en el manifiesto; no ejecuta bots, no crea
jobs y no consume cuota. El ``POST`` de job solo acepta object keys
temporales emitidos al mismo principal, confirmados y sin vencer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from central_api.api.bots import OPERATIONS
from central_api.security.principals import ApiPrincipal
from central_api.security.secret_redaction import public_error
from central_api.api.dependencies import require_api_principal
from central_api.settings import get_settings
from central_api.storage import firmar_subida, presign_get_url

router = APIRouter()

UPLOAD_TTL_SECONDS = 900
UPLOAD_MAX_BYTES = 52_428_800
ARTIFACT_DOWNLOAD_TTL_SECONDS = 300

# Tickets emitidos por clave de objeto (en PG: tabla de uploads temporales).
TICKETS: dict[str, dict] = {}


class TicketBody(BaseModel):
    bot: str
    operacion: str
    campo: str
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream")
    size_bytes: int = Field(ge=1)
    sha256: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.post("/uploads", status_code=201)
def request_upload(
    body: TicketBody,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    definition = OPERATIONS.get((body.bot, body.operacion))
    if definition is None:
        return JSONResponse(status_code=404, content=public_error("not_found"))
    if body.size_bytes > UPLOAD_MAX_BYTES:
        return JSONResponse(status_code=400, content=public_error("validation"))
    upload_id = str(uuid.uuid4())
    object_key = f"uploads/{upload_id}"
    expires_at = _utcnow() + timedelta(seconds=UPLOAD_TTL_SECONDS)
    TICKETS[object_key] = {
        "upload_id": upload_id,
        "owner": principal.user_id,
        "bot": body.bot,
        "operacion": body.operacion,
        "campo": body.campo,
        "filename": body.filename,
        "size_bytes": body.size_bytes,
        "expires_at": expires_at,
        "confirmado": True,  # el storage confirma por callback en PG; aquí directo
    }
    # La central firma la subida (SigV4 real con storage configurado, ticket
    # de desarrollo documentado si no); el cliente sube directo al storage.
    ajustes = get_settings()
    firmado = firmar_subida(
        object_key=object_key,
        content_type=body.content_type,
        ttl_seconds=UPLOAD_TTL_SECONDS,
        endpoint=ajustes.object_storage_endpoint,
        region=ajustes.object_storage_region,
        bucket=ajustes.object_storage_bucket,
        access_key=ajustes.object_storage_access_key,
        secret_key=ajustes.object_storage_secret_key,
    )
    return JSONResponse(
        status_code=201,
        content={
            "upload_id": upload_id,
            "object_key": object_key,
            "upload_url": firmado["upload_url"],
            "expires_at": expires_at.isoformat().replace("+00:00", "Z"),
            "required_headers": {"Content-Type": body.content_type},
        },
    )


@router.get("/jobs/{job_id}/artifacts/{artifact_id}/download")
async def download_artifact(
    job_id: str,
    artifact_id: str,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> RedirectResponse:
    """Redirige al objeto tras comprobar ownership y su vínculo persistido.

    El URL del bucket dura cinco minutos y solo se emite para un artifact
    asociado al job visible del usuario. Un mismo 404 oculta inexistencia y
    falta de autorización.
    """
    try:
        job_uuid = uuid.UUID(job_id)
        artifact_uuid = uuid.UUID(artifact_id)
        user_uuid = uuid.UUID(str(principal.user_id))
        from sqlalchemy import select

        from central_api.db import nueva_sesion
        from central_api.models.execution import JobArtifact
        from central_api.repositories.jobs import JobRepository

        async with nueva_sesion() as session:
            repo = JobRepository(session)  # type: ignore[arg-type]
            await repo.get_scoped(job_uuid, user_uuid)
            artifact = (await session.execute(
                select(JobArtifact).where(
                    JobArtifact.id == artifact_uuid,
                    JobArtifact.job_id == job_uuid,
                )
            )).scalar_one_or_none()
            if artifact is None:
                raise LookupError("artifact no encontrado")
            if artifact.expires_at is not None and artifact.expires_at <= _utcnow():
                raise LookupError("artifact expirado")
            object_key = str(artifact.object_key)
    except Exception as exc:  # noqa: BLE001 - oculta job ajeno/no disponible
        raise HTTPException(status_code=404, detail="artefacto no encontrado") from exc

    settings = get_settings()
    try:
        url = presign_get_url(
            endpoint=(
                settings.object_storage_public_endpoint
                or settings.object_storage_endpoint
            ),
            region=settings.object_storage_region,
            bucket=settings.object_storage_bucket,
            access_key=settings.object_storage_access_key,
            secret_key=settings.object_storage_secret_key,
            object_key=object_key,
            expires_seconds=ARTIFACT_DOWNLOAD_TTL_SECONDS,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=503, detail="almacenamiento no disponible"
        ) from exc
    return RedirectResponse(
        url=url,
        status_code=307,
        headers={
            "Cache-Control": "private, no-store",
            "Referrer-Policy": "no-referrer",
        },
    )


def verify_upload_key(object_key: str, user_id: str) -> bool:
    """Verifica pertenencia, vigencia y confirmación de un object key."""
    ticket = TICKETS.get(object_key)
    if ticket is None or ticket["owner"] != user_id:
        return False
    if ticket["expires_at"] < _utcnow() or not ticket["confirmado"]:
        return False
    return True


__all__ = ["TICKETS", "UPLOAD_TTL_SECONDS", "UPLOAD_MAX_BYTES", "verify_upload_key"]
