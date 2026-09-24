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

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from central_api.admin._common import (
    es_clave_sensible,
    redactar_metadata,
    require_admin,
    sanear_valor_texto,
    validar_motivo,
)
from central_api.admin.audit import log_event
from central_api.db import db_configurado, nueva_sesion
from central_api.security.rsa_credentials import (
    CredentialDecryptionError,
    decrypt_configured_credential,
)
from central_api.settings import get_settings
from central_api.store import JOBS, utcnow

router = APIRouter()

EXECUTION_TABLES = ("jobs", "job_results", "job_artifacts", "job_events")

# Contrato de las tablas físicas por bot. Solo estas columnas se seleccionan y
# se exponen aunque una tabla futura agregue datos internos o credenciales.
BOT_TABLE_COLUMNS = (
    "job_id", "user_id", "worker_id", "bot", "operation", "status",
    "result", "priority", "attempts", "max_attempts", "app_version",
    "protocol_version", "cancel_reason", "cancelled_by", "error_code",
    "request_payload", "response_payload", "response_summary",
    "response_received_at", "credential_metadata", "created_at",
    "assigned_at", "started_at", "finished_at", "updated_at",
    "artifact_names", "artifact_metadata",
)


def _bot_physical_table(bot: str) -> str | None:
    """Resuelve únicamente códigos de bot del catálogo a un identificador fijo."""
    from central_api.api.bots import CATALOGUE

    known_bots = {str(item["bot"]) for item in CATALOGUE}
    if bot not in known_bots or not re.fullmatch(r"[a-z][a-z0-9_]*", bot):
        return None
    return f"bot_jobs_{bot}"


#: Subcampos no secretos que el panel puede mostrar de una credencial.
SUBCAMPOS_DE_CREDENCIAL = ("available", "fields", "context")


def _es_contenedor_de_credenciales(normalizado: str) -> bool:
    """Indica si la clave describe credenciales sin contener valores.

    La credencial viaja sellada; lo que el panel puede mostrar de ella son los
    nombres de campo usados y el contexto no secreto. ``credential_metadata``
    lo calcula la central (nunca el cliente), así que se filtra a los mismos
    subcampos seguros en vez de taparlo entero, que dejaría al operador sin
    saber qué credencial consumió el job.
    """
    return normalizado in {
        "credentials",
        "credenciales",
        "credentialmetadata",
        "credencialmetadata",
        "credentialmeta",
    }


def _sanear_contenedor_credencial(anidado: dict) -> dict:
    """Conserva solo los subcampos seguros, saneando cada uno de nuevo."""
    return {
        nombre_seguro: _sanear_valor_tabla(anidado.get(nombre_seguro))
        for nombre_seguro in SUBCAMPOS_DE_CREDENCIAL
        if nombre_seguro in anidado
    }


def _normalizar_nombre(nombre: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(nombre).lower())


def _sanear_campo(nombre: object, valor: Any) -> Any:
    """Sanea el valor de una columna con nombre conocido.

    Una credencial (o su metadata) se filtra a los subcampos seguros cuando es
    un objeto; el resto de los nombres sensibles se tapan completos.
    """
    if _es_contenedor_de_credenciales(_normalizar_nombre(nombre)):
        return (
            _sanear_contenedor_credencial(valor)
            if isinstance(valor, dict)
            else "[REDACTED]"
        )
    if es_clave_sensible(str(nombre)):
        return "[REDACTED]"
    return _sanear_valor_tabla(valor)


