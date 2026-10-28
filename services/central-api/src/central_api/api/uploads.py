"""Carga directa prefirmada de archivos de entrada (plan 02 §3.12).

``POST /uploads`` emite un ticket temporal (URL ``PUT`` + ``object_key``
opaco) para un campo declarado en el manifiesto; no ejecuta bots, no crea
jobs y no consume cuota. El ``POST`` de job solo acepta object keys
temporales emitidos al mismo principal, confirmados y sin vencer.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from central_api.api.bots import OPERATIONS
from central_api.db import db_configurado, nueva_sesion
from central_api.security.principals import ApiPrincipal
from central_api.security.secret_redaction import public_error
from central_api.api.dependencies import require_api_principal
from central_api.settings import get_settings
from mrbot_contracts.log_redaction import OBJETO_ELIMINADO
from central_api.storage import (
    firmar_subida,
    get_object_bytes,
    object_exists,
    presign_get_url,
)
from central_api.input_files import UPLOAD_FIELDS, UPLOAD_FIELD_MAX_BYTES

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
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [{
                "bot": "portal_iva",
                "operacion": "importar",
                "campo": "ventas_txt",
                "cuit_representado": "20123456786",
                "filename": "ventas.txt",
                "content_type": "text/plain",
                "size_bytes": 120,
            }]
        }
    )

    bot: str = Field(
        description="Identificador del bot cuya operación recibirá el archivo.",
        examples=["portal_iva"],
    )
    operacion: str = Field(
        description="Operación declarada por el bot para la que se emite el ticket.",
        examples=["importar"],
    )
    campo: str = Field(
        default="archivos",
        min_length=1,
        max_length=120,
        description="Nombre del campo de archivo que se usará en la operación (de 1 a 120 caracteres).",
        examples=["archivos"],
    )
    cuit_representado: str | None = Field(
        default=None,
        description="CUIT del representado (o del representante si el bot no tiene representado).",
        examples=["00000000000"],
    )
    filename: str = Field(
        min_length=1,
        max_length=255,
        description="Nombre original del archivo, de 1 a 255 caracteres. La ruta enviada se descarta.",
        examples=["comprobantes.pdf"],
    )
    content_type: str = Field(
        default="application/octet-stream",
        description="Tipo MIME que debe usarse también en la carga del archivo.",
        examples=["application/pdf"],
    )
    size_bytes: int = Field(
        ge=1,
        description="Tamaño del archivo en bytes. Debe ser mayor que cero y no superar 52.428.800 bytes (50 MiB).",
        examples=[1048576],
    )
    sha256: str | None = Field(
        default=None,
        description="Hash SHA-256 hexadecimal opcional del contenido del archivo.",
        examples=["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"],
    )


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


@router.post(
    "/uploads",
    status_code=201,
    summary="Solicita una carga directa de archivo",
    description=(
        "Paso 1 de un flujo de dos pasos: emite un ticket temporal para cargar un archivo "
        "asociado a una operación existente. Envíe un JSON con bot, operación, campo, nombre, "
        "tipo y tamaño. La respuesta 201 incluye `object_key`, la dirección temporal de carga "
        "y los encabezados requeridos. Cargue el archivo allí y, en el paso 2, envíe el "
        "`object_key` en el campo de archivo de la operación correspondiente. "
        "No ejecuta la operación ni crea un trabajo. Se acepta cualquier tipo MIME declarado, "
        "con tamaño informado entre 1 y 52.428.800 bytes (50 MiB); el cliente debe respetar "
        "el encabezado Content-Type devuelto y cargar el archivo usando la dirección temporal. El ticket permanece "
        "vigente hasta la fecha de retención indicada y solo puede utilizarlo la cuenta que lo solicitó."
    ),
    responses={
        201: {
            "description": "Ticket emitido. La respuesta incluye la clave del objeto, una URL de carga y el encabezado requerido.",
            "content": {"application/json": {"example": {
                "upload_id": "7de83a76-9ac8-4c26-9a5f-e5e9709bce42",
                "object_key": "550e8400-e29b-41d4-a716-446655440000/portal_iva/00000000000/comprobantes.pdf",
                "bucket": "uploads",
                "upload_url": "https://almacenamiento.example/carga-temporal",
                "upload_url_expires_in": 900,
                "expires_at": "2026-10-12T17:00:00Z",
                "required_headers": {"Content-Type": "application/pdf"},
            }}},
        },
        400: {
            "description": "El tamaño declarado supera 52.428.800 bytes. Reduzca el archivo antes de solicitar el ticket.",
            "content": {"application/json": {"example": {"detail": {"error_code": "validation", "message": "Datos de entrada inválidos."}}}},
        },
        401: {
            "description": "Las credenciales están ausentes o no son válidas. Envíe una clave de API válida con el método de autenticación admitido.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        404: {
            "description": "La combinación de bot y operación no existe. Revise ambos identificadores en el catálogo de operaciones disponibles.",
            "content": {"application/json": {"example": {"detail": {"error_code": "not_found", "message": "Recurso no encontrado."}}}},
        },
        422: {
            "description": "El cuerpo no cumple el esquema: faltan campos obligatorios o hay valores fuera de sus restricciones. Corrija los campos indicados y vuelva a enviar JSON válido.",
        },
        503: {
            "description": "No se pudo guardar el ticket o verificar la autenticación por indisponibilidad del servicio de datos. Reintente más tarde; no reutilice una carga sin un ticket confirmado.",
            "content": {"application/json": {"examples": {
                "ticket_no_guardado": {"summary": "No se pudo emitir el ticket", "value": {"detail": "Servicio no disponible"}},
                "autenticacion_no_disponible": {"summary": "No se pudo verificar la autenticación", "value": {"detail": {"error_code": "service_not_enabled", "message": "Servicio temporalmente no disponible."}}},
            }}},
        },
    },
)
async def request_upload(
    body: TicketBody,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """Emite una URL temporal para cargar un archivo de hasta 50 MiB."""
    definition = OPERATIONS.get((body.bot, body.operacion))
    if definition is None:
        return JSONResponse(status_code=404, content=public_error("not_found"))
    campos_permitidos = UPLOAD_FIELDS.get((body.bot, body.operacion), frozenset())
    if body.campo not in campos_permitidos:
        return JSONResponse(
            status_code=422,
            content={"detail": {
                "error_code": "validation",
                "message": (
                    f"El campo {body.campo!r} no admite archivos en esta operación. "
                    f"Campos admitidos: {', '.join(sorted(campos_permitidos)) or 'ninguno'}."
                ),
            }},
        )
    if body.size_bytes > UPLOAD_MAX_BYTES:
        return JSONResponse(status_code=400, content=public_error("validation"))
    max_campo = UPLOAD_FIELD_MAX_BYTES.get(
        (body.bot, body.operacion, body.campo), UPLOAD_MAX_BYTES
    )
    if body.size_bytes > max_campo:
        return JSONResponse(
            status_code=422,
            content={"detail": {
                "error_code": "validation",
                "message": (
                    f"El archivo supera el máximo permitido para {body.campo}: "
                    f"{max_campo:,} bytes."
                ),
            }},
        )
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


@router.get(
    "/jobs/{job_id}/artifacts/{artifact_id}/download",
    status_code=307,
    summary="Descarga un artefacto de un trabajo",
    description=(
        "Comprueba que el trabajo y su artefacto pertenecen a la cuenta autenticada y que el "
        "artefacto no venció. Si la comprobación es correcta, responde con una redirección "
        "temporal al archivo; el destino de descarga es válido durante 300 segundos. "
        "La respuesta no contiene el archivo directamente."
    ),
    responses={
        307: {
            "description": "Redirección temporal al archivo. Use la URL indicada en el encabezado Location dentro de los siguientes 300 segundos.",
            "headers": {"Location": {"description": "URL temporal de descarga del archivo.", "schema": {"type": "string", "format": "uri"}}},
        },
        401: {
            "description": "Las credenciales están ausentes o no son válidas. Envíe una clave de API válida con el método de autenticación admitido.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        404: {
            "description": "El identificador no es válido, el trabajo o artefacto no existe, el trabajo pertenece a otra cuenta o el artefacto venció. Compruebe los identificadores y la vigencia; no se distingue entre inexistencia y falta de acceso.",
            "content": {"application/json": {"example": {"detail": "artefacto no encontrado"}}},
        },
        410: {
            "description": "El archivo ya no está en el almacenamiento (fue eliminado por la política de retención o manualmente). Vuelva a ejecutar la consulta para generarlo de nuevo.",
            "content": {"application/json": {"example": {"detail": "objeto eliminado del bucket"}}},
        },
        503: {
            "description": "El almacenamiento de archivos no está disponible o no está configurado. Revise su disponibilidad y vuelva a intentar la descarga.",
            "content": {"application/json": {"example": {"detail": "almacenamiento no disponible"}}},
        },
    },
)
async def download_artifact(
    job_id: str,
    artifact_id: str,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> RedirectResponse:
    """Redirige al archivo tras comprobar su pertenencia y vigencia.

    La URL de descarga dura cinco minutos. Un mismo 404 oculta la inexistencia
    del trabajo o artefacto y la falta de autorización.
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
            nombre_original = str(getattr(artifact, "filename", "") or "")
    except Exception as exc:  # noqa: BLE001 - oculta job ajeno/no disponible
        raise HTTPException(status_code=404, detail="artefacto no encontrado") from exc

    settings = get_settings()
    if object_exists(
        endpoint=settings.object_storage_endpoint,
        region=settings.object_storage_region,
        bucket=settings.bucket_para_artefacto(object_key),
        access_key=settings.object_storage_access_key,
        secret_key=settings.object_storage_secret_key,
        object_key=object_key,
    ) is False:
        raise HTTPException(status_code=410, detail=OBJETO_ELIMINADO)
    try:
        url = presign_get_url(
            endpoint=(
                settings.object_storage_public_endpoint
                or settings.object_storage_endpoint
            ),
            region=settings.object_storage_region,
            bucket=settings.bucket_para_artefacto(object_key),
            access_key=settings.object_storage_access_key,
            secret_key=settings.object_storage_secret_key,
            object_key=object_key,
            expires_seconds=ARTIFACT_DOWNLOAD_TTL_SECONDS,
            filename=nombre_original,
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


async def resolver_archivos(
    user_id: str,
    bot: str,
    claves: list[str],
    *,
    operation: str | None = None,
    expected_fields: dict[str, str] | None = None,
    include_content: bool = False,
) -> list[dict]:
    """Devuelve los tickets vigentes de ``claves`` si son del usuario y del bot.

    Lanza ``LookupError`` si alguna clave no existe, es ajena, venció o
    pertenece a otro bot. Los metadatos permiten re-utilizar el archivo en
    varios jobs mientras dure la retención.
    """
    resultado: list[dict] = []
    prefijo_usuario = f"{user_id}/"
    for clave in claves:
        clave = str(clave)
        # No se consulta ni revela información de claves ajenas al principal.
        if not clave.startswith(prefijo_usuario) or ".." in clave.split("/"):
            raise LookupError("archivo no encontrado en uploads")
        ticket: dict | None = None
        if db_configurado():
            try:
                import uuid as _uuid

                from sqlalchemy import text

                async with nueva_sesion() as session:
                    fila = (
                        await session.execute(
                            text(
                                "SELECT object_key, bot, operation, field, filename, content_type, size_bytes, sha256 "
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
        if ticket is None or ticket.get("bot") != bot:
            raise LookupError("archivo no encontrado en uploads")
        operacion_ticket = str(ticket.get("operation") or ticket.get("operacion") or "")
        campo_ticket = str(ticket.get("field") or ticket.get("campo") or "")
        if operation is not None and operacion_ticket != operation:
            raise LookupError("archivo no encontrado en uploads")
        if expected_fields is not None and expected_fields.get(clave) != campo_ticket:
            raise LookupError("archivo no encontrado en uploads")
        campos_permitidos = UPLOAD_FIELDS.get((bot, operacion_ticket), frozenset())
        if campo_ticket not in campos_permitidos:
            raise LookupError("archivo no encontrado en uploads")

        limite = UPLOAD_FIELD_MAX_BYTES.get(
            (bot, operacion_ticket, campo_ticket), UPLOAD_MAX_BYTES
        )
        if int(ticket.get("size_bytes") or 0) > limite:
            raise LookupError(f"El archivo supera el máximo permitido para {campo_ticket}: {limite:,} bytes.")

        settings = get_settings()
        storage_args = {
            "endpoint": settings.object_storage_endpoint,
            "region": settings.object_storage_region,
            "bucket": settings.object_storage_uploads_bucket,
            "access_key": settings.object_storage_access_key,
            "secret_key": settings.object_storage_secret_key,
            "object_key": clave,
        }
        existe = await asyncio.to_thread(object_exists, **storage_args)
        if existe is False:
            raise LookupError(OBJETO_ELIMINADO)
        if existe is None:
            raise LookupError("No se pudo comprobar el archivo en el bucket uploads.")
        try:
            contenido = await asyncio.to_thread(
                get_object_bytes,
                **storage_args,
                max_bytes=limite,
            )
        except RuntimeError as exc:
            if "excede el tamaño" in str(exc):
                raise LookupError(f"El archivo supera el máximo permitido para {campo_ticket}: {limite:,} bytes.") from exc
            if await asyncio.to_thread(object_exists, **storage_args) is False:
                raise LookupError(OBJETO_ELIMINADO) from exc
            raise LookupError("No se pudo leer el archivo del bucket uploads.") from exc

        from central_api.api.file_inputs import _validar_archivo

        try:
            _validar_archivo(
                bot, operacion_ticket, campo_ticket,
                str(ticket.get("filename") or ""), contenido,
            )
        except Exception as exc:
            detalle = getattr(exc, "detail", None) or "El archivo no cumple el formato permitido."
            raise LookupError(str(detalle)) from exc

        item = {
            "object_key": clave,
            "filename": ticket["filename"],
            "content_type": ticket["content_type"],
            "size_bytes": int(ticket["size_bytes"]),
            "sha256": ticket.get("sha256"),
            "field": campo_ticket,
            "operation": operacion_ticket,
        }
        if include_content:
            item["content"] = contenido
        resultado.append(item)
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
