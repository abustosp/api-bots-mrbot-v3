"""Compatibilidad para las rutas históricas de bots de V1/V2.

Las rutas por operación se generan ahora en :mod:`bot_routes`, que reutiliza
la misma tabla de aliases y el mismo submitter. Este módulo conserva el parser
plano usado por esos endpoints, así como el GET histórico de APOC.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from central_api.api.bots import submit_job
from central_api.api.dependencies import require_api_principal
from central_api.security.principals import ApiPrincipal

router = APIRouter()


def _openapi_error(
    description: str,
    error_code: str,
    message: str,
    *,
    headers: dict | None = None,
    with_correlation_id: bool = False,
) -> dict:
    """Construye un ejemplo del sobre de error devuelto por estas rutas."""
    error_detail = {"error_code": error_code, "message": message}
    if with_correlation_id:
        error_detail["correlation_id"] = "corr-0190f0c0"
    response = {
        "description": description,
        "content": {
            "application/json": {
                "example": {"detail": error_detail}
            }
        },
    }
    if headers:
        response["headers"] = headers
    return response

_V2_SECRET_FIELDS = {
    "clave",
    "clave_representante",
    "contrasena",
    "clave_encriptada",
}


# (path V1, bot canónico, operación canónica). La tabla también define los
# prefijos y paths de operación de los routers generados en bot_routes.py.
BOT_ROUTE_ALIASES: tuple[tuple[str, str, str], ...] = (
    ("/mis_comprobantes/consulta", "mis_comprobantes", "consultar"),
    ("/mis_comprobantes/solicitar_consulta", "mis_comprobantes", "solicitar"),
    ("/mis_comprobantes/historial", "mis_comprobantes", "historial"),
    ("/ccma/consulta", "ccma", "consultar"),
    ("/ccma/compensacion", "ccma_compensacion", "compensar"),
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
    ("/libros_iva/consulta", "libros_portal_iva", "descargar"),
    ("/libros_iva/ddjj", "libros_portal_iva", "descargar_ddjj"),
    ("/libro-sueldos-digital/consulta", "libro_sueldos_digital", "descargar"),
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


async def _decode_payload(
    request: Request,
    *,
    bot: str | None = None,
    operation: str | None = None,
) -> tuple[dict, dict] | JSONResponse:
    """Lee JSON plano y rechaza adjuntos o campos de archivo inline."""
    from central_api.api.file_inputs import file_not_uploaded, tiene_contenido
    from central_api.input_files import INPUT_FILE_FIELDS, KEY_FIELDS

    def reject(expected_field: str) -> JSONResponse:
        error = file_not_uploaded(
            expected_field, bot=bot, operation=operation
        )
        return JSONResponse(status_code=422, content={"detail": error.detail})

    def fallback_key_field() -> str:
        if bot and operation:
            return (
                next(iter(KEY_FIELDS.get((bot, operation), {})), None)
                or INPUT_FILE_FIELDS.get((bot, operation))
                or "object_key"
            )
        return "object_key"

    def inline_field(value: Any) -> str | None:
        if isinstance(value, dict):
            for name, item in value.items():
                lowered = str(name).lower()
                if "b64" in lowered or "base64" in lowered:
                    # Los campos viejos vacíos ("b64": null/""/false) no son
                    # adjuntos: el archivo viaja por object_key.
                    if tiene_contenido(item):
                        known_flag = name in {
                            "carga_b64",
                            "incluir_base64",
                            "incluir_b64",
                            "archivo_historico_b64",
                        } and isinstance(item, bool)
                        if not known_flag:
                            if name == "archivos_base64":
                                return "archivos"
                            if bot and operation:
                                for field_key in KEY_FIELDS.get((bot, operation), {}):
                                    base = field_key.removesuffix("_key")
                                    if name in {f"{base}_b64", f"{base}_base64"}:
                                        return field_key
                            return fallback_key_field()
                nested = inline_field(item)
                if nested:
                    return nested
        elif isinstance(value, list):
            for item in value:
                nested = inline_field(item)
                if nested:
                    return nested
        return None

    content_type = request.headers.get("content-type", "").lower()
    if "application/json" in content_type or not content_type:
        try:
            raw: Any = await request.json()
        except (ValueError, json.JSONDecodeError):
            return _bad_payload("El cuerpo debe ser un objeto JSON válido.")
        if not isinstance(raw, dict):
            return _bad_payload("El cuerpo debe ser un objeto JSON.")
        expected_field = inline_field(raw)
        if expected_field:
            return reject(expected_field)
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
        expected_field = (
            next(iter(KEY_FIELDS.get((bot, operation), {})), None)
            if bot and operation else None
        ) or (INPUT_FILE_FIELDS.get((bot, operation)) if bot and operation else None)
        return reject(expected_field or "object_key")

    expected_field = (
        next(iter(KEY_FIELDS.get((bot, operation), {})), None)
        if bot and operation else None
    ) or (INPUT_FILE_FIELDS.get((bot, operation)) if bot and operation else None)
    return reject(expected_field or "object_key")


@router.get(
    "/apoc/consulta/{cuit}",
    status_code=202,
    tags=["apoc"],
    summary="Solicitar una consulta de APOC",
    description=(
        "Crea o recupera el job de consulta para el CUIT indicado. La respuesta "
        "incluye el identificador y el estado actual del job; úselo con "
        "GET /api/v3/jobs/{job_id} para consultar el resultado. Una repetición para el "
        "mismo CUIT reutiliza la solicitud ya registrada."
    ),
    responses={
        202: {
            "description": "Consulta aceptada o job existente reconocido.",
            "headers": {
                "Location": {"description": "Ruta para consultar el job.", "schema": {"type": "string", "example": "/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc"}},
                "X-Correlation-ID": {"description": "Identificador para dar seguimiento a esta solicitud.", "schema": {"type": "string", "example": "corr-0190f0c0"}},
            },
            "content": {"application/json": {"example": {
                "success": True,
                "job_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abc",
                "status": "PENDIENTE",
            }}},
        },
        401: _openapi_error("La clave de acceso falta, no es válida, venció o pertenece a una cuenta deshabilitada. Envíe una clave activa con el método de autenticación admitido.", "authentication", "Credenciales inválidas."),
        404: _openapi_error("La consulta solicitada no está disponible. Verifique que la operación figure en GET /api/v3/bots.", "not_found", "Recurso no encontrado.", with_correlation_id=True),
        409: _openapi_error("La clave de idempotencia asociada a esta solicitud ya se utilizó con otros datos. Verifique los jobs existentes o use la ruta de creación genérica para enviar una solicitud distinta.", "idempotency_conflict", "La clave de idempotencia ya se usó con otro contenido.", with_correlation_id=True),
        429: _openapi_error("La cuenta agotó la cuota disponible para crear jobs. Espere la renovación de la cuota o consulte el plan de la cuenta antes de reintentar.", "quota_exhausted", "Cuota agotada.", headers={"Retry-After": {"description": "Segundos sugeridos antes de reintentar.", "schema": {"type": "string", "example": "60"}}}, with_correlation_id=True),
        503: _openapi_error("No fue posible autenticar la solicitud o registrar el job por una indisponibilidad temporal. Reintente más tarde. El mismo CUIT identifica la repetición de esta solicitud.", "service_not_enabled", "Servicio temporalmente no disponible.", with_correlation_id=True),
    },
)
async def apoc_compat_get(
    cuit: str,
    request: Request,
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """Registra una consulta APOC y devuelve el ID del job reutilizable."""
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