def _sanear_valor_tabla(valor: Any) -> Any:
    """Redacta secretos y omite claves/valores de almacenamiento o URL."""
    if isinstance(valor, dict):
        salida = {}
        for clave, anidado in valor.items():
            nombre = str(clave)
            normalizado = _normalizar_nombre(nombre)
            if (
                "objectkey" in normalizado
                or "url" in normalizado
                or "href" in normalizado
                or normalizado == "uri"
                or normalizado.endswith("uri")
            ):
                continue
            if _es_contenedor_de_credenciales(normalizado) and isinstance(anidado, dict):
                salida[clave] = _sanear_contenedor_credencial(anidado)
                continue
            if es_clave_sensible(nombre):
                salida[clave] = "[REDACTED]"
            else:
                salida[clave] = _sanear_valor_tabla(anidado)
        return salida
    if isinstance(valor, (list, tuple)):
        return [_sanear_valor_tabla(item) for item in valor]
    if isinstance(valor, str):
        return sanear_valor_texto(valor)
    return valor

TABLE_COLUMNS = {
    "jobs": (
        "id", "user_id", "usuario_email", "bot", "operacion", "estado",
        "resultado", "intento", "worker_id", "creado_en", "asignado_en",
        "iniciado_en", "finalizado_en", "request", "credentials",
    ),
    "job_results": (
        "job_id", "attempt", "result", "payload", "summary", "received_at",
    ),
    "job_artifacts": (
        "id", "job_id", "kind", "name", "content_type", "size_bytes",
        "sha256", "created_at", "expires_at",
    ),
    "job_events": (
        "id", "job_id", "attempt", "type", "key", "payload", "occurred_at",
    ),
}

# Nombres de las tablas de logs que los administradores conocen de V1/V2. V3
# conserva una fuente canónica normalizada, pero expone estos alias visuales
# para que cada sección identifique la tabla histórica equivalente cuando la
# operación tiene una correspondencia directa.
LEGACY_BOT_TABLES = {
    "aportes_en_linea": ("consulta_aportes_en_linea_logs",),
    "ccma": ("consulta_ccma_logs",),
    "certificado_mipyme": ("consulta_certificado_mipyme_logs",),
    "controladores_fiscales": ("consulta_controladores_fiscales_logs",),
    "declaracion_en_linea": ("consulta_declaracion_en_linea_logs",),
    "facturometro": ("consulta_facturometro_logs",),
    "hacienda": ("consulta_hacienda_logs",),
    "libros_portal_iva": ("consulta_libros_iva_logs",),
    "liquidacion_granos": ("consulta_liquidacion_granos_logs",),
    "mis_comprobantes": ("consulta_mc_logs",),
    "mis_facilidades": ("consulta_mis_facilidades_logs",),
    "mis_retenciones": ("consulta_mis_retenciones_logs",),
    "mis_retenciones_iva_simple": ("consulta_mis_retenciones_iva_simple_logs",),
    "moa": ("consulta_moa_logs",),
    "pago_devoluciones": ("consulta_pago_devoluciones_logs",),
    "portal_iva": ("consulta_portal_iva_logs", "consulta_portal_iva_carga_logs"),
    "rcel": ("consulta_rcel_logs",),
    "retper_iibb_agip": (
        "consulta_retenciones_percepciones_iibb_agip_logs",
    ),
    "retper_iibb_misiones": (
        "consulta_retenciones_percepciones_iibb_misiones_logs",
    ),
    "sct": ("consulta_sct_logs", "consulta_sct_compensaciones_logs"),
    "sifere": ("consulta_sifere_logs",),
    "siper": ("consulta_siper_logs",),
    "srt": ("consulta_srt_logs",),
    "vep_archivo": ("consulta_vep_logs",),
    "vep_ccma": ("consulta_vep_ccma_logs",),
}


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
        "response": redactar_metadata(resultado) if resultado else None,
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
    *,
    job_id: str | None = None,
    bot: str = "",
    estado: str = "",
    operacion: str = "",
    usuario: str = "",
    q: str = "",
    limit: int = 100,
    offset: int = 0,
) -> list[dict] | None:
    """Lee el agregado completo de ejecución y sus tablas relacionadas."""
    if not db_configurado():
        return None
    try:
        from sqlalchemy import String, cast, or_, select

        from central_api.models.execution import Job, JobArtifact, JobEvent, JobResult
        from central_api.models.identity import User
    except Exception:  # noqa: BLE001 - desarrollo sin SQLAlchemy
        return None
    try:
        async with nueva_sesion() as sesion:
            stmt = select(Job).order_by(Job.created_at.desc(), Job.id.desc())
            needs_user_join = bool(usuario.strip() or q.strip())
            if needs_user_join:
                stmt = stmt.join(User, User.id == Job.user_id)
            if job_id:
                stmt = stmt.where(Job.id == uuid.UUID(job_id))
            if bot:
                stmt = stmt.where(Job.bot == bot)
            if estado:
                stmt = stmt.where(Job.status == estado)
            if operacion:
                stmt = stmt.where(Job.operation == operacion)
            if usuario.strip():
                usuario_filtro = usuario.strip()
                try:
                    usuario_uuid = uuid.UUID(usuario_filtro)
                except ValueError:
                    usuario_uuid = None
                usuario_condiciones = [User.email.ilike(f"%{usuario_filtro}%")]
                if usuario_uuid is not None:
                    usuario_condiciones.append(Job.user_id == usuario_uuid)
                stmt = stmt.where(or_(*usuario_condiciones))
            if q.strip():
                termino = f"%{q.strip()}%"
                stmt = stmt.where(or_(
                    Job.bot.ilike(termino),
                    Job.operation.ilike(termino),
                    Job.status.ilike(termino),
                    cast(Job.id, String).ilike(termino),
                    User.email.ilike(termino),
                ))
            filas = list((await sesion.execute(
                stmt.offset(max(0, offset)).limit(max(1, min(limit, 200)))
            )).scalars())
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
            "response": (
                {
                    "attempt": resultado.attempt,
                    "result": resultado.result,
                    "payload": redactar_metadata(dict(resultado.payload or {})),
                    "summary": redactar_metadata(dict(resultado.summary or {})),
                    "received_at": _iso(resultado.received_at),
                }
                if resultado is not None else None
            ),
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


