"""API secundaria del worker (`/internal/v1`): asignacion, cancelacion y salud.

Solo la central la consume, en red privada, con Bearer [REDACTED] worker.
Liveness (`GET health`) es anonima y sin dependencias externas.
Nada aqui toca base de datos, RSA privada ni claves de bucket (W-1/SEC-3).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from bot_worker.bots.errors import ArtifactUploadError, ErrorDeBot
from bot_worker.bots.registry import get_plugin
from bot_worker.config import PROTOCOL_VERSION, WorkerConfig, WorkerSettings, parse_args
from bot_worker.schemas import BotSchemaDocument, get_schema_document
from bot_worker.reporting.central import (
    PresignError,
    request_presign,
    send_cancel_ack,
)
from bot_worker.reporting.heartbeat import START_MONO, heartbeat_loop
from bot_worker.reporting.results import (
    ResultStore,
    idempotency_key,
    send_result,
)
from bot_worker.runtime.arca_login import ArcaLoginError
from bot_worker.runtime.browser import (
    BrowserCrashedError,
    BrowserUnavailableError,
    build_browser_factory,
)
from bot_worker.runtime.captcha import CaptchaSolver, CaptchaUnsolvableError
from bot_worker.runtime.sealed import (
    SealedEnvelopeError,
    decrypt_sealed_section,
    generate_sealed_keypair,
)
from bot_worker.security.assignments import (
    ASSIGNMENT_SCOPE_ASSIGN_FORCE,
    AssignmentDenied,
    load_verify_key,
    verify_assignment,
)
from bot_worker.runtime.context import (
    ArtifactStore,
    BotRuntime,
    CancellationToken,
    DeadlineBudget,
    FiscalCredentials,
    JobCancelled,
)
from bot_worker.runtime.proxies import build_proxy_config
from bot_worker.runtime.workdir import cleanup_workdir, new_workdir
from bot_worker.scheduler.supervisor import (
    JobSupervisor,
    LeaseConflict,
    LocalJob,
    WorkerDraining,
    WorkerSaturated,
)

log = logging.getLogger("bot_worker.api")
# Un diagnostic_code es un identificador fijo del código (p. ej.
# "arca_login_rejected"). Cualquier otra cosa se descarta: podría ser texto
# del sitio externo o un secreto interpolado.
_DIAGNOSTIC_CODE_RE = re.compile(r"[a-z][a-z0-9_]{2,63}")
_CLASS_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}")

# Reintentos de registro durante el arranque para fallos de red temporales.
# Cinco intentos en total, con una espera creciente entre ellos.
REGISTER_RETRY_DELAYS_SECONDS = (1.0, 2.0, 5.0, 10.0)


def _now_z() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
    )


def _parse_z(value: str | None) -> float | None:
    if not value:
        return None
    try:
        text = value.replace("Z", "+00:00")
        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None


def _valid_uuid(value: str) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


# ---------------------------------------------------------------- modelos ---


class ArtifactSlotIn(BaseModel):
    """Slot de artefacto que la central preasigna para una ejecución."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "artifact_id": "artifact-01",
                    "name_hint": "constancia.pdf",
                    "put_url": "https://storage.example.invalid/put",
                    "object_key": "jobs/01/constancia.pdf",
                    "max_bytes": 52428800,
                    "content_types": ["application/pdf"],
                }
            ]
        }
    )

    artifact_id: str
    name_hint: str = ""
    put_url: str = ""
    object_key: str = ""
    max_bytes: int = 52_428_800
    content_types: list[str] = Field(default_factory=list)


