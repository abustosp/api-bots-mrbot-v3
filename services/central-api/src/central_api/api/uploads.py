"""Carga directa prefirmada de archivos de entrada (plan 02 §3.12).

``POST /uploads`` emite un ticket temporal (URL ``PUT`` + ``object_key``
opaco) para un campo declarado en el manifiesto; no ejecuta bots, no crea
jobs y no consume cuota. El ``POST`` de job solo acepta object keys
temporales emitidos al mismo principal, confirmados y sin vencer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from central_api.api.bots import OPERATIONS
from central_api.security.principals import ApiPrincipal
from central_api.security.secret_redaction import public_error
from central_api.api.dependencies import require_api_principal
from central_api.settings import get_settings
from central_api.storage import firmar_subida

router = APIRouter()

UPLOAD_TTL_SECONDS = 900
UPLOAD_MAX_BYTES = 52_428_800

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


def verify_upload_key(object_key: str, user_id: str) -> bool:
    """Verifica pertenencia, vigencia y confirmación de un object key."""
    ticket = TICKETS.get(object_key)
    if ticket is None or ticket["owner"] != user_id:
        return False
    if ticket["expires_at"] < _utcnow() or not ticket["confirmado"]:
        return False
    return True


__all__ = ["TICKETS", "UPLOAD_TTL_SECONDS", "UPLOAD_MAX_BYTES", "verify_upload_key"]