async def _bot_table_records_db(
    *,
    bot: str,
    job_id: str | None = None,
    estado: str = "",
    operacion: str = "",
    usuario: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any] | None:
    """Consulta la tabla física allowlistada de un bot sin reflejar columnas secretas.

    El nombre se genera desde el catálogo canónico. Se consultan las columnas
    visibles conocidas, verificadas contra ``information_schema`` para tolerar
    despliegues graduales sin leer columnas internas agregadas en el futuro.
    ``None`` indica que la tabla aún no existe y permite el fallback V3.
    """
    table_name = _bot_physical_table(bot)
    if not table_name or not db_configurado():
        return None
    try:
        from sqlalchemy import text
    except Exception:  # noqa: BLE001 - desarrollo sin SQLAlchemy
        return None

    try:
        async with nueva_sesion() as sesion:
            available = (await sesion.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'public' AND table_name = :table_name "
                    "ORDER BY ordinal_position"
                ),
                {"table_name": table_name},
            )).scalars().all()
            available_set = set(available)
            if not available_set:
                return None
            columns = [name for name in BOT_TABLE_COLUMNS if name in available_set]
            required_columns = {
                "job_id", "user_id", "bot", "operation", "status",
                "request_payload", "response_payload", "created_at",
            }
            if not required_columns.issubset(columns):
                raise HTTPException(
                    status_code=503,
                    detail="La tabla física del bot no tiene el esquema esperado",
                )

            conditions: list[str] = []
            params: dict[str, Any] = {}
            conditions.append("t.bot = :bot")
            params["bot"] = bot
            if job_id:
                try:
                    params["job_id"] = str(uuid.UUID(job_id))
                except (ValueError, AttributeError, TypeError) as exc:
                    raise HTTPException(status_code=400, detail="job_id con formato inválido") from exc
                conditions.append("t.job_id = CAST(:job_id AS UUID)")
            if estado.strip() and "status" in columns:
                conditions.append("t.status = :estado")
                params["estado"] = estado.strip()
            if operacion.strip() and "operation" in columns:
                conditions.append("t.operation = :operacion")
                params["operacion"] = operacion.strip()
            if usuario.strip():
                user_value = usuario.strip()
                conditions.append(
                    "(CAST(t.user_id AS TEXT) = :usuario_uuid "
                    "OR u.email ILIKE :usuario_email)"
                )
                params["usuario_uuid"] = user_value
                params["usuario_email"] = f"%{user_value}%"
            if q.strip():
                searchable = [
                    "CAST(t.job_id AS TEXT)",
                    "CAST(t.user_id AS TEXT)",
                    "u.email",
                ]
                searchable.extend(
                    f"CAST(t.{name} AS TEXT)"
                    for name in ("bot", "operation", "status")
                    if name in columns
                )
                conditions.append("(" + " OR ".join(
                    f"{expression} ILIKE :q" for expression in searchable
                ) + ")")
                params["q"] = f"%{q.strip()}%"
            where_sql = " WHERE " + " AND ".join(conditions) if conditions else ""
            from_sql = (
                f'FROM public."{table_name}" AS t '
                "LEFT JOIN public.users AS u ON u.id = t.user_id"
            )
            total = int((await sesion.execute(
                text(f"SELECT COUNT(*) {from_sql}{where_sql}"), params
            )).scalar_one())

            # Los nombres interpolados provienen exclusivamente de la constante
            # BOT_TABLE_COLUMNS y del catálogo, no de parámetros HTTP.
            select_sql = ", ".join(f't."{name}"' for name in columns)
            select_sql += ', u.email AS "usuario_email"'
            query = (
                f"SELECT {select_sql} {from_sql}{where_sql} "
                "ORDER BY t.created_at DESC NULLS LAST, t.job_id DESC "
                "LIMIT :limit OFFSET :offset"
            )
            params["limit"] = max(1, min(int(limit), 100))
            params["offset"] = max(0, int(offset))
            rows = (await sesion.execute(text(query), params)).mappings().all()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - no ocultar fallas reales de PostgreSQL
        raise HTTPException(
            status_code=503,
            detail="No se pudo consultar la tabla física del bot",
        ) from exc

    records = [
        {
            name: _sanear_campo(name, row.get(name))
            for name in columns
        } | {"usuario_email": _sanear_valor_tabla(row.get("usuario_email"))}
        for row in rows
    ]
    return {
        "table": table_name,
        "columns": columns,
        "display_columns": [*columns, "usuario_email"],
        "records": records,
        "total": total,
    }


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


