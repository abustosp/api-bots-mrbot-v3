"""Dependencias de borde público: principal, correlación e idempotencia.

- ``require_api_principal`` termina la autenticación antes del dominio
  (plan 02 §4.1): lee ``X-API-Key`` ``mbk_<key_id>_<secreto>``, la verifica
  contra PostgreSQL (``api_keys`` por ``key_prefix`` + ``users``) con HMAC
  en tiempo constante y devuelve un ``ApiPrincipal`` inmutable con el
  ``user_id`` UUID pleno (sin IDs correlativos, I-1/I-2). Fallbacks
  documentados: sin secreto de verificación configurado opera en modo
  desarrollo con un principal estable (nunca en producción); con secreto
  configurado pero sin base falla cerrado (401, fail-closed) porque no
  puede verificar; la base caída responde 503 sanitizado.
- ``Idempotency-Key`` es obligatoria para crear jobs y compras (S-2): la
  misma llave solo se reutiliza con igual fingerprint canónico; con otro
  contenido responde ``409 idempotency_conflict`` sin crear nada nuevo.
- Toda respuesta incluye ``X-Correlation-ID`` (acepta el válido o genera).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Header, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import (
    APIKeyHeader,
    HTTPBasic,
    HTTPBasicCredentials,
)

from central_api.security.api_keys import parse_api_key, verify_presented_secret
from central_api.security.principals import ANONYMOUS_USER_ID, ApiPrincipal
from central_api.security.secret_redaction import public_error
from central_api.settings import get_settings
from central_api.store import utcnow

# Registro de idempotencia en memoria (en PG: tabla con UNIQUE + retención
# IDEMPOTENCY_RETENTION_DAYS, reutilizable tras estado terminal, S-2).
IDEMPOTENCY: dict[tuple[str, str], dict] = {}

basic_scheme = HTTPBasic(
    scheme_name="HTTPBasic",
    description="Método 1: username = usuario (email o alias), password = API key.",
    auto_error=False,
)

api_key_header_scheme = APIKeyHeader(
    name="X-API-Key",
    scheme_name="V1",
    description=(
        "Método 2 (como en la V1): headers `email` (usuario) + `X-API-Key` "
        "(API key), enviados juntos. En Swagger se completan ambos campos en "
        "esta misma sección."
    ),
    auto_error=False,
)

# Requisitos de seguridad publicados en OpenAPI: dos métodos alternativos.
OPENAPI_SECURITY = [
    {"HTTPBasic": []},
    {"V1": []},
]
_SECURITY_SCHEMES_AUTH = {"HTTPBasic", "V1"}


def apply_auth_openapi(document: dict) -> dict:
    """Unifica la seguridad del OpenAPI a los dos métodos admitidos.

    FastAPI publica cada dependencia ``Security`` como alternativa separada.
    Se fija explícitamente ``HTTPBasic`` o ``V1`` (email + X-API-Key).
    """
    for item in document.get("paths", {}).values():
        for operation in item.values():
            if not isinstance(operation, dict):
                continue
            security = operation.get("security")
            if not security:
                continue
            names = {name for requirement in security for name in requirement}
            if names & _SECURITY_SCHEMES_AUTH:
                operation["security"] = [dict(r) for r in OPENAPI_SECURITY]
    schemes = document.get("components", {}).get("securitySchemes")
    if schemes:
        ordered = {
            name: schemes[name]
            for name in ("HTTPBasic", "V1")
            if name in schemes
        }
        ordered.update({k: v for k, v in schemes.items() if k not in ordered})
        document["components"]["securitySchemes"] = ordered
    return document


def correlation_id(request: Request) -> str:
    """Acepta ``X-Correlation-ID`` válido o genera uno nuevo."""
    raw = request.headers.get("X-Correlation-ID", "")
    try:
        uuid.UUID(str(raw))
        return str(raw)
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid4())


def fingerprint_payload(bot: str, operation: str, payload: dict) -> str:
    """Hash canónico de la intención (sin secretos en claro)."""
    canonical = json.dumps(
        {"bot": bot, "operacion": operation, "payload": payload},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def current_user_id() -> str:
    """Usuario estable del modo desarrollo (sin secreto configurado)."""
    return ANONYMOUS_USER_ID


def _no_autorizado() -> HTTPException:
    """401 público idéntico para toda falla de credencial (sin oráculo)."""
    return HTTPException(
        status_code=401,
        detail=public_error("authentication")["detail"],
        headers={"WWW-Authenticate": "Basic"},
    )


async def _principal_desde_pg(
    hmac_secret: str, key_id: str, secret: str, identidad: str | None = None
) -> ApiPrincipal | None:
    """Verifica la key contra PostgreSQL; ``None`` si no autentica.

    Busca ``api_keys`` por ``key_prefix`` (solo activas: no revocadas ni
    vencidas), compara el HMAC en tiempo constante (con verificador ficticio
    si la fila no existe, plan 02 §4.4) y exige ``users.habilitado``. Los
    scopes salen de la fila; vacíos heredan el defecto de ``api_client``.
    """
    from sqlalchemy import func, select

    from central_api.db import nueva_sesion
    from central_api.models.identity import ApiKey, User

    async with nueva_sesion() as sesion:
        # Varios usuarios pueden compartir el mismo valor de clave: el selector
        # no es único, se resuelve por usuario + verificador.
        filas = (
            await sesion.execute(
                select(ApiKey, User)
                .join(User, User.id == ApiKey.user_id)
                .where(
                    ApiKey.key_prefix == key_id,
                    ApiKey.revoked_at.is_(None),
                    (ApiKey.expires_at.is_(None))
                    | (ApiKey.expires_at > func.current_timestamp()),
                )
            )
        ).all()
        fila = cuenta = None
        for candidata, usuario in filas:
            if identidad is not None and usuario.email.strip().casefold() != identidad.strip().casefold():
                continue
            if verify_presented_secret(hmac_secret, secret, candidata.verifier_hmac):
                fila, cuenta = candidata, usuario
                break
        if fila is None:
            verify_presented_secret(hmac_secret, secret, None)  # tiempo constante
            return None
        if not cuenta.habilitado:
            return None
        scopes = list(getattr(fila, "scopes", None) or [])
        if scopes:
            return ApiPrincipal(
                user_id=str(fila.user_id), key_id=key_id,
                scopes=frozenset(str(s) for s in scopes),
            )
        return ApiPrincipal(user_id=str(fila.user_id), key_id=key_id)


async def _clave_registrada_pg(key_id: str) -> bool:
    """Indica si existe el selector en PostgreSQL, incluso si está revocado."""
    from sqlalchemy import select

    from central_api.db import nueva_sesion
    from central_api.models.identity import ApiKey

    async with nueva_sesion() as sesion:
        fila = (
            await sesion.execute(
                select(ApiKey.id).where(ApiKey.key_prefix == key_id).limit(1)
            )
        ).first()
    return fila is not None


async def _habilitado_en_pg(user_id: str) -> bool | None:
    """Estado persistente del usuario, o ``None`` si solo existe en memoria."""
    from sqlalchemy import select

    from central_api.db import nueva_sesion
    from central_api.models.identity import User

    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        return None
    async with nueva_sesion() as sesion:
        fila = (
            await sesion.execute(
                select(User.habilitado).where(User.id == user_uuid).limit(1)
            )
        ).first()
    return bool(fila[0]) if fila is not None else None


def _principal_desde_memoria(
    presented: str | None, identidad: str | None = None
) -> ApiPrincipal | None:
    """Verifica la clave contra el store en memoria del panel admin.

    Cubre usuarios de depuración (p. ej. ``abp``/``testing``) y claves
    ``mrk_*`` emitidas sin PostgreSQL. Solo usa verificador HMAC + estado;
    nunca expone el valor.
    """
    if not presented:
        return None
    try:
        from central_api.admin.users import API_KEYS, USERS, _firmar_clave
    except Exception:  # noqa: BLE001 - sin store admin, sin fallback
        return None
    import hmac as _hmac

    verificador = _firmar_clave(presented)
    for meta in API_KEYS.values():
        if meta.revocada:
            continue
        if not _hmac.compare_digest(meta.verificador_hmac, verificador):
            continue
        usuario = USERS.get(meta.user_id)
        if usuario is None or usuario.estado != "habilitado":
            continue
        if identidad is not None and usuario.email.strip().casefold() != identidad.strip().casefold():
            continue
        scopes = frozenset(meta.scopes) if meta.scopes else ApiPrincipal(
            user_id=usuario.id, key_id=meta.id
        ).scopes
        return ApiPrincipal(user_id=usuario.id, key_id=meta.id, scopes=scopes)
    return None


def _selector_y_secreto(api_key: str) -> tuple[str, str]:
    """Obtiene el selector y el material firmado para cualquier API key.

    Las claves V3 mantienen ``mbk_<key_id>_<secret>``. Para claves elegidas
    literalmente por un usuario se usa un selector HMAC con el secreto del
    servidor, sin persistir ni exponer el valor original.
    """
    parsed = parse_api_key(api_key)
    if parsed is not None and len(parsed[0]) <= 16:
        return parsed
    selector_secret = get_settings().api_key_hmac_secret
    if selector_secret:
        selector = hmac.new(
            selector_secret.encode("utf-8"), api_key.encode("utf-8"), hashlib.sha256
        ).hexdigest()[:16]
    else:
        selector = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16]
    return selector, api_key


async def authenticate_api_key(
    identidad: str | None, api_key: str
) -> ApiPrincipal | None:
    """Valida clave, propietario, vigencia y estado para Basic o headers V1."""
    from central_api.db import db_configurado

    if not api_key:
        return None
    settings = get_settings()
    if settings.api_key_hmac_secret and db_configurado():
        key_id, secret = _selector_y_secreto(api_key)
        try:
            principal = await _principal_desde_pg(
                settings.api_key_hmac_secret, key_id, secret, identidad
            )
            if principal is not None:
                return principal
            # Una clave que exista en PostgreSQL no puede volver a ser válida
            # por un metadato de memoria obsoleto (revocada, vencida o usuario
            # deshabilitado).
            if await _clave_registrada_pg(key_id):
                return None
            principal_memoria = _principal_desde_memoria(api_key, identidad)
            if principal_memoria is None:
                return None
            habilitado = await _habilitado_en_pg(principal_memoria.user_id)
            if habilitado is False:
                return None
            return principal_memoria
        except Exception:  # noqa: BLE001 - fallo de DB sanitizado
            raise HTTPException(
                status_code=503,
                detail=public_error("service_not_enabled")["detail"],
            ) from None
    return _principal_desde_memoria(api_key, identidad)


async def require_api_principal(
    request: Request,
    basic_credentials: HTTPBasicCredentials | None = Security(basic_scheme),
    x_api_key: str | None = Security(api_key_header_scheme),
    email: str | None = Header(default=None, include_in_schema=False),
) -> ApiPrincipal:
    """Autentica al cliente por cualquiera de los dos métodos admitidos.

    1. ``Authorization: Basic b64(usuario:api_key)``.
    2. Headers ``email`` + ``X-API-Key`` (forma V1). ``X-API-Key`` sin
       ``email`` se conserva por compatibilidad con clientes V3 previos.

    Orden de verificación: PostgreSQL cuando hay secreto + base; fallback en
    memoria del panel; sin secreto configurado devuelve el principal estable
    de desarrollo; con secreto pero sin coincidencia falla cerrado (401); con
    base caída responde 503 sanitizado.
    """
    authorization = request.headers.get("Authorization", "")
    if basic_credentials is not None:
        identidad = (basic_credentials.username or "").strip()
        if not identidad or not basic_credentials.password:
            raise _no_autorizado()
        principal = await authenticate_api_key(identidad, basic_credentials.password)
        if principal is not None:
            return principal
        raise _no_autorizado()

    if authorization:
        # Cualquier otro esquema (incluido Bearer) se rechaza: no se degrada a
        # la autenticación abierta de desarrollo.
        raise _no_autorizado()

    if x_api_key:
        identidad = (email or "").strip() or None
        principal = await authenticate_api_key(identidad, x_api_key)
        if principal is not None:
            return principal
        if identidad is not None or get_settings().api_key_hmac_secret:
            raise _no_autorizado()
        return ApiPrincipal(user_id=current_user_id(), key_id="dev")

    if email and get_settings().api_key_hmac_secret:
        raise _no_autorizado()

    if not get_settings().api_key_hmac_secret:
        return ApiPrincipal(user_id=current_user_id(), key_id="dev")
    raise _no_autorizado()


@dataclass
class IdempotencyHit:
    """Resultado de consultar el registro de idempotencia."""

    job_id: str | None = None
    conflict: bool = False


async def check_idempotency(
    user_id: str, key: str, fingerprint: str
) -> IdempotencyHit:
    """Busca la llave: replay exacto, conflicto, o vía libre (S-2)."""
    if not key:
        return IdempotencyHit()
    from central_api.db import db_configurado

    if db_configurado():
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        try:
            owner = uuid.UUID(str(user_id))
        except (TypeError, ValueError, AttributeError):
            return IdempotencyHit()
        async with nueva_sesion() as session:
            row = (await session.execute(
                text(
                    "SELECT fingerprint, job_id FROM api_idempotency "
                    "WHERE user_id = :user_id AND idempotency_key = :key "
                    "AND expires_at > CURRENT_TIMESTAMP"
                ),
                {"user_id": owner, "key": key[:128]},
            )).mappings().first()
        if row is None:
            return IdempotencyHit()
        if row["fingerprint"] == fingerprint:
            return IdempotencyHit(
                job_id=str(row["job_id"]) if row["job_id"] else None
            )
        return IdempotencyHit(conflict=True)
    record = IDEMPOTENCY.get((user_id, key))
    if record is None:
        return IdempotencyHit()
    if record["fingerprint"] == fingerprint:
        return IdempotencyHit(job_id=record["job_id"])
    return IdempotencyHit(conflict=True)


async def remember_idempotency(
    user_id: str, key: str, fingerprint: str, job_id: str
) -> None:
    """Persiste la identidad de idempotencia junto al job creado."""
    if not key:
        return
    from central_api.db import db_configurado

    if db_configurado():
        from sqlalchemy import text

        from central_api.db import nueva_sesion

        try:
            owner, job = uuid.UUID(str(user_id)), uuid.UUID(str(job_id))
        except (TypeError, ValueError, AttributeError):
            return
        async with nueva_sesion() as session:
            await session.execute(
                text(
                    "INSERT INTO api_idempotency "
                    "(user_id, idempotency_key, fingerprint, job_id, expires_at) "
                    "VALUES (:user_id, :key, :fingerprint, :job_id, :expires_at) "
                    "ON CONFLICT (user_id, idempotency_key) DO NOTHING"
                ),
                {
                    "user_id": owner,
                    "key": key[:128],
                    "fingerprint": fingerprint[:128],
                    "job_id": job,
                    "expires_at": utcnow() + timedelta(days=30),
                },
            )
        return
    IDEMPOTENCY[(user_id, key)] = {"fingerprint": fingerprint, "job_id": job_id}


def idempotency_conflict_response() -> JSONResponse:
    """409 público ante misma llave con distinto contenido."""
    return JSONResponse(
        status_code=409, content=public_error("idempotency_conflict")
    )


# Metadatos de presentación por job (en PG: columnas de jobs + eventos).
# El store esqueleto solo guarda núcleo; aquí viven dueño, tiempos,
# cancelación, archivos firmados al leer y resultado proyectado.
JOB_META: dict[str, dict] = {}


def ensure_meta(job_id: str, user_id: str) -> dict:
    """Crea o devuelve los metadatos de presentación de un job."""
    meta = JOB_META.get(job_id)
    if meta is None:
        meta = {
            "owner": user_id, "started_at": None, "finished_at": None,
            "cancel_reason": None, "cancelled_by": None, "error": None,
            "files": [], "data": None, "result": None,
        }
        JOB_META[job_id] = meta
    return meta


def project_job(job, meta: dict | None = None) -> dict:
    """Proyecta un job con las claves/semántica V2 y los detalles nuevos V3.

    ``status_detail`` conserva el estado interno. ``status`` mantiene los
    valores que esperan clientes V2: una asignación sigue siendo pendiente y
    un fallo se representa como ``COMPLETO`` más ``result=ERROR``.
    """
    meta = meta if meta is not None else JOB_META.get(job.id, {})
    actual_status = str(getattr(job, "status", "PENDIENTE"))
    result_data = getattr(job, "result", None)
    result_data = result_data if isinstance(result_data, dict) else {}
    outcome = result_data.get("result")
    if actual_status == "FALLIDO":
        outcome = "ERROR"
    if outcome not in ("OK", "PARCIAL", "ERROR"):
        outcome = None

    bot = str(getattr(job, "bot", ""))
    raw_operation = str(getattr(job, "operation", ""))
    operation, operation_legacy = _project_operation(bot, raw_operation)
    public_status = {
        "ASIGNADO": "PENDIENTE",
        "FALLIDO": "COMPLETO",
    }.get(actual_status, actual_status)

    raw_error = meta.get("error") or result_data.get("error")
    if outcome == "ERROR" and not raw_error:
        raw_error = {"error_code": "unexpected"}
    error, error_detail = _project_job_error(raw_error, outcome, meta)

    started_at = meta.get("started_at") or getattr(job, "started_at", None)
    finished_at = meta.get("finished_at") or getattr(job, "finished_at", None)
    if actual_status not in ("COMPLETO", "FALLIDO", "CANCELADO"):
        finished_at = None
    projected = {
        "job_id": str(getattr(job, "id", "")),
        "status": public_status,
        "status_detail": actual_status,
        "result": outcome,
        "bot": bot,
        "operation": operation,
        "created_at": _iso_utc(getattr(job, "created_at", None)),
        "started_at": _iso_utc(started_at),
        # No se inventa la fecha de terminación si la metadata no la guardó.
        "finished_at": _iso_utc(finished_at),
        "cancel_reason": meta.get("cancel_reason"),
        "cancelled_by": _legacy_cancelled_by(meta.get("cancelled_by")),
        "error": error,
        "error_detail": error_detail,
        "files": _project_job_files(meta.get("files")),
        "data": meta.get("data") if actual_status == "COMPLETO" else None,
    }
    if operation_legacy is not None:
        projected["operation_legacy"] = operation_legacy
    return projected


def _iso_utc(value: Any) -> str | None:
    """Serializa datetimes como ISO-8601 UTC terminado en ``Z``."""
    if value is None:
        return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    elif isinstance(value, datetime):
        parsed = value
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# Nombre de operacion que publicaba V2 cuando no se deduce de la ruta publica.
_OPERACION_LEGACY_V2: dict[tuple[str, str], str] = {
    ("vep_ccma", "generar"): "consulta",
    ("comprobantes", "consultar"): "consulta",
    ("comprobantes", "historial"): "historial",
    ("comprobantes", "solicitar"): "solicitar_consulta",
    ("mis_comprobantes", "historial"): "historial",
    ("libros_portal_iva", "descargar_libros"): "consulta",
}


def _project_operation(bot: str, operation: str) -> tuple[str, str | None]:
    """Normaliza nombres históricos de operación para la respuesta pública."""
    legado = _OPERACION_LEGACY_V2.get((bot, operation))
    if legado:
        return operation, legado
    if bot == "apoc" and operation in ("consulta", "consultar"):
        # La ruta pública es GET /apoc/consulta/{cuit} (sin alias en
        # BOT_ROUTE_ALIASES): se publica igualmente el nombre histórico.
        return "consultar", "consulta"
    if bot == "consulta_cuit" and operation in ("consulta", "consultar"):
        # Este nombre histórico está registrado en OPERATIONS, aunque la ruta
        # V1 correspondiente usa el sufijo ``individual``.
        return "consultar", "consulta"
    try:
        # Import local evita un ciclo durante la creación de los routers.
        from central_api.api.bot_compat import BOT_ROUTE_ALIASES
    except ImportError:  # pragma: no cover - fallback de importación parcial
        return operation, None
    for path, alias_bot, canonical in BOT_ROUTE_ALIASES:
        if alias_bot != bot:
            continue
        legacy = path.rsplit("/", 1)[-1]
        if operation in (canonical, legacy):
            return canonical, legacy if legacy != canonical else None
    return operation, None


_JOB_ERROR_MESSAGES: dict[str, tuple[str, str]] = {
    "validation": (
        "Algunos datos enviados no tienen el formato esperado.",
        "Revise los campos de entrada y vuelva a enviar la solicitud.",
    ),
    "authentication": (
        "Los datos de acceso no fueron aceptados.",
        "Revise las credenciales de la cuenta e inténtelo nuevamente.",
    ),
    "invalid_credentials": (
        "Los datos de acceso no fueron aceptados.",
        "Revise las credenciales de la cuenta e inténtelo nuevamente.",
    ),
    "external_timeout": (
        "El servicio consultado tardó demasiado en responder.",
        "Inténtelo nuevamente más tarde.",
    ),
    "timeout": (
        "El servicio consultado tardó demasiado en responder.",
        "Inténtelo nuevamente más tarde.",
    ),
    "navigation": (
        "No se pudo acceder a la información solicitada.",
        "Verifique los datos de entrada e inténtelo nuevamente.",
    ),
    "query": (
        "No se encontró la información solicitada.",
        "Verifique los filtros y el período consultado.",
    ),
    "download": (
        "No se pudo obtener el archivo solicitado.",
        "Confirme que siga disponible y vuelva a intentarlo.",
    ),
    "processing": (
        "No se pudo procesar la solicitud.",
        "Revise los datos enviados e inténtelo nuevamente.",
    ),
    "storage": (
        "No se pudo guardar o recuperar el resultado.",
        "Inténtelo nuevamente más tarde.",
    ),
    "service_not_enabled": (
        "El servicio no está disponible temporalmente.",
        "Inténtelo nuevamente más tarde.",
    ),
    "infrastructure_error": (
        "El servicio no está disponible temporalmente.",
        "Inténtelo nuevamente más tarde.",
    ),
    "credentials_rejected": (
        "El portal rechazó la clave o el CUIT de acceso.",
        "Verifique el CUIT y la clave fiscal, y que la relación con el representado esté vigente.",
    ),
    "captcha_unsolvable": (
        "No se pudo superar la verificación de seguridad del portal.",
        "Inténtelo nuevamente en unos minutos.",
    ),
    "target_unavailable": (
        "El portal no respondió como se esperaba o no se encontró el dato solicitado.",
        "Verifique que el CUIT del representado y el período existan y estén habilitados, y reintente más tarde.",
    ),
    "deadline_exceeded": (
        "La consulta superó el tiempo máximo de ejecución.",
        "Reduzca el rango de fechas o la cantidad de datos solicitados e inténtelo nuevamente.",
    ),
    "chromium_crashed": (
        "La ejecución se interrumpió por un problema interno.",
        "Inténtelo nuevamente; si el problema continúa, contacte al soporte.",
    ),
    "worker_oom": (
        "La ejecución requirió más recursos de los disponibles.",
        "Reduzca el rango de datos solicitado e inténtelo nuevamente.",
    ),
    "artifact_upload_failed": (
        "No se pudieron guardar los archivos generados.",
        "Inténtelo nuevamente más tarde.",
    ),
    "cancellation_requested": (
        "La ejecución fue cancelada.",
        "Cree una nueva solicitud si todavía necesita el resultado.",
    ),
    "envelope_invalid": (
        "Los datos enviados no son válidos para esta operación.",
        "Revise los campos obligatorios, formatos y valores permitidos de la operación y vuelva a enviarla.",
    ),
    "internal": (
        "Ocurrió un problema interno al completar la solicitud.",
        "Inténtelo nuevamente; si el problema continúa, contacte al soporte.",
    ),
    "unexpected": (
        "Ocurrió un problema al completar la solicitud.",
        "Inténtelo nuevamente; si el problema continúa, contacte al soporte.",
    ),
}


def _project_job_error(
    value: Any, outcome: str | None, meta: dict
) -> tuple[list[str] | None, dict[str, str] | None]:
    """Traduce errores a mensajes españoles seguros y conserva detalle útil."""
    if outcome == "OK" or value in (None, {}, [], ""):
        return None, None
    if not isinstance(value, (dict, str, list)):
        value = {"error_code": "unexpected"}
    if isinstance(value, dict):
        candidate = (
            value.get("error_code") or value.get("category")
            or value.get("error_category") or value.get("code")
        )
        correlation = value.get("correlation_id") or meta.get("correlation_id")
    else:
        candidate = value if isinstance(value, str) else None
        correlation = meta.get("correlation_id")
    code = str(candidate or "unexpected").strip().lower()
    if code not in _JOB_ERROR_MESSAGES:
        code = "unexpected"
    cause, message = _JOB_ERROR_MESSAGES[code]
    detail = {"error_code": code, "cause": cause, "message": message}
    if isinstance(correlation, str) and 0 < len(correlation) <= 128:
        detail["correlation_id"] = correlation
    return [message], detail


def _project_job_files(value: Any) -> list[dict[str, Any]]:
    """Devuelve siempre listas de archivos con campos legacy y metadata V3."""
    if not isinstance(value, list):
        return []
    files: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, str):
            filename = item.rsplit("/", 1)[-1] or "archivo"
            direct_url = item if item.startswith(("http://", "https://")) else None
            item = {"name": filename, "url": direct_url}
        if not isinstance(item, dict):
            continue
        filename = item.get("filename") or item.get("name") or "archivo"
        size = item.get("size")
        if size is None:
            size = item.get("size_bytes")
        # V2 schema requires a string; use the historical empty-string value
        # when storage cannot provide a direct presigned URL.
        url = item.get("url") or ""
        files.append({
            "name": item.get("name") or filename,
            "url": url,
            "size": size,
            "artifact_id": item.get("artifact_id"),
            "filename": filename,
            "content_type": item.get("content_type"),
            "size_bytes": item.get("size_bytes") if item.get("size_bytes") is not None else size,
            "sha256": item.get("sha256"),
            "download_url": item.get("download_url"),
            "url_expires_in": item.get("url_expires_in") or (3600 if url else None),
        })
    return files


def _legacy_cancelled_by(value: Any) -> Any:
    if isinstance(value, str):
        return value.lower()
    return value


__all__ = [
    "IDEMPOTENCY",
    "correlation_id",
    "fingerprint_payload",
    "current_user_id",
    "require_api_principal",
    "IdempotencyHit",
    "check_idempotency",
    "remember_idempotency",
    "idempotency_conflict_response",
]