class JobEnvelope(BaseModel):
    """Sobre firmada que la central entrega al worker para ejecutar un job."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "description": (
                "Contrato interno central-worker. La firma, el job, la lease "
                "y la versión de protocolo se validan antes de ejecutar."
            ),
            "examples": [
                {
                    "protocol_version": 1,
                    "sealed": {
                        "alg": "RSA-OAEP-SHA256+Fernet",
                        "enc_key_b64": "BASE64_SEALED_KEY",
                        "blob_b64": "BASE64_SEALED_PAYLOAD",
                    },
                    "sealed_section": {
                        "alg": "RSA-OAEP-SHA256+Fernet",
                        "enc_key_b64": "BASE64_SEALED_KEY",
                        "blob_b64": "BASE64_SEALED_PAYLOAD",
                    },
                    "job_id": "0190c2d4-7b4a-7b5f-9f28-3efc1f7b1b10",
                    "attempt": 1,
                    "lease_id": "0190c2d4-7b4a-7b60-9f28-3efc1f7b1b10",
                    "lease_expires_at": "2026-09-24T03:00:00Z",
                    "assignment_token": "issued-in-memory-by-central",
                    "bot": "ccma",
                    "plugin": "ccma",
                    "plugin_version": "3.0.0",
                    "operation": "consultar",
                    "deadline_at": "2026-09-24T03:05:00Z",
                    "idempotency_key": "job-0190c2d4-attempt-1",
                    "payload": {"representado_cuit": "20123456789"},
                    "credentials": {
                        "cuit_representante": "20123456789",
                        "clave": "REEMPLAZAR_CON_CREDENCIAL_SELLADA",
                    },
                    "proxy_profile": {"mode": "direct", "country": "ar"},
                    "artifact_uploads": [
                        {
                            "artifact_id": "artifact-01",
                            "name_hint": "constancia.pdf",
                            "put_url": "https://storage.example.invalid/put",
                            "object_key": "jobs/0190c2d4/constancia.pdf",
                            "max_bytes": 52428800,
                            "content_types": ["application/pdf"],
                        }
                    ],
                    "callback": {
                        "events_url": "https://central-api.mrbot.com.ar/internal/v1/jobs/events"
                    },
                    "assignment_signature": "base64-ed25519-signature",
                    "assignment_expires_at": "2026-09-24T03:00:00Z",
                    "captcha_profile": {"provider": "disabled"},
                    "service_profile": {"name": "development"},
                    "force": False,
                }
            ],
        },
    )

    protocol_version: int = PROTOCOL_VERSION
    # La central envía la bandera histórica ``sealed: bool`` más la sección
    # ``sealed_section`` (forma alg/enc_key_b64/blob_b64); ambas se aceptan.
    sealed: dict[str, Any] | bool | None = None
    sealed_section: dict[str, Any] | None = None
    job_id: str = ""
    attempt: int = 0
    lease_id: str = ""
    lease_expires_at: str | None = None
    assignment_token: str = ""
    bot: str = ""  # nombre canónico del bot (la central también manda plugin)
    plugin: str = ""
    plugin_version: str = ""
    operation: str = ""
    deadline_at: str | None = None
    idempotency_key: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] | None = None
    proxy_profile: dict[str, Any] | None = None
    artifact_uploads: list[ArtifactSlotIn] = Field(default_factory=list)
    callback: dict[str, Any] | None = None
    assignment_signature: str = ""
    assignment_expires_at: str = ""
    captcha_profile: dict[str, Any] | None = None
    service_profile: dict[str, Any] | None = None
    force: bool = False


class CancelIn(BaseModel):
    """Solicitud firmada de cancelación cooperativa de una ejecución."""

    model_config = ConfigDict(
        extra="ignore",
        json_schema_extra={
            "description": (
                "La central firma esta solicitud. El worker no cambia el "
                "estado canónico, solo detiene cooperativamente el job."
            ),
            "examples": [
                {
                    "assignment_signature": "base64-ed25519-signature",
                    "assignment_expires_at": "2026-09-24T03:00:00Z",
                    "attempt": 1,
                    "lease_id": "0190c2d4-7b4a-7b60-9f28-3efc1f7b1b10",
                    "cancel_request_id": "0190c2d4-7b4a-7b61-9f28-3efc1f7b1b10",
                    "reason": "cancelado_por_usuario",
                    "requested_at": "2026-09-24T02:59:00Z",
                }
            ],
        },
    )

    assignment_signature: str = ""
    assignment_expires_at: str = ""
    attempt: int = 0
    lease_id: str = ""
    cancel_request_id: str = ""
    reason: str = "cancelado_por_usuario"
    requested_at: str | None = None


def unseal_envelope(env: JobEnvelope, privkey_pem: bytes) -> JobEnvelope:
    """Abre la sección sellada (si la hay) y la fusiona en memoria.

    Lo descifrado nunca se loguea ni se persiste: vive en el objeto en
    memoria que baja al runtime y se sanitiza antes del callback.
    """
    seccion = (
        env.sealed if isinstance(env.sealed, dict) else env.sealed_section
    )
    if not seccion:
        return env
    opened = decrypt_sealed_section(privkey_pem, seccion)
    merged = env.model_copy(deep=True)
    if isinstance(opened.get("credentials"), dict):
        merged.credentials = opened["credentials"]
    if isinstance(opened.get("proxy"), dict):
        merged.proxy_profile = opened["proxy"]
    if isinstance(opened.get("captcha"), dict):
        merged.captcha_profile = opened["captcha"]
    if isinstance(opened.get("service"), dict):
        merged.service_profile = opened["service"]
    slots = opened.get("artifact_uploads")
    if isinstance(slots, list):
        merged.artifact_uploads = [ArtifactSlotIn(**s) for s in slots if isinstance(s, dict)]
    merged.sealed = None  # el secreto abierto no vuelve a serializarse
    merged.sealed_section = None
    return merged


def envelope_errors(env: JobEnvelope) -> list[str]:
    """Validacion de sobre antes de reservar recursos (422 si falla)."""
    errors: list[str] = []
    if env.protocol_version != PROTOCOL_VERSION:
        errors.append("protocol_version incompatible")
    if not _valid_uuid(env.job_id):
        errors.append("job_id invalido")
    if not _valid_uuid(env.lease_id):
        errors.append("lease_id invalida")
    if env.attempt < 1:
        errors.append("attempt debe ser >= 1")
    lease_exp = _parse_z(env.lease_expires_at)
    if lease_exp is None or lease_exp <= time.time():
        errors.append("lease vencida o sin expiracion valida")
    nombre_plugin = env.plugin or env.bot
    plugin = get_plugin(nombre_plugin)
    if plugin is None:
        errors.append(f"plugin no soportado: {nombre_plugin}")
    elif env.operation not in plugin.manifest.operaciones:
        errors.append(f"operacion no soportada: {env.operation}")
    if (
        plugin is not None
        and plugin.manifest.requiere_credenciales_fiscales
        and not env.credentials
    ):
        errors.append("el plugin requiere credenciales fiscales")
    if len(env.artifact_uploads) > 16:
        errors.append("demasiados slots de artefacto")
    total = sum(s.max_bytes for s in env.artifact_uploads)
    if total > 262_144_000:
        errors.append("artefactos exceden 250 MiB por job")
    return errors


# ------------------------------------------------------------------ auth ---


def _central_headers(app: FastAPI, nodo: str) -> dict[str, str]:
    """Cabeceras worker→central: identidad + token de servicio del registro.

    El token vive solo en memoria desde el registro; nunca en entorno.
    """
    headers: dict[str, str] = {}
    if nodo:
        headers["X-Worker-Node"] = nodo
    token = getattr(app.state, "service_token", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _deny_firma(status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "code": "NO_FIRMADO",
            "message": "Solo la central puede ordenar ejecuciones.",
        },
    )


def verify_envelope_signature(request: Request, env: "JobEnvelope", *, scope: str) -> JSONResponse | None:
    """Gate de ejecución: verifica firma, vigencia y ligadura al sellado.

    Sin clave de central fijada (registro pendiente), sin firma, vencida o
    adulterada, devuelve el rechazo 403 sin ejecutar nada. Un sobre con
    ``force`` solo verifica bajo el alcance ``assign-force``: es la única
    vía que puede superar el cupo, y viene firmada por la central.
    """
    if env.force:
        scope = ASSIGNMENT_SCOPE_ASSIGN_FORCE
    key = getattr(request.app.state, "central_verify_key", None)
    sealed_for_signature = env.sealed_section
    if sealed_for_signature is None and isinstance(env.sealed, dict):
        # Compatibilidad con sobres previos que transportaban el objeto bajo
        # `sealed` y no tenían aún `sealed_section`.
        sealed_for_signature = env.sealed
    try:
        verify_assignment(
            key,
            scope=scope,
            job_id=env.job_id,
            attempt=env.attempt,
            lease_id=env.lease_id,
            expires_at=env.assignment_expires_at,
            sealed_section=sealed_for_signature,
            signature_b64=env.assignment_signature,
        )
    except AssignmentDenied:
        return _deny_firma(403)
    return None


def _node_from_advertised_url(url: str) -> str:
    """Extrae ``ip:port`` de la URL propia (stdlib, sin dependencias)."""
    rest = url.split("://", 1)
    if len(rest) != 2 or rest[0] not in ("http", "https"):
        raise ValueError("advertised_url debe ser http(s)://ip:port")
    host_port = rest[1].split("/", 1)[0].strip()
    if not host_port or ":" not in host_port:
        raise ValueError("advertised_url debe incluir ip:port")
    return host_port


def _resultado_error_de_bot(exc: ErrorDeBot) -> tuple[str, dict[str, Any]]:
    """Conserva la categoría pública de un error esperado de plugin.

    El diagnóstico del error puede contener detalles del sitio externo. Solo
    se conserva el nombre de clase y, si existe, el ``diagnostic_code`` fijo
    (identificador de código, nunca texto libre del sitio) para diagnóstico.
    """
    resultado: dict[str, Any] = {
        "result": "ERROR",
        "data": {},
        "internal": type(exc).__name__,
    }
    codigo = getattr(exc, "diagnostic_code", None)
    if isinstance(codigo, str) and _DIAGNOSTIC_CODE_RE.fullmatch(codigo):
        resultado["diagnostic_code"] = codigo
    # Tipo de la excepción original (p. ej. TimeoutError de Playwright): es un
    # nombre de clase, nunca su mensaje, así que no arrastra texto del sitio.
    causa = exc.__cause__
    if causa is not None and _CLASS_NAME_RE.fullmatch(type(causa).__name__):
        resultado["cause"] = type(causa).__name__
    return exc.categoria, resultado


async def _register_once(
    client: Any,
    settings: WorkerConfig,
    pubkey_pem: str,
    instance_nonce: str,
) -> tuple[str | None, str, str]:
    """Alta en la central con la pública efímera.

    Devuelve ``(nodo, service_token, verify_key_pem)``. El token autoriza las
    llamadas worker→central y la clave fija la verificación de asignaciones;
    ambos viven solo en memoria (la central los provisionó, sin entorno).
    """
    if not settings.advertised_url:
        log.warning("sin advertised_url: sin registro, solo latidos con worker_id")
        return None, "", ""
    import httpx

    try:
        node = _node_from_advertised_url(settings.advertised_url)
    except ValueError as exc:
        log.warning("advertised_url inválida (%s): sin registro", exc)
        return None, "", ""
    url = f"{settings.central_url.rstrip('/')}/internal/v1/workers/register"
    body = {
        "protocol_version": PROTOCOL_VERSION,
        "instance_nonce": instance_nonce,
        "advertised_url": settings.advertised_url,
        "capacity": settings.worker_concurrency,
        "capabilities": [],
        "build_version": settings.image_version,
        "sealed_pubkey_pem": pubkey_pem,
    }
    for intento in range(len(REGISTER_RETRY_DELAYS_SECONDS) + 1):
        try:
            resp = await client.post(url, json=body, timeout=5.0)
        except httpx.TransportError as exc:
            if intento < len(REGISTER_RETRY_DELAYS_SECONDS):
                espera = REGISTER_RETRY_DELAYS_SECONDS[intento]
                log.warning(
                    "registro con error transitorio (%s), reintento %s/%s en %ss",
                    type(exc).__name__, intento + 1,
                    len(REGISTER_RETRY_DELAYS_SECONDS), espera,
                )
                await asyncio.sleep(espera)
                continue
            log.warning("registro fallido tras reintentos: %s", type(exc).__name__)
            return None, "", ""

        if 500 <= resp.status_code < 600:
            if intento < len(REGISTER_RETRY_DELAYS_SECONDS):
                espera = REGISTER_RETRY_DELAYS_SECONDS[intento]
                log.warning(
                    "registro con HTTP %s, reintento %s/%s en %ss",
                    resp.status_code, intento + 1,
                    len(REGISTER_RETRY_DELAYS_SECONDS), espera,
                )
                await asyncio.sleep(espera)
                continue
            log.warning("registro fallido tras reintentos: HTTP %s", resp.status_code)
            return None, "", ""

        if not 200 <= resp.status_code < 300:
            log.warning("registro rechazado: HTTP %s", resp.status_code)
            return None, "", ""
        try:
            data = resp.json()
        except (TypeError, ValueError):
            log.warning("registro rechazado: respuesta JSON inválida")
            return None, "", ""
        if not isinstance(data, dict) or data.get("accepted") is not True:
            log.warning("registro rechazado: respuesta sin accepted=true")
            return None, "", ""
        return (
            node,
            str(data.get("service_token") or ""),
            str(data.get("assignment_verify_key_pem") or ""),
        )
    return None, "", ""  # pragma: no cover - bucle termina en éxito o retorno


def create_app(settings: WorkerConfig | None = None) -> FastAPI:
    # Sin settings explícitos se parsea argv (proceso real vía run()).
    # Importar el módulo nunca lee entorno ni argv: app nula hasta run().
    settings = settings or parse_args()
    supervisor = JobSupervisor(
        configured=settings.worker_concurrency,
        queue_limit=settings.worker_local_queue_limit,
    )
    results = ResultStore()
    sealed_privkey, sealed_pubkey = generate_sealed_keypair()

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        import httpx

        stop = asyncio.Event()
        app.state.stop_heartbeat = stop
        client = httpx.AsyncClient()
        app.state.http = client
        # Nonce unico por proceso: evita que un reinicio secuestre el ID vivo.
        instance_nonce = uuid.uuid4().hex
        app.state.instance_nonce = instance_nonce
        node, service_token, verify_pem = await _register_once(
            client, settings, sealed_pubkey, instance_nonce
        )
        # Provisionado por la central en memoria: token para hablarle y
        # clave para verificar sus asignaciones. Nada de esto usa entorno.
        app.state.worker_node = node or ""
        app.state.service_token = service_token
        try:
            app.state.central_verify_key = (
                load_verify_key(verify_pem) if verify_pem else None
            )
        except AssignmentDenied as exc:
            log.warning("clave de central inválida (%s): sin gate", exc)
            app.state.central_verify_key = None
        task = asyncio.create_task(
            heartbeat_loop(
                client=client,
                base_url=settings.central_url,
                worker_id=node or settings.worker_id,
                protocol_version=PROTOCOL_VERSION,
                image_version=settings.image_version,
                supervisor=supervisor,
                interval=settings.heartbeat_interval_seconds,
                jitter=settings.heartbeat_jitter_seconds,
                instance_id=instance_nonce,
                service_token=service_token,
                get_state=lambda: (
                    ("DRENANDO", False)
                    if supervisor.draining
                    else (
                        ("SATURADO", True)
                        if supervisor.en_ejecucion >= supervisor.capacity
                        else ("SANO", True)
                    )
                ),
                stop=stop,
            )
        )
        yield
        # Drenaje ante SIGTERM (uvicorn lo convierte en cierre del
        # lifespan): se deja de aceptar trabajo nuevo, se avisa DRENANDO
        # a la central y se espera a los jobs en vuelo hasta
        # WORKER_DRAIN_TIMEOUT_SECONDS sin perderlos; al vencer se pide
        # cancelación cooperativa y se reporta antes de salir.
        await _drenar_al_apagar(app, settings, supervisor, client, stop, task)
        await client.aclose()

    app = FastAPI(title="mrbot bot-worker", version=settings.image_version)
    app.state.settings = settings
    app.state.supervisor = supervisor
    app.state.results = results
    app.state.sealed_privkey = sealed_privkey
    app.router.lifespan_context = lifespan

    @app.exception_handler(HTTPException)
    async def _http_errors(_: Request, exc: HTTPException) -> JSONResponse:
        detail = exc.detail
        body = (
            detail
            if isinstance(detail, dict)
            else {"code": "ERROR", "message": str(detail)}
        )
        return JSONResponse(status_code=exc.status_code, content=body)

    # ------------------------------------------------------------- salud ---
    @app.get("/internal/v1/health")
    async def health() -> dict[str, str]:
        # Liveness minima: sin auth y sin dependencias externas.
        return {"status": "ok"}

    @app.get(
        "/internal/v1/bots/{bot}/{operation}/schema",
        response_model=BotSchemaDocument,
        summary="Schema documental de una operación de bot",
        description=(
            "Devuelve el JSON Schema del body plano V1, los campos de "
            "credenciales y ejemplos documentales en formato OpenAPI. "
            "No crea ni asigna jobs."
        ),
        tags=["schemas"],
        responses={
            200: {
                "description": "Schema de request y ejemplos documentales, sin credenciales reales.",
                "content": {
                    "application/json": {
                        "examples": {
                            "ccma_consultar": {
                                "summary": "Schema de ejemplo para CCMA",
                                "description": "La respuesta real contiene el modelo de la pareja bot/operación solicitada.",
                                "value": get_schema_document("ccma", "consultar"),
                            }
                        }
                    }
                },
            }
        },
    )
    async def bot_schema(bot: str, operation: str) -> dict[str, Any]:
        return get_schema_document(bot, operation)

    # ------------------------------------------------------------ asignar ---
    @app.post("/internal/v1/jobs", status_code=202)
    async def accept_job(
        env: JobEnvelope,
        request: Request,
    ) -> JSONResponse:
        denegado = verify_envelope_signature(request, env, scope="assign")
        if denegado is not None:
            return denegado
        try:
            env = unseal_envelope(env, request.app.state.sealed_privkey)
        except SealedEnvelopeError:
            return JSONResponse(
                status_code=422,
                content={
                    "code": "SOBRE_SELLADO_INVALIDO",
                    "message": "La sección sellada no pudo abrirse.",
                },
            )
        problems = envelope_errors(env)
        if problems:
            return JSONResponse(
                status_code=422,
                content={
                    "code": "SOBRE_INVALIDO",
                    "message": "; ".join(problems),
                },
            )
        accepted_at = _now_z()
        body = {
            "accepted": True,
            "job_id": env.job_id,
            "attempt": env.attempt,
            "lease_id": env.lease_id,
            "local_state": "EN_COLA",
            "queue_position": 0,
            "accepted_at": accepted_at,
            "protocol_version": PROTOCOL_VERSION,
        }
        job = LocalJob(
            job_id=env.job_id,
            attempt=env.attempt,
            lease_id=env.lease_id,
            plugin=env.plugin,
            operation=env.operation,
            accepted_at=accepted_at,
            deadline_at=env.deadline_at,
        )
        try:
            acceptance = await supervisor.accept(job, body, force=bool(env.force))
        except WorkerDraining:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "WORKER_DRENANDO",
                    "message": "El worker no acepta trabajo nuevo.",
                },
            )
        except LeaseConflict:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "LEASE_EN_CONFLICTO",
                    "message": "El intento ya esta asignado a otra lease.",
                },
            )
        except WorkerSaturated:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "WORKER_SATURADO",
                    "message": "El worker no tiene capacidad disponible.",
                    "capacity": supervisor.capacity,
                    "en_ejecucion": supervisor.en_ejecucion,
                    "en_cola": supervisor.en_cola,
                    "retry_after_seconds": 5,
                },
            )
        if acceptance.duplicate:
            cached = supervisor.duplicate_body(
                env.job_id, env.attempt, env.lease_id
            )
            return JSONResponse(status_code=202, content=cached or body)
        task = asyncio.create_task(_run_job(request.app, env, job))
        supervisor.track_task(job.key, task)
        return JSONResponse(status_code=202, content=body)

    # ------------------------------------------------------------ listar ---
    @app.get("/internal/v1/jobs")
    async def list_jobs(
        request: Request,
        state: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=100),
        cursor: str | None = Query(default=None),
    ) -> dict[str, Any]:
        states: set[str] | None = None
        if state:
            states = {s.strip().upper() for s in state.split(",") if s.strip()}
            valid = {
                "EN_COLA",
                "PREPARANDO",
                "CORRIENDO",
                "SUBIENDO",
                "REPORTANDO",
                "CANCELANDO",
            }
            if not states.issubset(valid):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "FILTRO_INVALIDO",
                        "message": "state contiene valores desconocidos.",
                    },
                )
        try:
            offset = int(cursor) if cursor else 0
        except ValueError:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "CURSOR_INVALIDO",
                    "message": "cursor opaco invalido.",
                },
            )
        items = supervisor.items()
        if states is not None:
            items = [j for j in items if j.local_state in states]
        page = items[offset : offset + limit]
        # Nunca payload, credenciales, URLs prefirmadas ni rutas locales.
        return {
            "items": [
                {
                    "job_id": j.job_id,
                    "attempt": j.attempt,
                    "lease_id": j.lease_id,
                    "plugin": j.plugin,
                    "operation": j.operation,
                    "local_state": j.local_state,
                    "accepted_at": j.accepted_at,
                    "started_at": j.started_at,
                    "deadline_at": j.deadline_at,
                }
                for j in page
            ],
            "next_cursor": (
                str(offset + limit)
                if offset + limit < len(items)
                else None
            ),
            "en_ejecucion": supervisor.en_ejecucion,
            "en_cola": supervisor.en_cola,
        }

    # ----------------------------------------------------------- cancelar ---
    @app.post("/internal/v1/jobs/{job_id}/cancel")
    async def cancel_job(
        job_id: str,
        body: CancelIn,
        request: Request,
    ) -> JSONResponse:
        try:
            verify_assignment(
                getattr(request.app.state, "central_verify_key", None),
                scope="cancel",
                job_id=job_id,
                attempt=body.attempt or 0,
                lease_id=body.lease_id,
                expires_at=body.assignment_expires_at,
                sealed_section=None,
                signature_b64=body.assignment_signature,
            )
        except AssignmentDenied:
            return _deny_firma(403)
        sup: JobSupervisor = request.app.state.supervisor
        job = sup.get(job_id, body.attempt or None)
        if job is None:
            return JSONResponse(
                status_code=404,
                content={
                    "code": "DESCONOCIDO",
                    "message": "El worker no conoce ese intento.",
                },
            )
        if body.lease_id != job.lease_id:
            return JSONResponse(
                status_code=409,
                content={
                    "code": "LEASE_EN_CONFLICTO",
                    "message": "La lease no coincide con el intento.",
                },
            )
        if job.local_state in ("TERMINADO", "CANCELADO"):
            return JSONResponse(
                status_code=200,
                content={
                    "job_id": job.job_id,
                    "local_state": job.local_state,
                    "accepted": True,
                },
            )
        sup.request_cancel(job)
        token: CancellationToken | None = _cancel_tokens.pop(
            job.key, None
        )
        if token is not None:
            token.cancel()
        # Acuse cooperativo ante la central (best-effort, sin tumbar el 202).
        settings: WorkerConfig = request.app.state.settings
        await send_cancel_ack(
            getattr(request.app.state, "http", None),
            settings.central_url,
            job_id=job.job_id,
            accepted=True,
            attempt=body.attempt or job.attempt,
            lease_id=body.lease_id,
            cancel_request_id=body.cancel_request_id,
            worker_node=_central_node(request.app),
            timeout_seconds=settings.callback_timeout_seconds,
            service_token=getattr(request.app.state, "service_token", ""),
        )
        # Repetir con igual (job, attempt, lease, cancel_request_id) es idem.
        return JSONResponse(
            status_code=202,
            content={
                "job_id": job.job_id,
                "local_state": job.local_state,
                "accepted": True,
            },
        )

    # ------------------------------------------------------------- estado ---
    @app.get("/internal/v1/status")
    async def status(
        request: Request
    ) -> dict[str, Any]:
        sup: JobSupervisor = request.app.state.supervisor
        st: WorkerConfig = request.app.state.settings
        in_flight = sup.en_ejecucion
        if sup.draining:
            state = "DRENANDO"
        elif in_flight >= sup.capacity:
            state = "SATURADO"
        else:
            state = "SANO"
        return {
            "worker_id": st.worker_id,
            "state": state,
            "accepting_jobs": not sup.draining,
            "en_ejecucion": in_flight,
            "en_cola": sup.en_cola,
            "capacity": sup.capacity,
            "available_capacity": sup.available_capacity,
            "protocol_version": PROTOCOL_VERSION,
            "image_version": st.image_version,
            "bots_supported": [
                f"{p.manifest.nombre}@{p.manifest.version}"  # type: ignore[union-attr]
                for p in _registry_plugins()
            ],
            "resources": _resources(in_flight),
            "counters_since_start": dict(sup.counters),
            "uptime_seconds": int(time.monotonic() - START_MONO),
            "observed_at": _now_z(),
        }

    # --------------------------------------------------------------- bots ---
    @app.get("/internal/v1/bots")
    async def bots(
        request: Request
    ) -> dict[str, Any]:
        st: WorkerConfig = request.app.state.settings
        return {
            "protocol_version": PROTOCOL_VERSION,
            "image_version": st.image_version,
            "bots": [
                p.manifest.to_status_dict()  # type: ignore[union-attr]
                for p in _registry_plugins()
            ],
        }

    return app


def _registry_plugins() -> list[Any]:
    from bot_worker.bots.registry import REGISTRY

    return list(REGISTRY.values())


def _resources(chromium_processes: int) -> dict[str, Any]:
    import os

    mem_total, mem_avail = 0, 0
    try:
        with open("/proc/meminfo") as fh:
            info = {}
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    info[parts[0][:-1]] = int(parts[1]) * 1024
        mem_total = info.get("MemTotal", 0)
        mem_avail = info.get("MemAvailable", 0)
    except OSError:
        pass
    shm_avail = 0
    try:
        st = os.statvfs("/dev/shm")
        shm_avail = st.f_bavail * st.f_frsize
    except OSError:
        pass
    try:
        with open("/sys/fs/cgroup/pids.current") as fh:
            pids_current = int(fh.read().strip())
    except (OSError, ValueError):
        pids_current = 0
    return {
        "memory_available_bytes": mem_avail,
        "memory_current_bytes": max(0, mem_total - mem_avail),
        "shm_available_bytes": shm_avail,
        "chromium_processes": chromium_processes,
        "pids_current": pids_current,
        "cpu_count": os.cpu_count() or 0,
    }


_cancel_tokens: dict[tuple[str, int], CancellationToken] = {}


class _HttpEventSink:
    """Progreso best-effort hacia la central, sin secretos en el mensaje.

    Habla el cuerpo de la central (``event_id``/``event_type``/
    ``assignment_attempt``) con ``X-Worker-Node`` cuando hay nodo.
    """

    def __init__(
        self, client: Any, base_url: str, job_id: str,
        attempt: int = 0, worker_node: str = "", service_token: str = "",
    ) -> None:
        self._client = client
        self._base_url = base_url
        self._job_id = job_id
        self._attempt = attempt
        self._worker_node = worker_node
        self._service_token = service_token
        self._seq = 0

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self._worker_node:
            headers["X-Worker-Node"] = self._worker_node
        if self._service_token:
            headers["Authorization"] = f"Bearer {self._service_token}"
        return headers

    async def progress(
        self, phase: str, percent: int, message: str
    ) -> None:
        self._seq += 1
        extra: dict[str, Any] = {"headers": self._headers()} if self._headers() else {}
        try:
            await self._client.post(
                f"{self._base_url.rstrip('/')}/internal/v1/jobs/"
                f"{self._job_id}/events",
                json={
                    "event_id": f"evt-{self._job_id}-{self._attempt}-{self._seq}",
                    "event_type": "progress",
                    "assignment_attempt": self._attempt,
                    "message": f"{phase} {percent}%: {message}"[:256],
                },
                timeout=3.0,
                **extra,
            )
        except Exception:
            log.debug("evento de progreso descartado (transporte)")


def _sanitize(value: Any, secrets: list[str]) -> Any:
    """Quita secretos de memoria del resultado antes del callback."""
    if isinstance(value, dict):
        return {k: _sanitize(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret and secret in value:
                return "<redacted>"
    return value


async def _run_job(app: FastAPI, env: JobEnvelope, job: LocalJob) -> None:
    """Vida completa del job bajo semaforo, con cleanup garantizado."""
    settings: WorkerConfig = app.state.settings
    supervisor: JobSupervisor = app.state.supervisor
    store: ResultStore = app.state.results
    async with supervisor.semaphore():
        cancellation = CancellationToken()
        _cancel_tokens[job.key] = cancellation
        workdir = None
        credentials: FiscalCredentials | None = None
        outcome = "fallido"
        try:
            if job.cancel_requested:
                cancellation.cancel()
            job.local_state = "PREPARANDO"
            job.started_at = _now_z()
            await _report_started(app, env, job)

            if env.credentials:
                credentials = FiscalCredentials(
                    cuit_representante=str(
                        env.credentials.get("cuit_representante", "")
                    ),
                    clave=str(env.credentials.get("clave", "")),
                )
            secrets = (
                [credentials.clave] if credentials and credentials.clave else []
            )
            _prov = env.captcha_profile or {}
            secrets += [
                str(v)
                for v in (
                    (env.proxy_profile or {}).get("password"),
                    _prov.get("arca_key"),
                    _prov.get("srt_key"),
                    (env.service_profile or {}).get("cuit_api_key"),
                )
                if v
            ]
            workdir = new_workdir(settings.work_dir, job.job_id, job.attempt)

            slots = {
                s.artifact_id: ArtifactSlotRef(
                    s.artifact_id,
                    s.put_url,
                    s.object_key,
                    s.max_bytes,
                    tuple(s.content_types),
                )
                for s in env.artifact_uploads
            }
            client = getattr(app.state, "http", None)
            central_node = _central_node(app)
            service_token = getattr(app.state, "service_token", "")
            # Provisión de la central (sobre sellado, en memoria): proxy,
            # captcha y endpoints de servicio. Sin provisión rigen los
            # defaults sin secretos (sin proxy, captcha deshabilitado,
            # endpoints públicos).
            proxy_cfg = build_proxy_config(env.proxy_profile)
            captcha_profile = (
                env.captcha_profile if isinstance(env.captcha_profile, dict) else {}
            )
            service_profile = (
                env.service_profile if isinstance(env.service_profile, dict) else {}
            )

            async def _presign(
                artifact_id: str, content_type: str, size_bytes: int
            ) -> dict[str, Any]:
                """Pide URL prefirmada a la central antes de subir.

                Solo se llama para slots sin URL (adicional o vencida). Un
                fallo aquí es ``ArtifactUploadError``: reintento de subida,
                nunca reejecución del bot.
                """
                if client is None:
                    raise ArtifactUploadError(
                        "sin canal a la central para presign"
                    )
                try:
                    return await request_presign(
                        client,
                        settings.central_url,
                        job_id=job.job_id,
                        artifact_id=artifact_id,
                        content_type=content_type,
                        size_bytes=size_bytes,
                        worker_node=central_node,
                        timeout_seconds=settings.callback_timeout_seconds,
                        service_token=service_token,
                        sealed_privkey=getattr(app.state, "sealed_privkey", None),
                    )
                except PresignError as exc:
                    raise ArtifactUploadError(str(exc)) from exc

            runtime = BotRuntime(
                job_id=job.job_id,
                work_dir=workdir,
                deadline=DeadlineBudget(
                    deadline_epoch=_parse_z(env.deadline_at)
                    or (time.time() + settings.job_default_timeout_seconds)
                ),
                credentials=credentials,
                proxy=proxy_cfg,
                artifact_store=ArtifactStore(  # type: ignore[arg-type]
                    workdir, slots, presign=_presign, http_client=client
                ),
                event_sink=_HttpEventSink(
                    client, settings.central_url, job.job_id,
                    attempt=job.attempt, worker_node=central_node,
                    service_token=service_token,
                ),
                browser_factory=build_browser_factory(proxy_cfg, captcha_profile),
                cancellation=cancellation,
            )
            base_plugin = get_plugin(env.plugin or env.bot)
            assert base_plugin is not None  # validado en admision
            # Copia por job: configure() aplica la sección service del
            # sobre sin mutar la instancia compartida del registro.
            plugin = copy.copy(base_plugin)
            if hasattr(plugin, "configure"):
                plugin.configure(service_profile)
            job.local_state = "CORRIENDO"
            timeout = min(
                runtime.deadline.remaining_seconds(),
                plugin.manifest.timeout_por_defecto_seconds,  # type: ignore[union-attr]
                settings.job_default_timeout_seconds,
            )
            try:
                validated = await plugin.validate(env.payload)
                result = await asyncio.wait_for(
                    plugin.execute(validated, runtime),  # type: ignore[arg-type]
                    timeout=max(1.0, timeout),
                )
                category: str | None = None
                bot_result = {
                    "result": result.result,
                    "data": _sanitize(result.data, secrets),
                    "artifacts": _sanitize(result.artifacts, secrets),
                    "warnings": result.warnings,
                    "metrics": result.metrics,
                }
                outcome = (
                    "completado"
                    if result.result in ("OK", "PARCIAL")
                    else "fallido"
                )
            except (asyncio.TimeoutError, TimeoutError):
                category, bot_result = "DEADLINE_EXCEEDED", {
                    "result": "ERROR",
                    "data": {},
                }
            except (JobCancelled, asyncio.CancelledError):
                category, bot_result = "CANCELLATION_REQUESTED", {
                    "result": "ERROR",
                    "data": {},
                }
                outcome = "cancelado"
            except ArcaLoginError:
                category, bot_result = "CREDENTIALS_REJECTED", {
                    "result": "ERROR",
                    "data": {},
                }
            except BrowserCrashedError:
                supervisor.counters["chromium_crashes"] += 1
                category, bot_result = "CHROMIUM_CRASHED", {
                    "result": "ERROR",
                    "data": {},
                }
            except BrowserUnavailableError:
                category, bot_result = "INTERNAL", {
                    "result": "ERROR",
                    "data": {},
                }
            except CaptchaUnsolvableError:
                category, bot_result = "CAPTCHA_UNSOLVABLE", {
                    "result": "ERROR",
                    "data": {},
                }
            except ArtifactUploadError:
                category, bot_result = "ARTIFACT_UPLOAD_FAILED", {
                    "result": "ERROR",
                    "data": {},
                }
            except ValueError:
                category, bot_result = "ENVELOPE_INVALID", {
                    "result": "ERROR",
                    "data": {},
                }
            except ErrorDeBot as exc:
                category, bot_result = _resultado_error_de_bot(exc)
            except Exception as exc:  # borde: sin texto crudo ni stack
                log.exception("falla interna de plugin (redactada)")
                category, bot_result = "INTERNAL", {
                    "result": "ERROR",
                    "data": {},
                }
                bot_result["internal"] = type(exc).__name__
            if job.cancel_requested and outcome != "cancelado":
                category = "CANCELLATION_REQUESTED"
                outcome = "cancelado"
                bot_result = {"result": "ERROR", "data": {}}

            # Cuerpo que espera la central (ResultBody): resultado terminal
            # ("OK" completa, otro valor falla), datos, error y el intento
            # que la central usa para aplicar y deducir idempotencia.
            datos = bot_result.get("data") or {}
            payload = {
                "result": (
                    bot_result.get("result", "OK") if outcome == "completado"
                    else "CANCELADO" if outcome == "cancelado" else "ERROR"
                ),
                "data": datos if isinstance(datos, dict) else {},
                "artifacts": bot_result.get("artifacts", []),
                "error": (
                    None if outcome == "completado"
                    else {
                        "error_code": category or "unexpected",
                        **(
                            {"diagnostic_code": bot_result["diagnostic_code"]}
                            if bot_result.get("diagnostic_code") else {}
                        ),
                        **({"cause": bot_result["cause"]} if bot_result.get("cause") else {}),
                    }
                ),
                "assignment_attempt": job.attempt,
                "event_id": idempotency_key(job.job_id, job.attempt),
                "error_category": category,
            }
            if outcome == "cancelado":
                # Acuse cooperativo: el job se detuvo sin completar.
                # Va antes del reporte para no perderse si ya se reportó.
                job.local_state = "REPORTANDO"
                await send_cancel_ack(
                    client,
                    settings.central_url,
                    job_id=job.job_id,
                    accepted=True,
                    attempt=job.attempt,
                    lease_id=job.lease_id,
                    worker_node=central_node,
                    timeout_seconds=settings.callback_timeout_seconds,
                )
            job.local_state = "REPORTANDO"
            if await store.already_reported(job.job_id, job.attempt):
                return
            callback_base = (env.callback or {}).get(
                "base_url", settings.central_url
            )
            ok = await send_result(
                client,
                callback_base,
                job.job_id,
                payload,
                timeout_seconds=settings.callback_timeout_seconds,
                worker_node=central_node,
                service_token=service_token,
            )
            if ok:
                await store.mark_reported(job.job_id, job.attempt, payload)
            else:
                log.warning("resultado sin confirmar; se conserva la tarea")
        finally:
            # Cleanup garantizado: browser via factory, workspace, secreto.
            _cancel_tokens.pop(job.key, None)
            cleanup_workdir(workdir)
            credentials = None
            job.local_state = (
                "CANCELADO" if outcome == "cancelado" else "TERMINADO"
            )
            supervisor.release(job, outcome)


async def _drenar_al_apagar(
    app: FastAPI,
    settings: WorkerConfig,
    supervisor: JobSupervisor,
    client: Any,
    stop: asyncio.Event,
    heartbeat_task: asyncio.Task[None],
) -> None:
    """Secuencia de drenaje: DRENANDO, espera, cancelación al vencer.

    No pierde jobs: los en vuelo siguen hasta terminar o hasta
    ``WORKER_DRAIN_TIMEOUT_SECONDS``; solo al vencer se señala
    cancelación cooperativa (el runner reporta cada job antes de salir
    y la central reencola por lease vencida lo no reportado).
    """
    from bot_worker.reporting.heartbeat import (
        build_heartbeat_payload,
        send_heartbeat,
    )

    supervisor.begin_drain()
    worker_id = getattr(app.state, "worker_node", "") or settings.worker_id
    try:
        await send_heartbeat(
            client,
            settings.central_url,
            worker_id,
            build_heartbeat_payload(
                worker_id=worker_id,
                protocol_version=PROTOCOL_VERSION,
                image_version=settings.image_version,
                supervisor=supervisor,
                state="DRENANDO",
                accepting_jobs=False,
                sequence=0,
                instance_id=getattr(app.state, "instance_nonce", ""),
            ),
        )
    except Exception:
        log.debug("latido final DRENANDO descartado (transporte)")
    stop.set()
    try:
        await asyncio.wait_for(asyncio.shield(heartbeat_task), timeout=5.0)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        heartbeat_task.cancel()
    vacio = await supervisor.wait_empty(
        timeout_seconds=float(settings.worker_drain_timeout_seconds)
    )
    if not vacio:
        for job in supervisor.cancel_all():
            token = _cancel_tokens.get(job.key)
            if token is not None:
                token.cancel()
        log.warning("drenaje vencido: cancelación cooperativa pedida")
        await supervisor.wait_empty(timeout_seconds=10.0)


async def _report_started(
    app: FastAPI, env: JobEnvelope, job: LocalJob
) -> None:
    """Evento `started` idempotente (misma clave que el resultado)."""
    settings: WorkerConfig = app.state.settings
    client = getattr(app.state, "http", None)
    if client is None:
        return
    nodo = _central_node(app)
    headers = _central_headers(app, nodo)
    extra: dict[str, Any] = {"headers": headers} if headers else {}
    try:
        await client.post(
            f"{settings.central_url.rstrip('/')}/internal/v1/jobs/"
            f"{job.job_id}/events",
            json={
                "event_id": f"evt-{job.job_id}-{job.attempt}-started",
                "event_type": "started",
                "assignment_attempt": job.attempt,
                "assignment_token": env.assignment_token,
            },
            timeout=3.0,
            **extra,
        )
    except Exception:
        log.debug("evento started descartado (transporte)")


def _central_node(app: FastAPI) -> str:
    """Nodo registrado ante la central (``ip:port``) o cadena vacía.

    La cabecera ``X-Worker-Node`` solo se envía con un nodo real: el valor
    por defecto sin registrar nunca debe viajar a la central.
    """
    node = getattr(app.state, "worker_node", "") or ""
    if node and node != "sin-asignar":
        return node
    return ""


class ArtifactSlotRef:
    """Adaptador minimo al slot que espera ArtifactStore."""

    def __init__(
        self,
        artifact_id: str,
        put_url: str,
        object_key: str,
        max_bytes: int,
        content_types: tuple[str, ...] = (),
    ) -> None:
        self.artifact_id = artifact_id
        self.put_url = put_url
        self.object_key = object_key
        self.max_bytes = max_bytes
        self.content_types = content_types


def run(argv: list[str] | None = None) -> None:
    """Entrypoint: ``python -m bot_worker --central-url ... --advertised-url ...``.

    Toda la configuración entra por argv (ver ``bot_worker.config``); el
    proceso no lee variables de entorno para configurarse.
    """
    import uvicorn

    settings = parse_args(argv)
    logging.basicConfig(level=settings.log_level)
    extra: dict[str, Any] = {}
    if settings.tls_enabled:
        from bot_worker.security.tls import TlsPaths, uvicorn_tls_kwargs

        extra = uvicorn_tls_kwargs(
            TlsPaths(
                ca_file=settings.tls_ca_file,
                cert_file=settings.tls_cert_file,
                key_file=settings.tls_key_file,
                client_cert_file=settings.tls_client_cert_file,
                client_key_file=settings.tls_client_key_file,
            )
        )
    uvicorn.run(
        create_app(settings),
        host="0.0.0.0",
        port=settings.port,
        **extra,
    )


# Importar el módulo nunca configura nada: sin ``run()`` no hay app.
app = None


if __name__ == "__main__":
    run()