def _bot_sections(
    registros: list[dict[str, Any]], *, bot_filter: str = ""
) -> list[dict[str, Any]]:
    """Agrupa registros por cada bot del catálogo, incluso si está vacío.

    La sección conserva la separación histórica que ofrecían V1/V2, pero cada
    fila sigue apuntando a las cuatro tablas canónicas de V3. El request y el
    response son metadatos saneados, nunca contienen el ciphertext ni URLs de
    almacenamiento.
    """
    from central_api.api.bots import CATALOGUE

    by_bot: dict[str, list[dict[str, Any]]] = {}
    for registro in registros:
        bot = str((registro.get("job") or {}).get("bot") or "")
        by_bot.setdefault(bot, []).append(registro)

    sections = []
    for item in CATALOGUE:
        bot = str(item["bot"])
        if bot_filter and bot != bot_filter:
            continue
        bot_records = by_bot.get(bot, [])
        by_operation = {
            operation: [
                registro
                for registro in bot_records
                if (registro.get("job") or {}).get("operacion") == operation
            ]
            for operation in item["operaciones"]
        }
        sections.append({
            "bot": bot,
            "operaciones": list(item["operaciones"]),
            "tablas": list(EXECUTION_TABLES),
            "tablas_legacy": list(LEGACY_BOT_TABLES.get(bot, ())),
            "total": len(bot_records),
            "records": bot_records,
            "por_operacion": [
                {
                    "operacion": operation,
                    "total": len(operation_records),
                    "records": operation_records,
                }
                for operation, operation_records in by_operation.items()
            ],
        })
    return sections


