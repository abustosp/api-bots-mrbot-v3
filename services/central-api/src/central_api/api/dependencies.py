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

from fastapi import Header, HTTPException, Request, Security
from fastapi.responses import JSONResponse
from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBasic,
    HTTPBasicCredentials,
    HTTPBearer,
)

from central_api.security.api_keys import parse_api_key, verify_presented_secret
from central_api.security.bearer import decode_bearer
from central_api.security.principals import ANONYMOUS_USER_ID, ApiPrincipal
from central_api.security.secret_redaction import public_error
from central_api.settings import get_settings

# Registro de idempotencia en memoria (en PG: tabla con UNIQUE + retención
# IDEMPOTENCY_RETENTION_DAYS, reutilizable tras estado terminal, S-2).
IDEMPOTENCY: dict[tuple[str, str], dict] = {}

bearer_scheme = HTTPBearer(
    scheme_name="HTTPBearer",
    bearerFormat="b64(usuario).b64(api_key)",
    description=(
        "Token base64url(usuario).base64url(api_key). Se obtiene con "
        "POST /api/v3/auth/token. Alternativas: HTTPBasic o headers "
        "email + X-API-Key (forma V1)."
    ),
    auto_error=False,
)

basic_scheme = HTTPBasic(
    scheme_name="HTTPBasic",
    description="Usuario (email o alias) como username y la API key como password.",
    auto_error=False,
)

api_key_header_scheme = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ApiKeyHeader",
    description="API key en el request (forma V1). Usar junto con el header email.",
    auto_error=False,
)

email_header_scheme = APIKeyHeader(
    name="email",
    scheme_name="UserHeader",
    description="Usuario (email o alias) dueño de la API key enviada en X-API-Key (forma V1).",
    auto_error=False,
)


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
        headers={"WWW-Authenticate": "Bearer"},
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
        fila = (
            await sesion.execute(
                select(ApiKey).where(
                    ApiKey.key_prefix == key_id,
                    ApiKey.revoked_at.is_(None),
                    (ApiKey.expires_at.is_(None))
                    | (ApiKey.expires_at > func.current_timestamp()),
                )
            )
        ).scalar_one_or_none()
        verificador = fila.verifier_hmac if fila is not None else None
        if not verify_presented_secret(hmac_secret, secret, verificador):
            return None
        if fila is None:
            return None  # dummy comparado: no existe, sin oráculo
        cuenta = await sesion.get(User, fila.user_id)
        if cuenta is None or not cuenta.habilitado:
            return None
        if identidad is not None and cuenta.email.strip().casefold() != identidad.strip().casefold():
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
    """Valida clave, propietario, vigencia y estado para tokens Bearer o login."""
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
    bearer_credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    basic_credentials: HTTPBasicCredentials | None = Security(basic_scheme),
    x_api_key: str | None = Security(api_key_header_scheme),
    email: str | None = Security(email_header_scheme),
) -> ApiPrincipal:
    """Autentica al cliente por cualquiera de los tres métodos admitidos.

    1. ``Authorization: Bearer b64(usuario).b64(api_key)``.
    2. ``Authorization: Basic b64(usuario:api_key)``.
    3. Headers ``email`` + ``X-API-Key`` (forma V1). ``X-API-Key`` sin
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

    if bearer_credentials is not None:
        try:
            identidad, api_key = decode_bearer(bearer_credentials.credentials)
        except ValueError:
            raise _no_autorizado() from None
        principal = await authenticate_api_key(identidad, api_key)
        if principal is not None:
            return principal
        raise _no_autorizado()

    if authorization:
        # No degradar Authorization mal formado a la autenticación abierta de
        # desarrollo cuando el esquema HTTP no es Bearer.
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


def check_idempotency(
    user_id: str, key: str, fingerprint: str
) -> IdempotencyHit:
    """Busca la llave: replay exacto, conflicto, o vía libre (S-2)."""
    if not key:
        return IdempotencyHit()
    record = IDEMPOTENCY.get((user_id, key))
    if record is None:
        return IdempotencyHit()
    if record["fingerprint"] == fingerprint:
        return IdempotencyHit(job_id=record["job_id"])
    return IdempotencyHit(conflict=True)


def remember_idempotency(user_id: str, key: str, fingerprint: str, job_id: str) -> None:
    """Persiste la identidad de idempotencia junto al job creado."""
    if key:
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
    """Proyecta un ``Job`` a la forma V2 ``JobStatusResponse`` (plan 02 §3.6)."""
    from central_api.store import utcnow

    meta = meta if meta is not None else JOB_META.get(job.id, {})
    created = job.created_at.isoformat().replace("+00:00", "Z") if job.created_at else None
    result = job.result or {}
    status = job.status
    outcome = result.get("result") if isinstance(result, dict) else None
    if status == "FALLIDO":
        outcome = "ERROR"
    return {
        "job_id": job.id,
        "status": status,
        "result": outcome,
        "bot": job.bot,
        "operation": job.operation,
        "created_at": created,
        "started_at": meta.get("started_at"),
        "finished_at": meta.get("finished_at") or (
            utcnow().isoformat().replace("+00:00", "Z")
            if status in ("COMPLETO", "FALLIDO", "CANCELADO") else None
        ),
        "cancel_reason": meta.get("cancel_reason"),
        "cancelled_by": meta.get("cancelled_by"),
        "error": meta.get("error") or result.get("error") if isinstance(result, dict) else None,
        "files": meta.get("files", []),
        "data": meta.get("data") if status == "COMPLETO" else None,
    }


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
