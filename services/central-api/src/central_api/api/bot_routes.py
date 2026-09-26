"""Routers V1 por bot; todos delegan al submitter y job store de V3."""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from central_api.api.bot_compat import BOT_ROUTE_ALIASES, _bad_payload, _decode_payload
from central_api.api.bot_payloads import public_bot_compat_body_schema
from central_api.api.bots import CATALOGUE, OPERATIONS, CreateJobResponse, submit_job
from central_api.api.dependencies import project_job, require_api_principal
from central_api.api.jobs import (
    CancelBody,
    JobStatusResponse,
    _visible_job,
    _visible_job_db,
    cancel_job,
)
from central_api.db import db_configurado
from central_api.security.principals import ApiPrincipal

try:
    from central_api.api.bot_schemas import get_request_model
except ImportError:  # pragma: no cover - transitional compatibility during schema rollout
    from pydantic import ConfigDict, create_model

    class _FallbackRequest(BaseModel):
        model_config = ConfigDict(extra="allow")

    def get_request_model(bot: str, operation: str) -> type[BaseModel]:
        """Permissive flat fallback until the shared schema module is available."""
        return create_model(
            f"{bot}_{operation}_FallbackRequest",
            __base__=_FallbackRequest,
        )

    def get_openapi_examples(bot: str, operation: str) -> dict[str, dict[str, Any]]:
        return {
            "ejemplo": {
                "summary": f"Solicitud {bot}/{operation}",
                "description": "Cuerpo plano de compatibilidad del bot.",
                "value": {},
            }
        }


router = APIRouter()


class BotRouteLinks(BaseModel):
    job: str
    status: str
    cancel: str


class BotRouteCreateResponse(CreateJobResponse):
    """Acuse de alta con enlaces a consulta y cancelación por alias V1."""

    links: BotRouteLinks

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "success": True,
                "job_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abc",
                "status": "PENDIENTE",
                "links": {
                    "job": "/api/v3/jobs/0190f0c0-7f5b-7b2e-9f7e-123456789abc",
                    "status": "/api/v3/ccma/consulta/0190f0c0-7f5b-7b2e-9f7e-123456789abc",
                    "cancel": "/api/v3/ccma/consulta/cancelar/0190f0c0-7f5b-7b2e-9f7e-123456789abc",
                },
            }
        }
    )


_SECRET_FIELDS = {"clave", "clave_representante", "contrasena", "clave_encriptada"}


def _split_path(route_path: str) -> tuple[str, str]:
    prefix, separator, operation_path = route_path.rpartition("/")
    if not separator or not prefix:
        raise ValueError(f"ruta de bot inválida: {route_path}")
    return prefix, f"/{operation_path}"


def _body_openapi(bot: str, operation: str, route_path: str | None) -> dict[str, Any]:
    """Keep the published V1 body shape while attaching central examples."""
    model = get_request_model(bot, operation)
    # The old aliases have an externally consumed V1 schema snapshot. The
    # shared model drives request validation; this adapter preserves those
    # public property names while exposing examples from bot_schemas.
    schema = public_bot_compat_body_schema(
        bot,
        operation,
        route_path=route_path,
    )
    schema_examples = schema.get("examples") or model.model_json_schema().get("examples") or []
    examples = {
        "consulta_habitual": {
            "summary": "Ejemplo de la operación",
            "description": "Ejemplo de entrada tomado del schema histórico o del plugin correspondiente.",
            "value": schema_examples[0] if schema_examples else {},
        }
    }
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": schema,
                    "examples": examples,
                }
            },
        }
    }


def _with_links(response: JSONResponse, route_path: str) -> JSONResponse:
    if response.status_code != 202:
        return response
    try:
        body = json.loads(response.body)
        job_id = str(body["job_id"])
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return response
    body["links"] = {
        "job": f"/api/v3/jobs/{job_id}",
        "status": f"/api/v3{route_path}/{job_id}",
        "cancel": f"/api/v3{route_path}/cancelar/{job_id}",
    }
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "content-type"}
    }
    return JSONResponse(
        status_code=response.status_code,
        content=body,
        headers=headers,
        background=response.background,
    )


def _create_handler(
    bot: str,
    operation: str,
    route_path: str,
    request_model: type[BaseModel],
):
    async def create_bot_job(
        request: Request,
        principal: ApiPrincipal = Depends(require_api_principal),
        idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    ) -> JSONResponse:
        decoded = await _decode_payload(request)
        if isinstance(decoded, JSONResponse):
            return decoded
        payload, credentials = decoded
        # Validate and coerce the operation-specific flat body. Error details
        # are intentionally generic so Pydantic cannot echo a submitted secret.
        input_body = dict(payload)
        input_body.update(credentials)
        try:
            typed_body = request_model.model_validate(input_body)
        except ValidationError:
            return _bad_payload("El cuerpo no coincide con el esquema de la operación.")
        normalized_body = typed_body.model_dump(exclude_unset=True)
        credentials = dict(credentials)
        for field in _SECRET_FIELDS:
            if field in normalized_body:
                credentials.setdefault(field, normalized_body.pop(field))
        response = await submit_job(
            bot=bot,
            operacion=operation,
            payload=normalized_body,
            credentials=credentials,
            request=request,
            principal=principal,
            idempotency_key=idempotency_key,
        )
        return _with_links(response, route_path)

    create_bot_job.__name__ = f"create_{bot}_{operation}_{route_path.strip('/').replace('/', '_').replace('-', '_')}"
    return create_bot_job