def _table_catalog() -> list[dict[str, Any]]:
    """Devuelve el catálogo visible del explorador sin reflejar tablas arbitrarias.

    V2 permitía seleccionar cualquier tabla física. En V3 el catálogo es una
    allowlist de tablas operativas y vistas virtuales por bot: así se consultan
    datos reales de PostgreSQL sin permitir que el panel lea secretos, tablas
    internas o columnas de infraestructura por reflexión.
    """
    from central_api.api.bots import CATALOGUE

    entries = [
        {
            "name": name,
            "label": name,
            "kind": "canonical",
            "bot": None,
            "columns": list(TABLE_COLUMNS[name]),
            "legacy": [],
        }
        for name in EXECUTION_TABLES
    ]
    for item in CATALOGUE:
        bot = str(item["bot"])
        entries.append({
            "name": f"bot:{bot}",
            "label": f"Bot {bot}",
            "kind": "bot",
            "bot": bot,
            "physical_name": _bot_physical_table(bot),
            "physical": True,
            "columns": list(BOT_TABLE_COLUMNS),
            "operations": list(item["operaciones"]),
            "legacy": list(LEGACY_BOT_TABLES.get(bot, ())),
        })
    return entries


def _table_entry(tabla: str) -> dict[str, Any] | None:
    """Resuelve una tabla canónica, virtual por bot o alias V1/V2."""
    normalized = tabla.strip()
    entries = _table_catalog()
    direct = next((entry for entry in entries if entry["name"] == normalized), None)
    if direct is not None:
        return direct
    for entry in entries:
        if normalized in entry.get("legacy", []):
            return entry
    return None


