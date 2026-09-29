"""Despacho e inspección de workers por HTTP.

La central NUNCA ejecuta bots ni abre la DB del worker: inspecciona
(``GET /internal/v1/status``), asigna (``POST /internal/v1/jobs``) y consulta
(``GET /internal/v1/jobs``) contra la IP guardada del worker.
"""

from __future__ import annotations

import logging

import httpx

from central_api.settings import get_settings
from central_api.services.apoc_base import read_cached_text
from central_api.store import Job, WorkerEntry

log = logging.getLogger("central_api.scheduler.dispatcher")

ASSIGN_PATH = "/internal/v1/jobs"
STATUS_PATH = "/internal/v1/status"
WORKER_TIMEOUT_SECONDS = 10.0


def worker_base_url(node: str) -> str:
    address = str(node).strip()
    if address.startswith(("http://", "https://")):
        return address.rstrip("/")
    scheme = "https" if address.rsplit(":", 1)[-1] == "443" else "http"
    return f"{scheme}://{address}"


def _limpio(valor: str) -> str | None:
    """Normaliza secretos: vacíos y placeholders de desarrollo dan None."""
    texto = (valor or "").strip()
    if not texto or texto in ("sin-configurar", "placeholder", "dev-placeholder"):
        return None
    return texto


def provisioned_section(bot: str | None = None) -> dict:
    """Datos que el worker necesita y ya no tiene en entorno: proxy,
    claves de captcha y endpoints de servicio. Salen de los secretos de la
    central y viajan solo dentro del sobre sellado.

    ``bot`` limita los datos voluminosos: la tabla de apócrifos de AFIP pesa
    más de un megabyte y solo la necesita el plugin ``apoc``, así que no debe
    viajar en el sobre de todos los jobs.
    """
    settings = get_settings()
    section: dict = {
        "proxy": None,
        "captcha": {
            "enabled": True,
            "arca_key": _limpio(settings.capmonster_arca_key),
            "srt_key": _limpio(settings.capmonster_srt_key),
        },
        "service": {
            "cuit_base_url": _limpio(settings.cuit_service_base_url),
            "cuit_masiva_url": _limpio(settings.cuit_service_masiva_url),
            "cuit_usuario": _limpio(settings.cuit_service_usuario),
            "cuit_api_key": _limpio(settings.cuit_service_api_key),
        },
    }
    if bot == "apoc":
        apoc_base = read_cached_text()
        if apoc_base is not None:
            # El plugin la lee de la sección ``service`` (configure()).
            section["service"]["apoc_base_text"] = apoc_base
    if settings.proxy_enabled and settings.proxy_host:
        section["proxy"] = {
            "mode": settings.proxy_mode,
            "host": settings.proxy_host,
            "username": settings.proxy_username or None,
            "password": settings.proxy_password or None,
            "country": settings.proxy_country,
        }
    return section


async def probe_worker(node: str) -> dict:
    """Inspección de salud viva: detalle a demanda desde el worker."""
    async with httpx.AsyncClient(timeout=WORKER_TIMEOUT_SECONDS) as client:
        resp = await client.get(worker_base_url(node) + STATUS_PATH)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}


# Verbo de ejecución por bot: el catálogo público usa el nombre de la
# operación pública ("consulta") y cada plugin del worker declara sus
# propios verbos ("consultar", ...). El dispatcher traduce al verbo que el
# worker admite en su manifiesto; sin traducción vale el nombre público.
OPERACION_WORKER: dict[tuple[str, str], str] = {
    ("consulta_cuit", "consulta"): "consultar",
    ("mis_comprobantes", "consulta"): "consultar",
}


def worker_operation(bot: str, operation: str) -> str:
    """Traduce la operación pública al verbo del plugin del worker."""
    return OPERACION_WORKER.get((bot, operation), operation)


def artifact_upload_slots(job: Job) -> list[dict]:
    """Declara los artefactos opcionales que el job puede subir.

    Los slots se entregan sin URL. El worker los presigna bajo demanda, una
    vez que la asignación está viva y la central puede autorizarla.
    """
    payload = job.payload if isinstance(job.payload, dict) else {}
    slots: list[dict] = []
    if job.bot == "ccma" and payload.get("incluir_pdf") and payload.get("subir", True):
        slots.append(
            {
                "artifact_id": "ccma_resumen.pdf",
                "name_hint": "ccma_resumen.pdf",
                "max_bytes": 10_485_760,
                "content_types": ["application/pdf"],
            }
        )
    if job.bot == "mis_comprobantes" and payload.get("subir_csv", True):
        for tipo, artifact_id in (
            ("emitidos", "emitidos_csv"),
            ("recibidos", "recibidos_csv"),
        ):
            if payload.get(tipo):
                slots.append(
                    {
                        "artifact_id": artifact_id,
                        "name_hint": f"{tipo}.csv",
                        "max_bytes": 52_428_800,
                        "content_types": ["text/csv"],
                    }
                )
    return slots


