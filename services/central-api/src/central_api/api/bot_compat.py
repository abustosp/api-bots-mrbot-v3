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
    """Lee JSON plano/envelope o formulario sin proxificar archivos."""
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


@router.get("/apoc/consulta/{cuit}", status_code=202, tags=["apoc"])
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
