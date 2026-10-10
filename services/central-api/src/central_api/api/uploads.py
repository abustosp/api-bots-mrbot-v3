"""Carga directa prefirmada de archivos de entrada (plan 02 §3.12).

``POST /uploads`` emite un ticket temporal (URL ``PUT`` + ``object_key``
opaco) para un campo declarado en el manifiesto; no ejecuta bots, no crea
jobs y no consume cuota. El ``POST`` de job solo acepta object keys
temporales emitidos al mismo principal, confirmados y sin vencer.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from central_api.api.bots import OPERATIONS
from central_api.db import db_configurado, nueva_sesion
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


_SEGMENTO_INVALIDO = re.compile(r"[^A-Za-z0-9._() \-]+")


def segmento_seguro(valor: str, *, defecto: str) -> str:
    """Sanea un segmento de object key (sin separadores ni ``..``)."""
    limpio = _SEGMENTO_INVALIDO.sub("_", str(valor or "")).strip(" .")
    return (limpio or defecto)[:200]


def clave_upload(user_id: str, bot: str, cuit: str | None, filename: str) -> str:
    """Clave en el bucket ``uploads``: ``{usuario}/{bot}/{cuit}/{archivo}``.

    El prefijo de usuario evita que dos cuentas con el mismo CUIT y nombre de
    archivo se pisen el objeto: la subida prefirmada es por clave exacta.
    """
    digitos = re.sub(r"\D", "", str(cuit or ""))
    return "/".join((
        segmento_seguro(str(user_id), defecto="usuario"),
        segmento_seguro(bot, defecto="bot"),
        digitos or "sin_cuit",
        segmento_seguro(filename.replace("\\", "/").rsplit("/", 1)[-1], defecto="archivo"),
    ))


class TicketBody(BaseModel):
    bot: str
    operacion: str
    campo: str = Field(default="archivos", min_length=1, max_length=120)
    cuit_representado: str | None = Field(
        default=None,
        description="CUIT del representado (o del representante si el bot no tiene representado).",
    )
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="application/octet-stream")
    size_bytes: int = Field(ge=1)
    sha256: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _persist_ticket(ticket: dict) -> None:
    """Persiste el ticket sin almacenar URLs firmadas ni secretos de storage."""
    if not db_configurado():
        return
    try:
        from sqlalchemy import text

        async with nueva_sesion() as session:
            await session.execute(
                text(
                    "INSERT INTO upload_tickets "
                    "(object_key, upload_id, user_id, bot, operation, field, filename, "
                    "content_type, size_bytes, sha256, confirmed, expires_at) "
                    "VALUES (:object_key, :upload_id, :user_id, :bot, :operation, :field, "
                    ":filename, :content_type, :size_bytes, :sha256, :confirmed, :expires_at) "
                    "ON CONFLICT (object_key) DO UPDATE SET "
                    "upload_id = EXCLUDED.upload_id, operation = EXCLUDED.operation, "
                    "field = EXCLUDED.field, filename = EXCLUDED.filename, "
                    "content_type = EXCLUDED.content_type, size_bytes = EXCLUDED.size_bytes, "
                    "sha256 = EXCLUDED.sha256, confirmed = EXCLUDED.confirmed, "
                    "expires_at = EXCLUDED.expires_at, created_at = CURRENT_TIMESTAMP "
                    "WHERE upload_tickets.user_id = EXCLUDED.user_id"
                ),
                ticket,
            )
    except Exception as exc:  # nunca emitir una capacidad que no sobrevivirá al reinicio
        raise HTTPException(status_code=503, detail="Servicio no disponible") from exc


@router.post("/uploads", status_code=201)
async def request_upload(
    body: TicketBody,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    definition = OPERATIONS.get((body.bot, body.operacion))
    if definition is None:
        return JSONResponse(status_code=404, content=public_error("not_found"))
    if body.size_bytes > UPLOAD_MAX_BYTES:
        return JSONResponse(status_code=400, content=public_error("validation"))
    upload_id = str(uuid.uuid4())
    object_key = clave_upload(
        str(principal.user_id), body.bot, body.cuit_representado, body.filename
    )
    ajustes = get_settings()
    expires_at = _utcnow() + timedelta(seconds=ajustes.uploads_retention_seconds)
    ticket = {
        "upload_id": upload_id,
        "owner": str(principal.user_id),
        "user_id": str(principal.user_id),
        "object_key": object_key,
        "bot": body.bot,
        "operacion": body.operacion,
        "operation": body.operacion,
        "campo": body.campo,
        "field": body.campo,
        "filename": body.filename,
        "cuit": re.sub(r"\D", "", str(body.cuit_representado or "")),
        "content_type": body.content_type,
        "size_bytes": body.size_bytes,
        "sha256": body.sha256,
        "expires_at": expires_at,
        "confirmado": True,
        "confirmed": True,  # callback de storage futuro puede confirmarlo explícitamente
    }
    await _persist_ticket(ticket)
    # Mantiene el modo sin DATABASE_URL y permite pruebas locales sin PostgreSQL.
    TICKETS[object_key] = ticket
    # La central firma la subida (SigV4 real con storage configurado, ticket
    # de desarrollo documentado si no); el cliente sube directo al bucket
    # temporal ``uploads``.
    firmado = firmar_subida(
        object_key=object_key,
        content_type=body.content_type,
        ttl_seconds=UPLOAD_TTL_SECONDS,
        endpoint=(
            ajustes.object_storage_public_endpoint
            or ajustes.object_storage_endpoint
        ),
        region=ajustes.object_storage_region,
        bucket=ajustes.object_storage_uploads_bucket,
        access_key=ajustes.object_storage_access_key,
        secret_key=ajustes.object_storage_secret_key,
    )
    return JSONResponse(
        status_code=201,
        content={
            "upload_id": upload_id,
            "object_key": object_key,
            "bucket": ajustes.object_storage_uploads_bucket,
            "upload_url": firmado["upload_url"],
            "upload_url_expires_in": UPLOAD_TTL_SECONDS,
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
    """Fallback síncrono de desarrollo para consumidores legados.

    Con PostgreSQL los routers asíncronos deben usar
    :func:`verify_upload_key_async`, para no abrir una segunda conexión síncrona
    ni degradar la verificación durable a una caché de proceso.
    """
    ticket = TICKETS.get(object_key)
    if ticket is None or ticket["owner"] != str(user_id):
        return False
    if ticket["expires_at"] < _utcnow() or not ticket["confirmado"]:
        return False
    return True


async def verify_upload_key_async(object_key: str, user_id: str) -> bool:
    """Verifica un ticket durable, o el fallback de memoria sin base configurada."""
    if not db_configurado():
        return verify_upload_key(object_key, user_id)
    try:
        import uuid

        from sqlalchemy import text

        owner = uuid.UUID(str(user_id))
        async with nueva_sesion() as session:
            ticket = (
                await session.execute(
                    text(
                        "SELECT 1 FROM upload_tickets "
                        "WHERE object_key = :object_key AND user_id = :user_id "
                        "AND confirmed = true AND expires_at > CURRENT_TIMESTAMP"
                    ),
                    {"object_key": object_key, "user_id": owner},
                )
            ).first()
        return ticket is not None
    except (ValueError, TypeError, AttributeError):
        return False
    except Exception:
        # En modo DB no se autoriza por una caché potencialmente obsoleta.
        return False


async def resolver_archivos(user_id: str, bot: str, claves: list[str]) -> list[dict]:
    """Devuelve los tickets vigentes de ``claves`` si son del usuario y del bot.

    Lanza ``LookupError`` si alguna clave no existe, es ajena, venció o
    pertenece a otro bot. Los metadatos permiten re-utilizar el archivo en
    varios jobs mientras dure la retención.
    """
    resultado: list[dict] = []
    for clave in claves:
        ticket: dict | None = None
        if db_configurado():
            try:
                import uuid as _uuid

                from sqlalchemy import text

                async with nueva_sesion() as session:
                    fila = (
                        await session.execute(
                            text(
                                "SELECT object_key, bot, filename, content_type, size_bytes, sha256 "
                                "FROM upload_tickets WHERE object_key = :k AND user_id = :u "
                                "AND confirmed = true AND expires_at > CURRENT_TIMESTAMP"
                            ),
                            {"k": clave, "u": _uuid.UUID(str(user_id))},
                        )
                    ).mappings().first()
                if fila is not None:
                    ticket = dict(fila)
            except Exception as exc:  # noqa: BLE001 - sin base no se autoriza
                raise LookupError("no se pudo verificar el archivo") from exc
        else:
            t = TICKETS.get(clave)
            if t and t["owner"] == str(user_id) and t["expires_at"] > _utcnow() and t["confirmado"]:
                ticket = t
        if ticket is None or ticket["bot"] != bot:
            raise LookupError(f"archivo no disponible: {clave}")
        resultado.append({
            "object_key": clave,
            "filename": ticket["filename"],
            "content_type": ticket["content_type"],
            "size_bytes": int(ticket["size_bytes"]),
            "sha256": ticket.get("sha256"),
        })
    return resultado


__all__ = [
    "resolver_archivos",
    "clave_upload",
    "TICKETS",
    "UPLOAD_TTL_SECONDS",
    "UPLOAD_MAX_BYTES",
    "verify_upload_key",
    "verify_upload_key_async",
]