def build_envelope(
    job: Job,
    worker: WorkerEntry,
    assignment_token: str,
    lease_id: str = "",
    lease_expires_at: str | None = None,
    force: bool = False,
) -> dict:
    """Arma el sobre de asignación, sellando lo sensible cuando se puede.

    La sección sensible (credenciales fiscales efímeras) viaja cifrada con la
    RSA pública que el worker publicó en su registro. Si un job tiene
    credenciales y el worker no publicó una clave, se rechaza el despacho en
    lugar de degradar a texto claro.

    Además del sobre histórico (``sealed``/``sealed_section``/``bot``), el
    worker exige admisión ``plugin`` + ``attempt`` + ``lease_id`` +
    ``lease_expires_at`` + verbo de operación de su manifiesto: viajan como
    campos adicionales del mismo sobre.

    Con ``force`` el sobre se firma bajo el alcance ``assign-force``: el
    worker solo lo admite por esa vía para superar el cupo.
    """
    from central_api.security.assignments import (
        ASSIGNMENT_SCOPE_ASSIGN,
        ASSIGNMENT_SCOPE_ASSIGN_FORCE,
        default_expiry,
        process_signing_key,
        sealed_hash_of,
        sign_assignment,
    )
    from central_api.security.sealed import encrypt_sealed_section

    settings = get_settings()
    sensitive = {"credentials": job.credentials}
    sensitive.update(provisioned_section(job.bot))
    expires_at = default_expiry(settings.worker_ack_lease_seconds)
    envelope: dict = {
        "protocol_version": 1,  # mrbot_contracts.version.PROTOCOL_VERSION
        "job_id": job.id,
        "assignment_token": assignment_token,
        "bot": job.bot,
        "plugin": job.bot,
        "operation": worker_operation(job.bot, job.operation),
        "attempt": job.assignment_attempt,
        "lease_id": lease_id,
        "lease_expires_at": lease_expires_at,
        "payload": job.payload,
        "sealed": False,
        "sealed_section": None,
        "credentials": None,
        "artifact_uploads": artifact_upload_slots(job),
        "force": bool(force),
        "assignment_expires_at": expires_at,
        "assignment_signature": "",
    }
    if worker.sealed_pubkey_pem:
        envelope["sealed_section"] = encrypt_sealed_section(
            worker.sealed_pubkey_pem, sensitive
        )
        envelope["sealed"] = True
    else:
        if job.credentials:
            raise RuntimeError(
                "worker sin clave pública para recibir credenciales selladas"
            )
    private, _efimera = process_signing_key(settings.assignment_signing_key)
    envelope["assignment_signature"] = sign_assignment(
        private,
        scope=ASSIGNMENT_SCOPE_ASSIGN_FORCE if force else ASSIGNMENT_SCOPE_ASSIGN,
        job_id=job.id,
        attempt=job.assignment_attempt,
        lease_id=lease_id,
        expires_at=expires_at,
        sealed_hash=sealed_hash_of(envelope["sealed_section"]),
    )
    return envelope


async def dispatch_to_worker(
    job: Job,
    worker: WorkerEntry,
    assignment_token: str,
    lease_id: str = "",
    lease_expires_at: str | None = None,
    force: bool = False,
) -> bool:
    """Asigna el job al worker por HTTP. ``True`` si el worker aceptó (2xx)."""
    envelope = build_envelope(job, worker, assignment_token, lease_id, lease_expires_at, force=force)
    # Sin Bearer [REDACTED]: la ejecución se autoriza con la firma Ed25519
    # del sobre (el worker solo corre lo firmado por esta central).
    async with httpx.AsyncClient(timeout=WORKER_TIMEOUT_SECONDS) as client:
        resp = await client.post(
            worker_base_url(worker.node) + ASSIGN_PATH,
            json=envelope,
        )
    if not 200 <= resp.status_code < 300:
        # El cuerpo del worker contiene solo códigos de validación y nunca se
        # debe registrar el envelope, que puede contener credenciales selladas.
        log.warning(
            "worker rechazó asignación job=%s worker=%s status=%s detail=%s",
            job.id,
            worker.node,
            resp.status_code,
            resp.text[:512],
        )
    return 200 <= resp.status_code < 300


async def list_worker_jobs(node: str) -> list:
    """Consulta los jobs en curso en un worker dado por su IP."""
    async with httpx.AsyncClient(timeout=WORKER_TIMEOUT_SECONDS) as client:
        resp = await client.get(worker_base_url(node) + ASSIGN_PATH)
        resp.raise_for_status()
        data = resp.json()
    return data if isinstance(data, list) else data.get("jobs", [])