def _job_matches(
    job: Any,
    *,
    bot: str = "",
    estado: str = "",
    operacion: str = "",
    usuario: str = "",
    q: str = "",
) -> bool:
    """Aplica en memoria los mismos filtros públicos del explorador."""
    values = {
        "bot": str(getattr(job, "bot", "") or ""),
        "estado": str(getattr(job, "status", "") or ""),
        "operacion": str(getattr(job, "operation", "") or ""),
        "usuario": str(
            getattr(job, "user_email", None)
            or (getattr(job, "payload", {}) or {}).get("user_id", "")
        ),
        "id": str(getattr(job, "id", "") or ""),
    }
    if bot and values["bot"] != bot:
        return False
    if estado and values["estado"] != estado:
        return False
    if operacion and values["operacion"] != operacion:
        return False
    if usuario and usuario.casefold() not in values["usuario"].casefold():
        return False
    if q and not any(q.casefold() in value.casefold() for value in values.values()):
        return False
    return True

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
        "bot_sections": _bot_sections(registros, bot_filter=bot),
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
    operacion: str = "",
    usuario: str = "",
    q: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Lista cada tabla de ejecución con una forma compatible con V1/V2.

    ``jobs``, ``job_results``, ``job_artifacts`` y ``job_events`` se mantienen
    separados en ``tables``. Los artefactos exponen únicamente nombre y
    metadatos, nunca URLs prefirmadas ni ``object_key``.
    """
    require_admin(authorization)
    if job_id:
        # Se valida en la entrada para que TODO camino (tabla física, canónica o
        # respaldo en memoria) rechace el filtro mal formado. Si se dejara pasar,
        # la consulta canónica lanzaría ValueError y el ``except`` amplio de
        # ``_ejecuciones_db`` devolvería una página sin filtrar como si fuera
        # válida, engañando al operador.
        job_id = _validar_uuid(job_id)
    entry = _table_entry(tabla) if tabla != "all" else None
    if tabla != "all" and entry is None:
        raise HTTPException(status_code=400, detail="tabla no válida")
    if entry and entry["kind"] == "bot":
        table_bot = str(entry["bot"])
        if bot and bot != table_bot:
            raise HTTPException(status_code=400, detail="bot no coincide con la tabla")
        bot = table_bot
    page_size = max(1, min(limit, 100))
    page_offset = max(0, offset)

    if entry and entry["kind"] == "bot":
        physical = await _bot_table_records_db(
            bot=bot,
            job_id=job_id,
            estado=estado,
            operacion=operacion,
            usuario=usuario,
            q=q,
            limit=page_size,
            offset=page_offset,
        )
        if physical is not None:
            records = physical["records"]
            total = physical["total"]
            has_more = page_offset + len(records) < total
            tables = _registros_por_tabla([])
            tables[physical["table"]] = records
            section = {
                "bot": bot,
                "operaciones": list(entry.get("operations", [])),
                "tablas": [physical["table"]],
                "tablas_legacy": list(entry.get("legacy", [])),
                "total": total,
                "records": records,
                "columns": physical["columns"],
                "display_columns": physical["display_columns"],
                "physical_table": True,
            }
            return {
                "success": True,
                "tabla": tabla,
                "tabla_resuelta": entry["name"],
                "catalogo": entry,
                "filtros": {
                    "bot": bot,
                    "estado": estado,
                    "operacion": operacion,
                    "usuario": usuario,
                    "q": q,
                },
                "limit": page_size,
                "offset": page_offset,
                "has_more": has_more,
                "next_offset": page_offset + page_size if has_more else None,
                "total": total,
                "records": records,
                "tables": tables,
                "bot_sections": [section],
                "physical_table": True,
                "columns": physical["columns"],
                "display_columns": physical["display_columns"],
                "fuente": "postgresql",
            }

    registros_db = await _ejecuciones_db(
        job_id=job_id,
        bot=bot,
        estado=estado,
        operacion=operacion,
        usuario=usuario,
        q=q,
        limit=page_size + 1,
        offset=page_offset,
    )
    if registros_db is None:
        candidatos = [
            _registro_memoria(job)
            for job in sorted(
                JOBS.values(),
                key=lambda item: (str(item.created_at), item.id),
                reverse=True,
            )
            if _job_matches(
                job,
                bot=bot,
                estado=estado,
                operacion=operacion,
                usuario=usuario,
                q=q,
            )
        ]
        registros = candidatos[page_offset : page_offset + page_size + 1]
        fuente = "memoria"
    else:
        registros = registros_db
        fuente = "postgresql"
    if entry and entry["kind"] == "bot":
        # Antes de desplegar la migración, el panel conserva el fallback V3;
        # aplicar la misma política estricta evita que esa ruta filtre URLs.
        registros = [_sanear_valor_tabla(registro) for registro in registros]
    has_more = len(registros) > page_size
    if has_more:
        registros = registros[:page_size]
    tables = _registros_por_tabla(registros)
    if tabla == "all" or (entry and entry["kind"] == "bot"):
        selected = registros
    else:
        selected = tables[entry["name"]]
    sections = (
        _bot_sections(registros, bot_filter=bot)
        if tabla == "all" or (entry and entry["kind"] == "bot")
        else []
    )
    return {
        "success": True,
        "tabla": tabla,
        "tabla_resuelta": entry["name"] if entry else "all",
        "catalogo": entry,
        "filtros": {
            "bot": bot,
            "estado": estado,
            "operacion": operacion,
            "usuario": usuario,
            "q": q,
        },
        "limit": page_size,
        "offset": page_offset,
        "has_more": has_more,
        "next_offset": page_offset + page_size if has_more else None,
        "total": len(selected),
        "records": selected,
        "tables": tables,
        "bot_sections": sections,
        "fuente": fuente,
    }


@router.get("/table-catalog")
def catalogo_tablas_admin(authorization: str | None = Header(default=None)) -> dict:
    """Catálogo allowlistado para seleccionar una tabla sin renderizar todo."""
    require_admin(authorization)
    return {
        "success": True,
        "tables": _table_catalog(),
        "canonical_tables": list(EXECUTION_TABLES),
        "bot_tables": [entry for entry in _table_catalog() if entry["kind"] == "bot"],
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