def _status_handler(bot: str, operation: str):
    async def status_bot_job(
        job_id: str,
        principal: ApiPrincipal = Depends(require_api_principal),
    ) -> JSONResponse:
        if db_configurado():
            job = await _visible_job_db(job_id, principal)
        else:
            job = _visible_job(job_id, principal)
        if (
            job is None
            or job.bot != bot
            or job.operation != operation
        ):
            from central_api.security.secret_redaction import public_error

            return JSONResponse(status_code=404, content=public_error("not_found"))
        return JSONResponse(
            status_code=200,
            content=project_job(job, getattr(job, "_meta", None)),
        )

    status_bot_job.__name__ = f"status_{bot}_{operation}"
    return status_bot_job


def _cancel_handler(bot: str, operation: str):
    async def cancel_bot_job(
        job_id: str,
        request: Request,
        principal: ApiPrincipal = Depends(require_api_principal),
    ) -> JSONResponse:
        if db_configurado():
            job = await _visible_job_db(job_id, principal)
        else:
            job = _visible_job(job_id, principal)
        if (
            job is None
            or job.bot != bot
            or job.operation != operation
        ):
            from central_api.security.secret_redaction import public_error

            return JSONResponse(status_code=404, content=public_error("not_found"))
        motivo = "cancelado desde alias V1"
        if request.headers.get("content-type", "").startswith("application/json"):
            try:
                raw = await request.json()
            except (ValueError, json.JSONDecodeError):
                raw = {}
            if isinstance(raw, dict):
                motivo = str(raw.get("motivo") or motivo)[:500]
        return await cancel_job(job_id, CancelBody(motivo=motivo), principal)

    cancel_bot_job.__name__ = f"cancel_{bot}_{operation}"
    return cancel_bot_job


def build_bot_routers() -> list[APIRouter]:
    """Build one prefixed, bot-tagged router per V1 bot path prefix."""
    grouped: dict[tuple[str, str], list[tuple[str, str, str | None]]] = defaultdict(list)
    aliases_by_operation: set[tuple[str, str]] = set()
    alias_paths: set[str] = set()

    for route_path, bot, operation in BOT_ROUTE_ALIASES:
        if (bot, operation) not in OPERATIONS:
            raise RuntimeError(
                f"alias de bot sin operación canónica: {route_path} -> {bot}/{operation}"
            )
        prefix, operation_path = _split_path(route_path)
        grouped[(bot, prefix)].append((operation_path, operation, route_path))
        aliases_by_operation.add((bot, operation))
        alias_paths.add(route_path)

    # Operations without a legacy alias still get their canonical V1-shaped
    # /{bot}/{operation} triplet. This covers the full catalogue, including APOC.
    for bot_item in CATALOGUE:
        bot = str(bot_item["bot"])
        for operation_value in bot_item["operaciones"]:
            operation = str(operation_value)
            if (bot, operation) in aliases_by_operation:
                continue
            canonical_path = f"/{bot}/{operation}"
            if canonical_path in alias_paths:
                continue
            grouped[(bot, f"/{bot}")].append(
                (f"/{operation}", operation, None)
            )

    generated: list[APIRouter] = []
    for (bot, prefix), operations in grouped.items():
        bot_router = APIRouter(prefix=prefix, tags=[bot])
        for operation_path, operation, alias_path in operations:
            full_route = f"{prefix}{operation_path}"
            model = get_request_model(bot, operation)
            body_doc = _body_openapi(bot, operation, alias_path)
            safe_name = full_route.strip("/").replace("/", "_").replace("-", "_")

            bot_router.add_api_route(
                operation_path,
                _create_handler(bot, operation, full_route, model),
                methods=["POST"],
                status_code=202,
                response_model=BotRouteCreateResponse,
                openapi_extra=body_doc,
                summary=f"Crear job de {bot}",
                name=f"{bot}_{operation}_{safe_name}_create",
            )
            bot_router.add_api_route(
                f"{operation_path}/{{job_id}}",
                _status_handler(bot, operation),
                methods=["GET"],
                response_model=JobStatusResponse,
                summary=f"Consultar estado y resultado de {bot}",
                name=f"{bot}_{operation}_{safe_name}_status",
            )
            bot_router.add_api_route(
                f"{operation_path}/cancelar/{{job_id}}",
                _cancel_handler(bot, operation),
                methods=["POST"],
                response_model=JobStatusResponse,
                openapi_extra={
                    "requestBody": {
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": CancelBody.model_json_schema(),
                            }
                        },
                    }
                },
                summary=f"Cancelar job de {bot}",
                name=f"{bot}_{operation}_{safe_name}_cancel",
            )
        generated.append(bot_router)
    return generated


bot_routers = build_bot_routers()
for _bot_router in bot_routers:
    router.include_router(_bot_router)


__all__ = ["bot_routers", "build_bot_routers", "router"]
