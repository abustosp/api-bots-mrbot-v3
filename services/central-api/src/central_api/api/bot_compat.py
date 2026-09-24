"""Aliases de compatibilidad para las rutas de bots de V2.

La superficie normativa V3 es ``/bots/{bot}/{operacion}``, pero algunos
clientes todavía consumen las rutas por bot de V2. Estos aliases no ejecutan
plugins ni llaman workers directamente: traducen el path al manifiesto V3 y
reutilizan exactamente el mismo submitter, scheduler, idempotencia y ownership.
Los uploads grandes siguen el flujo V3 de ``/uploads`` con object keys
prefirmadas. Un multipart proxificado no se copia a memoria de la central.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from central_api.api.bot_payloads import public_bot_compat_body_schema
from central_api.api.bots import CreateJobResponse, OPERATIONS, submit_job
from central_api.api.dependencies import require_api_principal
from central_api.api.jobs import CancelBody, cancel_job, get_job
from central_api.security.principals import ApiPrincipal

router = APIRouter()

_V2_SECRET_FIELDS = {
    "clave",
    "clave_representante",
    "contrasena",
    "clave_encriptada",
}


# V2 generated routes mapped to the canonical V3 bot and operation. The list
# intentionally lives in one adapter instead of recreating one router per bot.
BOT_ROUTE_ALIASES: tuple[tuple[str, str, str], ...] = (
    ("/mis_comprobantes/consulta", "mis_comprobantes", "consultar"),
    ("/mis_comprobantes/solicitar_consulta", "mis_comprobantes", "solicitar"),
    ("/mis_comprobantes/historial", "mis_comprobantes", "historial"),
    ("/ccma/consulta", "ccma", "consultar"),
    ("/siper/consulta", "siper", "consultar"),
    ("/sct/consulta", "sct", "consultar"),
    ("/sct/compensaciones/consulta", "compensaciones", "consultar"),
    ("/portal_iva/consulta", "portal_iva", "descargar"),
    ("/portal_iva/carga", "carga_portal_iva", "cargar"),
    ("/rcel/consulta", "rcel", "descargar"),
    ("/hacienda/consulta", "hacienda", "consultar"),
    ("/sifere/consulta", "sifere", "consultar"),
    ("/aportes-en-linea/consulta", "aportes_en_linea", "descargar"),
    ("/declaracion-en-linea/consulta", "declaracion_en_linea", "consultar"),
    ("/mis_facilidades/consulta", "mis_facilidades", "consultar"),
    ("/mis_retenciones/consulta", "mis_retenciones", "consultar"),
    (
        "/mis_retenciones_iva_simple/consulta",
        "mis_retenciones_iva_simple",
        "consultar",
    ),
    (
        "/retenciones_percepciones_iibb/misiones/consulta",
        "retper_iibb_misiones",
        "consultar",
    ),
    (
        "/retenciones_percepciones_iibb/agip/consulta",
        "retper_iibb_agip",
        "consultar",
    ),
    (
        "/retenciones_percepciones_iibb/arba/consulta",
        "arba",
        "descargar",
    ),
    ("/arba/consulta", "arba", "descargar"),
    ("/pago_devoluciones/consulta", "pago_devoluciones", "consultar"),
    ("/moa/consulta", "moa", "consultar"),
    ("/libros_iva/consulta", "libros_portal_iva", "descargar_libros"),
    ("/libros_iva/ddjj", "libros_portal_iva", "descargar_ddjj"),
    ("/facturometro/consulta", "facturometro", "consultar"),
    ("/controladores-fiscales/carga", "controladores_fiscales", "presentar"),
    ("/certificado-mipyme/consulta", "certificado_mipyme", "descargar"),
    ("/srt/alicuotas/consulta", "srt", "consultar_alicuotas"),
    ("/vep/carga", "vep_archivo", "generar"),
    ("/vep_archivo/carga", "vep_archivo", "generar"),
    ("/vep-ccma/generar", "vep_ccma", "generar"),
    ("/vep/consulta-pagos", "consulta_pagos_vep", "consultar"),
    ("/liquidacion_granos/consulta", "liquidacion_granos", "consultar"),
    ("/consulta_cuit/individual", "consulta_cuit", "consultar"),
    ("/consulta_cuit/masivo", "consulta_cuit", "consultar_masivo"),
)


def _bad_payload(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "error_code": "validation",
                "message": message,
            }
        },
    )


async def _decode_payload(request: Request) -> tuple[dict, dict] | JSONResponse:
    """Lee JSON V3 o campos simples de formulario sin proxificar archivos.

    Un archivo recibido por multipart no se copia ni se conserva en la
    central. El cliente debe pedir un ticket en ``POST /uploads`` y enviar el
    ``object_key`` en el JSON del job.
    """
    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type or not content_type:
        try:
            raw: Any = await request.json()
        except (ValueError, json.JSONDecodeError):
            return _bad_payload("El cuerpo debe ser un objeto JSON válido.")
        if not isinstance(raw, dict):
            return _bad_payload("El cuerpo debe ser un objeto JSON.")
        payload = dict(raw.get("payload")) if isinstance(raw.get("payload"), dict) else dict(raw)
        credentials_value = raw.get("credentials")
        credentials = dict(credentials_value) if isinstance(credentials_value, dict) else {}
        payload.pop("credentials", None)
        for field in _V2_SECRET_FIELDS:
            if field in payload and field not in credentials:
                credentials[field] = payload[field]
            payload.pop(field, None)
        return payload, credentials

    if "multipart/form-data" in content_type:
        try:
            form = await request.form()
        except (AssertionError, RuntimeError):
            return _bad_payload(
                "La carga multipart proxificada no está habilitada. "
                "Use POST /api/v3/uploads y envíe el object_key."
            )
        payload: dict[str, Any] = {}
        credentials: dict[str, Any] = {}
        for key, value in form.multi_items():
            if getattr(value, "filename", None):
                return _bad_payload(
                    "Los archivos deben cargarse mediante POST /api/v3/uploads "
                    "y referenciarse por object_key."
                )
            if key == "credentials":
                try:
                    parsed = json.loads(str(value))
                except (TypeError, ValueError, json.JSONDecodeError):
                    return _bad_payload("credentials debe ser JSON válido.")
                if not isinstance(parsed, dict):
                    return _bad_payload("credentials debe ser un objeto JSON.")
                credentials = parsed
            elif key in _V2_SECRET_FIELDS:
                credentials[key] = value
            else:
                payload[key] = value
        return payload, credentials

    return _bad_payload("Content-Type no soportado. Use application/json.")


def _create_handler(bot: str, operacion: str) -> Callable[..., Awaitable[JSONResponse]]:
    async def create_compat_job(
        request: Request,
        principal: ApiPrincipal = Depends(require_api_principal),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        decoded = await _decode_payload(request)
        if isinstance(decoded, JSONResponse):
            return decoded
        payload, credentials = decoded
        return await submit_job(
            bot=bot,
            operacion=operacion,
            payload=payload,
            credentials=credentials,
            request=request,
            principal=principal,
            idempotency_key=idempotency_key,
        )

    create_compat_job.__name__ = f"create_{bot}_{operacion}_compat"
    return create_compat_job


def _status_handler() -> Callable[..., Awaitable[JSONResponse]]:
    async def status_compat_job(
        job_id: str,
        principal: ApiPrincipal = Depends(require_api_principal),
    ) -> JSONResponse:
        return await get_job(job_id, principal)

    status_compat_job.__name__ = "status_compat_job"
    return status_compat_job


def _cancel_handler() -> Callable[..., Awaitable[JSONResponse]]:
    async def cancel_compat_job(
        job_id: str,
        request: Request,
        principal: ApiPrincipal = Depends(require_api_principal),
    ) -> JSONResponse:
        motivo = "cancelado desde alias V2"
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                raw = await request.json()
            except (ValueError, json.JSONDecodeError):
                raw = {}
            if isinstance(raw, dict):
                motivo = str(raw.get("motivo") or motivo)[:500]
        return cancel_job(job_id, CancelBody(motivo=motivo), principal)

    cancel_compat_job.__name__ = "cancel_compat_job"
    return cancel_compat_job


def _register_routes() -> None:
    status_handler = _status_handler()
    cancel_handler = _cancel_handler()
    for route_path, bot, operacion in BOT_ROUTE_ALIASES:
        if (bot, operacion) not in OPERATIONS:
            raise RuntimeError(
                f"alias de bot sin operación canónica: {route_path} -> {bot}/{operacion}"
            )
        router.add_api_route(
            route_path,
            _create_handler(bot, operacion),
            methods=["POST"],
            status_code=202,
            response_model=CreateJobResponse,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": public_bot_compat_body_schema(
                                bot,
                                operacion,
                                route_path=route_path,
                            ),
                        }
                    },
                }
            },
            name=f"compat_{bot}_{operacion}_create",
            tags=["bots-compatibilidad"],
        )
        router.add_api_route(
            f"{route_path}/{{job_id}}",
            status_handler,
            methods=["GET"],
            response_model=dict[str, Any],
            name=f"compat_{bot}_{operacion}_status",
            tags=["bots-compatibilidad"],
        )
        router.add_api_route(
            f"{route_path}/cancelar/{{job_id}}",
            cancel_handler,
            methods=["POST"],
            response_model=dict[str, Any],
            openapi_extra={
                "requestBody": {
                    "content": {
                        "application/json": {
                            "schema": CancelBody.model_json_schema(),
                        }
                    }
                }
            },
            name=f"compat_{bot}_{operacion}_cancel",
            tags=["bots-compatibilidad"],
        )


_register_routes()


@router.get("/apoc/consulta/{cuit}", status_code=202, tags=["bots-compatibilidad"])
async def apoc_compat_get(
    cuit: str,
    request: Request,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """Adapta el GET histórico de APOC a un job V3 idempotente."""
    return await submit_job(
        bot="apoc",
        operacion="consultar",
        payload={"cuit": cuit},
        credentials={},
        request=request,
        principal=principal,
        idempotency_key=f"compat-apoc-{cuit}",
    )


__all__ = ["BOT_ROUTE_ALIASES", "router"]
