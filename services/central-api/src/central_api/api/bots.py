"""Creación de jobs: ``POST /api/v3/bots/{bot}/{operacion}`` -> 202 + job_id.

S-1: persiste el pedido (con reserva de billing) y responde de inmediato;
el scheduler lo asigna después a un worker sano por HTTP. Ningún import de
bots aquí: la operación se resuelve contra el manifiesto registrado.
``Idempotency-Key`` garantiza S-2 (replay exacto o 409 ante otro contenido).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from central_api.api.bot_payloads import (
    public_bot_body_schema,
    public_bot_payload_schema,
)
from central_api.api.dependencies import (
    check_idempotency,
    correlation_id,
    ensure_meta,
    fingerprint_payload,
    idempotency_conflict_response,
    remember_idempotency,
    require_api_principal,
)
from central_api.billing.entitlements import BOT_CREDIT_COST, PLANS, get_plan
from central_api.billing.reservation import QuotaExhausted, reserve_for_job
from central_api.db import db_configurado, nueva_sesion
from central_api.security.principals import ApiPrincipal
from central_api.security.credentials import (
    CredentialInputError,
    normalize_payload_and_credentials,
    normalize_v2_payload,
)
from central_api.security.secret_redaction import public_error
from central_api.store import JOBS, Job, new_job_id

router = APIRouter()


async def _persistir_job_db(
    user_id: str, bot: str, operacion: str, payload: dict,
    idempotency_key: str | None, credential_ciphertext: str | None = None,
) -> str | None:
    """Persiste el job en PostgreSQL; ``None`` si hay que usar memoria.

    Usa ``JobRepository`` + ``models`` (idempotencia por restricción única).
    Solo aplica cuando el principal es UUID (frontera de identidad PG).
    """
    try:
        import uuid

        from central_api.repositories.jobs import JobRepository
    except Exception:  # noqa: BLE001 - sin repos, fallback de desarrollo
        return None
    try:
        uid = uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError):
        return None
    try:
        async with nueva_sesion() as sesion:
            repo = JobRepository(sesion)  # type: ignore[arg-type]
            fila = await repo.create(
                user_id=uid, bot=bot, operation=operacion,
                request_payload=dict(payload),
                credential_ciphertext=credential_ciphertext,
                idempotency_key=idempotency_key,
            )
            return str(fila.id)
    except Exception:  # noqa: BLE001 - la creación vive igual en memoria
        return None


class CreateJobBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    payload: dict = Field(default_factory=dict)
    credentials: dict = Field(
        default_factory=dict,
        description="Compatibilidad V3: credenciales de borde, nunca en request_payload",
    )


class CreateJobResponse(BaseModel):
    success: bool = True
    job_id: str
    status: str = "PENDIENTE"


# Manifiesto único (plan 02 §3.11): única fuente de catálogo, validación,
# elegibilidad de worker y costo. Sin routers ni modelos ORM por bot.
#
# La operacion publica coincide con el verbo del manifiesto del worker
# (``REGISTRY``), salvo los alias historicos ``consulta`` de
# ``consulta_cuit`` y ``mis_comprobantes`` (el dispatcher los traduce a
# ``consultar``). Costos iguales a ``costo_creditos_sugerido`` del
# manifiesto, salvo los alias historicos que conservan su costo previo.
def _op(bot: str, operation: str, costo_creditos: int) -> dict:
    return {
        "bot": bot, "operation": operation,
        "costo_creditos": costo_creditos,
        "capabilities": (f"{bot}/{operation}",),
        "modo": "job", "requiere_archivos": False,
    }


OPERATIONS: dict[tuple[str, str], dict] = {
    ("consulta_cuit", "consulta"): _op("consulta_cuit", "consulta", 1),
    ("mis_comprobantes", "consulta"): _op("mis_comprobantes", "consulta", 1),
    ("apoc", "consultar"): _op("apoc", "consultar", 1),
    ("aportes_en_linea", "descargar"): _op("aportes_en_linea", "descargar", 2),
    ("arba", "descargar"): _op("arba", "descargar", 2),
    ("carga_portal_iva", "cargar"): _op("carga_portal_iva", "cargar", 3),
    ("ccma", "consultar"): _op("ccma", "consultar", 2),
    ("certificado_mipyme", "descargar"): _op("certificado_mipyme", "descargar", 1),
    ("compensaciones", "consultar"): _op("compensaciones", "consultar", 2),
    ("comprobantes", "consultar"): _op("comprobantes", "consultar", 2),
    ("comprobantes", "solicitar"): _op("comprobantes", "solicitar", 2),
    ("comprobantes", "historial"): _op("comprobantes", "historial", 2),
    ("consulta_cuit", "consultar"): _op("consulta_cuit", "consultar", 1),
    ("consulta_cuit", "consultar_masivo"): _op("consulta_cuit", "consultar_masivo", 1),
    ("consulta_pagos_vep", "consultar"): _op("consulta_pagos_vep", "consultar", 1),
    ("controladores_fiscales", "presentar"): _op("controladores_fiscales", "presentar", 2),
    ("declaracion_en_linea", "consultar"): _op("declaracion_en_linea", "consultar", 2),
    ("facturometro", "consultar"): _op("facturometro", "consultar", 1),
    ("hacienda", "consultar"): _op("hacienda", "consultar", 2),
    ("libros_portal_iva", "descargar_libros"): _op("libros_portal_iva", "descargar_libros", 2),
    ("libros_portal_iva", "descargar_ddjj"): _op("libros_portal_iva", "descargar_ddjj", 2),
    ("liquidacion_granos", "consultar"): _op("liquidacion_granos", "consultar", 2),
    ("mis_comprobantes", "consultar"): _op("mis_comprobantes", "consultar", 2),
    ("mis_comprobantes", "solicitar"): _op("mis_comprobantes", "solicitar", 2),
    ("mis_comprobantes", "historial"): _op("mis_comprobantes", "historial", 2),
    ("mis_facilidades", "consultar"): _op("mis_facilidades", "consultar", 2),
    ("mis_retenciones", "consultar"): _op("mis_retenciones", "consultar", 2),
    ("mis_retenciones_iva_simple", "consultar"): _op(
        "mis_retenciones_iva_simple", "consultar", 2),
    ("moa", "consultar"): _op("moa", "consultar", 2),
    ("pago_devoluciones", "consultar"): _op("pago_devoluciones", "consultar", 2),
    ("portal_iva", "descargar"): _op("portal_iva", "descargar", 3),
    ("portal_iva", "importar"): _op("portal_iva", "importar", 3),
    ("portal_iva", "gestionar"): _op("portal_iva", "gestionar", 3),
    ("rcel", "descargar"): _op("rcel", "descargar", 2),
    ("retper_iibb_agip", "consultar"): _op("retper_iibb_agip", "consultar", 2),
    ("retper_iibb_misiones", "consultar"): _op("retper_iibb_misiones", "consultar", 2),
    ("sct", "consultar"): _op("sct", "consultar", 2),
    ("sifere", "consultar"): _op("sifere", "consultar", 2),
    ("siper", "consultar"): _op("siper", "consultar", 1),
    ("srt", "consultar_alicuotas"): _op("srt", "consultar_alicuotas", 2),
    ("vep_archivo", "generar"): _op("vep_archivo", "generar", 3),
    ("vep_ccma", "generar"): _op("vep_ccma", "generar", 3),
}

CATALOGUE = [
    {"bot": "apoc", "operaciones": ["consultar"]},
    {"bot": "aportes_en_linea", "operaciones": ["descargar"]},
    {"bot": "arba", "operaciones": ["descargar"]},
    {"bot": "carga_portal_iva", "operaciones": ["cargar"]},
    {"bot": "ccma", "operaciones": ["consultar"]},
    {"bot": "certificado_mipyme", "operaciones": ["descargar"]},
    {"bot": "compensaciones", "operaciones": ["consultar"]},
    {"bot": "comprobantes", "operaciones": ["consultar", "solicitar", "historial"]},
    {"bot": "consulta_cuit",
     "operaciones": ["consulta", "consultar", "consultar_masivo"]},
    {"bot": "consulta_pagos_vep", "operaciones": ["consultar"]},
    {"bot": "controladores_fiscales", "operaciones": ["presentar"]},
    {"bot": "declaracion_en_linea", "operaciones": ["consultar"]},
    {"bot": "facturometro", "operaciones": ["consultar"]},
    {"bot": "hacienda", "operaciones": ["consultar"]},
    {"bot": "libros_portal_iva",
     "operaciones": ["descargar_libros", "descargar_ddjj"]},
    {"bot": "liquidacion_granos", "operaciones": ["consultar"]},
    {"bot": "mis_comprobantes",
     "operaciones": ["consulta", "consultar", "solicitar", "historial"]},
    {"bot": "mis_facilidades", "operaciones": ["consultar"]},
    {"bot": "mis_retenciones", "operaciones": ["consultar"]},
    {"bot": "mis_retenciones_iva_simple", "operaciones": ["consultar"]},
    {"bot": "moa", "operaciones": ["consultar"]},
    {"bot": "pago_devoluciones", "operaciones": ["consultar"]},
    {"bot": "portal_iva", "operaciones": ["descargar", "importar", "gestionar"]},
    {"bot": "rcel", "operaciones": ["descargar"]},
    {"bot": "retper_iibb_agip", "operaciones": ["consultar"]},
    {"bot": "retper_iibb_misiones", "operaciones": ["consultar"]},
    {"bot": "sct", "operaciones": ["consultar"]},
    {"bot": "sifere", "operaciones": ["consultar"]},
    {"bot": "siper", "operaciones": ["consultar"]},
    {"bot": "srt", "operaciones": ["consultar_alicuotas"]},
    {"bot": "vep_archivo", "operaciones": ["generar"]},
    {"bot": "vep_ccma", "operaciones": ["generar"]},
]


def _operation_or_none(bot: str, operacion: str) -> dict | None:
    return OPERATIONS.get((bot, operacion))


def _canonical_body_openapi() -> dict:
    """Describe el envelope V3 y permite explorar cada payload de bot.

    La ruta canónica recibe ``payload`` anidado. Los aliases V2 documentan el
    payload directamente, mientras que este esquema conserva el envelope y
    ofrece las 42 combinaciones bot/operación dentro de ``oneOf``.
    """
    payload_example = dict(
        public_bot_body_schema("ccma", "consultar")["examples"][0]
    )
    credentials_example = payload_example.pop("credentials", {})
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["payload"],
                        "properties": {
                            "payload": {
                                "oneOf": [
                                    public_bot_payload_schema(bot, operation)
                                    for bot, operation in OPERATIONS
                                ],
                                "description": (
                                    "Payload específico del bot. Seleccione el "
                                    "esquema correspondiente a bot/operacion."
                                ),
                            },
                            "credentials": {
                                "type": "object",
                                "additionalProperties": True,
                                "description": (
                                    "Credenciales fiscales efímeras. Nunca se "
                                    "persisten."
                                ),
                            },
                        },
                        "additionalProperties": False,
                        "example": {
                            "payload": payload_example,
                            "credentials": credentials_example,
                        },
                    }
                }
            },
        }
    }


async def submit_job(
    *,
    bot: str,
    operacion: str,
    payload: dict,
    credentials: dict,
    request: Request,
    principal: ApiPrincipal,
    idempotency_key: str | None,
) -> JSONResponse:
    """Admite un job desde cualquier adaptador público de bots.

    El adaptador genérico V3 y los aliases compatibles con las rutas V2 pasan
    por esta misma función. Así se conserva una sola frontera de autenticación,
    idempotencia, cuota, persistencia y encolado, sin que un router invoque un
    plugin ni al worker directamente.
    """
    corr = correlation_id(request)
    definition = _operation_or_none(bot, operacion)
    if definition is None:
        return JSONResponse(
            status_code=404, content=public_error("not_found", corr),
            headers={"X-Correlation-ID": corr},
        )
    normalized_payload = normalize_v2_payload(bot, operacion, payload)
    try:
        (
            normalized_payload,
            worker_credentials,
            credential_ciphertext,
            credential_fingerprint,
        ) = normalize_payload_and_credentials(
            normalized_payload,
            credentials,
            require_encryption=db_configurado(),
        )
    except CredentialInputError as exc:
        return JSONResponse(
            status_code=422,
            content={
                "detail": {
                    "error_code": "invalid_credentials",
                    "message": str(exc),
                }
            },
            headers={"X-Correlation-ID": corr},
        )
    fingerprint_payload_data = dict(normalized_payload)
    if credential_fingerprint:
        fingerprint_payload_data["_credential_fingerprint"] = credential_fingerprint
    fingerprint = fingerprint_payload(bot, operacion, fingerprint_payload_data)
    if idempotency_key:
        hit = check_idempotency(principal.user_id, idempotency_key, fingerprint)
        if hit.conflict:
            return JSONResponse(
                status_code=409,
                content=public_error("idempotency_conflict", corr),
                headers={"X-Correlation-ID": corr},
            )
        if hit.job_id and hit.job_id in JOBS:
            job = JOBS[hit.job_id]
            return JSONResponse(
                status_code=202,
                content={"success": True, "job_id": job.id, "status": job.status},
                headers={
                    "X-Correlation-ID": corr,
                    "Location": f"/api/v3/jobs/{job.id}",
                },
            )
    # Con base configurada la fila canónica vive en PostgreSQL
    # (JobRepository + models); el dict en memoria queda como caché.
    job_id_pg: str | None = None
    if db_configurado():
        job_id_pg = await _persistir_job_db(
            principal.user_id,
            bot,
            operacion,
            normalized_payload,
            idempotency_key,
            credential_ciphertext,
        )
        if credential_ciphertext and job_id_pg is None:
            return JSONResponse(
                status_code=503,
                content=public_error("service_not_enabled", corr),
                headers={"X-Correlation-ID": corr},
            )
    job = Job(
        id=job_id_pg or new_job_id(),
        bot=bot,
        operation=operacion,
        payload=normalized_payload,
        credentials=worker_credentials,
    )
    try:
        reserve_for_job(principal.user_id, job.id, bot, operacion)
    except QuotaExhausted:
        return JSONResponse(
            status_code=429, content=public_error("quota_exhausted", corr),
            headers={"X-Correlation-ID": corr, "Retry-After": "60"},
        )
    JOBS[job.id] = job
    ensure_meta(job.id, principal.user_id)
    if idempotency_key:
        remember_idempotency(principal.user_id, idempotency_key, fingerprint, job.id)
    return JSONResponse(
        status_code=202,
        content={"success": True, "job_id": job.id, "status": job.status},
        headers={"X-Correlation-ID": corr, "Location": f"/api/v3/jobs/{job.id}"},
    )


@router.post(
    "/bots/{bot}/{operacion}",
    status_code=202,
    openapi_extra=_canonical_body_openapi(),
)
async def create_job(
    bot: str,
    operacion: str,
    body: CreateJobBody,
    request: Request,
    principal: ApiPrincipal = Depends(require_api_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    """Crea un job usando la superficie genérica normativa de V3."""
    payload = dict(body.payload)
    credentials = dict(body.credentials)
    for name, value in (body.model_extra or {}).items():
        if name in {"clave", "clave_representante", "contrasena", "clave_encriptada"}:
            credentials[name] = value
        elif name not in payload:
            payload[name] = value
    return await submit_job(
        bot=bot,
        operacion=operacion,
        payload=payload,
        credentials=credentials,
        request=request,
        principal=principal,
        idempotency_key=idempotency_key,
    )


@router.get("/bots")
def list_bots(principal: ApiPrincipal = Depends(require_api_principal)) -> dict:
    """Catálogo visible con costo y disponibilidad (plan 02 §3.10)."""
    _ = principal
    items = [
        {
            "id": bot,
            "nombre": bot.replace("_", " ").capitalize(),
            "estado": "HABILITADO",
            "costo_creditos": min(
                (BOT_CREDIT_COST.get((bot, o), 1) for o in ops), default=1
            ),
            "operaciones": [
                {"id": op, "modo": "job",
                 "costo_creditos": BOT_CREDIT_COST.get((bot, op), 1)}
                for op in ops
            ],
        }
        for bot, ops in ((e["bot"], e["operaciones"]) for e in CATALOGUE)
    ]
    return {"success": True, "bots": CATALOGUE, "items": items}


@router.get("/bots/{bot}")
def bot_detail(
    bot: str, principal: ApiPrincipal = Depends(require_api_principal)
) -> JSONResponse:
    """Detalle y operaciones de un bot con schema de entrada y costo."""
    _ = principal
    ops = [
        {"id": op, "modo": "job",
         "costo_creditos": BOT_CREDIT_COST.get((bot, op), 1),
         "input_schema": {"type": "object"}}
        for (b, op) in OPERATIONS
        if b == bot
    ]
    if not ops:
        return JSONResponse(status_code=404, content=public_error("not_found"))
    plan = get_plan("free")
    _ = plan
    return JSONResponse(
        status_code=200,
        content={"id": bot, "nombre": bot, "estado": "HABILITADO",
                 "operaciones": ops, "planes": sorted(PLANS)},
    )
