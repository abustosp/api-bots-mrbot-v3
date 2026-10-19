"""Routers V1 por bot; todos delegan al submitter y job store de V3."""

from __future__ import annotations

import json
from collections import defaultdict
from copy import deepcopy
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, ValidationError

from central_api.api.bot_compat import (
    BOT_ROUTE_ALIASES,
    _bad_payload,
    _decode_payload,
    _openapi_error,
)
from central_api.api.bot_payloads import (
    OPERATION_DESCRIPTIONS,
    public_bot_compat_body_schema,
)
from central_api.api.file_inputs import (
    MULTIPART_FIELDS,
    externalizar_archivos,
    file_not_uploaded,
)
from central_api.api.bots import CATALOGUE, OPERATIONS, CreateJobResponse, submit_job
from central_api.api.dependencies import project_job, require_api_principal
from central_api.api.jobs import (
    CancelBody,
    JOB_STATUS_EXAMPLES,
    JobStatusResponse,
    _visible_job,
    _visible_job_db,
    cancel_job,
)
from central_api.db import db_configurado
from central_api.input_files import INPUT_FILE_FIELDS, KEY_FIELDS
from central_api.security.principals import ApiPrincipal

try:
    from central_api.api.bot_schemas import (
        V2CuitValidationError,
        get_request_model,
        normalize_v2_request_body,
    )
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

    class V2CuitValidationError(ValueError):
        detail: dict[str, Any] = {}

    def normalize_v2_request_body(bot: str, operation: str, body: dict[str, Any]) -> dict[str, Any]:
        return body

    def get_openapi_examples(bot: str, operation: str) -> dict[str, dict[str, Any]]:
        return {
            "ejemplo": {
                "summary": f"Solicitud {bot}/{operation}",
                "description": "Cuerpo plano de compatibilidad del bot.",
                "value": {},
            }
        }


router = APIRouter()

_EXAMPLE_JOB_ID = "0190f0c0-7f5b-7b2e-9f7e-123456789abc"


def _job_status_response() -> dict[str, Any]:
    """Documenta los estados públicos posibles al consultar o cancelar una ejecución."""
    summaries = {
        "pendiente": "Ejecución pendiente",
        "corriendo": "Ejecución en curso",
        "completo_ok": "Ejecución completa sin errores",
        "completo_parcial": "Ejecución completa con resultado parcial",
        "completo_error": "Ejecución completa con error",
        "cancelado": "Ejecución cancelada",
    }
    examples = {
        name: {"summary": summary, "value": JOB_STATUS_EXAMPLES[name]}
        for name, summary in summaries.items()
    }
    return {
        "description": (
            "Estado y resultado de la ejecución. `files` contiene los archivos disponibles "
            "con nombre, URL y tamaño; `error` y `error_detail` explican los resultados "
            "parciales o fallidos."
        ),
        "content": {"application/json": {"examples": examples}},
    }


def _not_found_response() -> dict[str, Any]:
    return _openapi_error(
        "La ejecución no existe, no pertenece a la cuenta autenticada o no corresponde a esta operación.",
        "not_found",
        "Ejecución no encontrada.",
    )


def _validation_response(description: str) -> dict[str, Any]:
    return _openapi_error(
        description,
        "validation",
        "Revise los datos enviados y vuelva a intentar la solicitud.",
    )


def _unavailable_response() -> dict[str, Any]:
    return _openapi_error(
        "No fue posible completar la solicitud por una indisponibilidad temporal. Reintente más tarde.",
        "service_not_enabled",
        "Servicio temporalmente no disponible.",
    )


def _auth_response() -> dict[str, Any]:
    return _openapi_error(
        "La clave de acceso falta, no es válida, venció o pertenece a una cuenta deshabilitada. Envíe una clave activa.",
        "authentication",
        "Credenciales inválidas.",
    )


def _idempotency_response() -> dict[str, Any]:
    return _openapi_error(
        "La Idempotency-Key ya se utilizó con otros datos. Para recuperar la ejecución existente, repita exactamente la solicitud original; para crear otra ejecución, envíe una clave nueva.",
        "idempotency_conflict",
        "La clave de idempotencia ya se usó con otro contenido.",
    )


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
    """Documenta únicamente referencias a archivos cargados previamente."""
    model = get_request_model(bot, operation)
    schema = model.model_json_schema()
    file_keys = KEY_FIELDS.get((bot, operation), {})
    file_list_field = INPUT_FILE_FIELDS.get((bot, operation))
    properties = dict(schema.get("properties") or {})
    legacy_file_fields = set(MULTIPART_FIELDS.get((bot, operation), {}))
    if (bot, operation) == ("portal_iva", "importar"):
        legacy_file_fields.update({"ventas_txt", "compras_txt"})
    for name in list(properties):
        lowered = name.lower()
        if "b64" in lowered or "base64" in lowered or name in legacy_file_fields:
            properties.pop(name, None)

    required = [
        name for name in schema.get("required", [])
        if "b64" not in name.lower() and "base64" not in name.lower()
        and name not in legacy_file_fields
    ]
    for field_key, runtime_field in file_keys.items():
        properties[field_key] = {
            "type": "string",
            "minLength": 1,
            "description": (
                "object_key obtenido previamente con POST /api/v3/uploads. Debe pertenecer "
                "a la cuenta autenticada y a esta operación."
            ),
            "example": f"<usuario>/{bot}/20123456786/{runtime_field.removesuffix('_path')}.txt",
        }
        legacy_name = runtime_field.removesuffix("_path") + "_b64"
        if legacy_name in schema.get("required", []):
            required.append(field_key)
    if file_list_field:
        properties[file_list_field] = {
            "type": "array",
            "minItems": 1,
            "items": {"type": "string", "minLength": 1},
            "description": "Lista de object_key obtenidos previamente con POST /api/v3/uploads.",
            "example": [f"<usuario>/{bot}/<identificador>/F8010.001.pem"],
        }
        if "files" in schema.get("required", []):
            required.append(file_list_field)
    schema["properties"] = properties
    if required:
        schema["required"] = list(dict.fromkeys(required))
    else:
        schema.pop("required", None)

    def limpiar_ejemplo(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: limpiar_ejemplo(item)
                for key, item in value.items()
                if "b64" not in str(key).lower()
                and "base64" not in str(key).lower()
                and key not in legacy_file_fields
            }
        if isinstance(value, list):
            return [limpiar_ejemplo(item) for item in value]
        return value

    schema = limpiar_ejemplo(schema)
    source_examples = model.model_json_schema().get("examples") or [{}]
    reference_example = limpiar_ejemplo(source_examples[0])
    for field_key in file_keys:
        reference_example.pop(field_key, None)
    if file_list_field:
        reference_example.pop(file_list_field, None)
    for name, value in list(reference_example.items()):
        if "cuit" in name.lower() and isinstance(value, str):
            reference_example[name] = "20123456786"

    if not (file_keys or file_list_field or legacy_file_fields):
        return {
            "requestBody": {
                "required": True,
                "content": {"application/json": {
                    "schema": schema,
                    "examples": {"consulta_habitual": {
                        "summary": "Ejemplo de la operación",
                        "value": reference_example,
                    }},
                }},
            }
        }

    path = route_path or f"/{bot}/{operation}"
    if (bot, operation) == ("carga_portal_iva", "cargar"):
        selected_file_keys = ["liv_cbte_key", "liv_alicuota_key"]
    elif bot == "portal_iva" and operation in {"importar", "gestionar"}:
        selected_file_keys = ["ventas_txt_key", "ventas_alicuotas_txt_key"]
    else:
        selected_file_keys = list(file_keys)[:1]
    selected_file_keys = [key for key in selected_file_keys if key in file_keys]

    filename_by_field = {
        "liv_cbte": "ventas.txt",
        "liv_alicuota": "ventas_alicuotas.txt",
        "lic_cbte": "compras.txt",
        "lic_alicuota": "compras_alicuotas.txt",
        "ventas_txt": "ventas.txt",
        "ventas_alicuotas_txt": "ventas_alicuotas.txt",
        "compras_txt": "compras.txt",
        "compras_alicuotas_txt": "compras_alicuotas.txt",
    }
    upload_requests: list[dict[str, Any]] = []
    upload_responses: list[dict[str, Any]] = []
    example_user = "550e8400-e29b-41d4-a716-446655440000"
    for index, field_key in enumerate(selected_file_keys, start=1):
        upload_field = field_key.removesuffix("_key")
        extension = ".csv" if upload_field.startswith("csv_") else ".txt"
        content_type = "text/csv" if extension == ".csv" else "text/plain"
        if upload_field == "archivo_excel":
            extension = ".xlsx"
            content_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        filename = filename_by_field.get(upload_field, f"{upload_field}{extension}")
        object_key = f"{example_user}/{bot}/20123456786/{filename}"
        upload_requests.append({
            "bot": bot,
            "operacion": operation,
            "campo": upload_field,
            "cuit_representado": "20123456786",
            "filename": filename,
            "content_type": content_type,
            "size_bytes": 120,
        })
        upload_responses.append({
            "upload_id": f"0190f0c0-7f5b-7b2e-9f7e-{index:012d}",
            "object_key": object_key,
            "bucket": "uploads",
            "upload_url": "<url temporal de carga>",
            "upload_url_expires_in": 900,
            "expires_at": "2030-01-01T00:00:00Z",
            "required_headers": {"Content-Type": content_type},
        })
        reference_example[field_key] = object_key

    if file_list_field:
        filename = "F8010.001.pem"
        object_key = f"{example_user}/{bot}/20123456786/{filename}"
        upload_requests.append({
            "bot": bot,
            "operacion": operation,
            "campo": file_list_field,
            "cuit_representado": "20123456786",
            "filename": filename,
            "content_type": "application/octet-stream",
            "size_bytes": 120,
        })
        upload_responses.append({
            "upload_id": "0190f0c0-7f5b-7b2e-9f7e-000000000001",
            "object_key": object_key,
            "bucket": "uploads",
            "upload_url": "<url temporal de carga>",
            "upload_url_expires_in": 900,
            "expires_at": "2030-01-01T00:00:00Z",
            "required_headers": {"Content-Type": "application/octet-stream"},
        })
        reference_example[file_list_field] = [object_key]

    if bot == "portal_iva" and operation == "gestionar":
        reference_example["descarga_ventas"] = True
        reference_example["descarga_compras"] = False
    expected_file_field = next(
        iter(file_keys), file_list_field or "object_key"
    )
    file_error_detail = file_not_uploaded(
        expected_file_field, bot=bot, operation=operation
    ).detail
    examples = {
        "flujo_en_dos_pasos": {
            "summary": "Paso 2: enviar el object_key en la operación",
            "description": (
                "Paso 1: por cada archivo, solicite un ticket con POST /api/v3/uploads y el cuerpo "
                f"{json.dumps(upload_requests, ensure_ascii=False)}. Cada respuesta 201 incluye "
                f"{json.dumps(upload_responses, ensure_ascii=False)}. Cargue cada archivo usando "
                "la dirección temporal y los encabezados indicados. Paso 2: envíe las referencias "
                f"en POST /api/v3{path}; no adjunte archivos ni incluya su contenido."
            ),
            "value": reference_example,
        }
    }
    request_body = {
        "required": True,
        "description": (
            "Los archivos solo se reciben mediante el flujo de dos pasos: solicite un ticket "
            "con POST /api/v3/uploads, cargue el archivo al bucket uploads y envíe el object_key "
            "en este JSON. No se aceptan archivos adjuntos ni contenido Base64. "
            f"Ejemplos del paso 1: solicitudes POST /api/v3/uploads {json.dumps(upload_requests, ensure_ascii=False)}; "
            f"respuestas 201: {json.dumps(upload_responses, ensure_ascii=False)}."
        ),
        "content": {"application/json": {"schema": schema, "examples": examples}},
    }
    return {
        "requestBody": request_body,
        "responses": {
            422: {
                "description": (
                    "Referencia inválida, archivo inexistente o eliminado, formato/tamaño no "
                    "admitido. No se aceptan archivos adjuntos ni contenido Base64."
                ),
                "content": {"application/json": {"example": {"detail": file_error_detail}}},
            }
        },
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
        try:
            uploaded_files = []
            if request.headers.get("content-type", "").lower().startswith("multipart/form-data"):
                expected_field = next(
                    iter(KEY_FIELDS.get((bot, operation), {})),
                    INPUT_FILE_FIELDS.get((bot, operation), "object_key"),
                )
                raise file_not_uploaded(
                    expected_field, bot=bot, operation=operation
                )
            else:
                decoded = await _decode_payload(
                    request, bot=bot, operation=operation
                )
                if isinstance(decoded, JSONResponse):
                    return decoded
                payload, credentials = decoded
            payload = await externalizar_archivos(
                bot, operation, payload, credentials, uploaded_files, principal
            )
        except HTTPException as exc:
            messages = {
                413: "file_too_large",
                415: "unsupported_file_type",
                422: "validation",
                503: "storage_unavailable",
            }
            if isinstance(exc.detail, dict):
                error_code = str(exc.detail.get("error_code") or messages.get(exc.status_code, "validation"))
                message = str(exc.detail.get("message") or "Revise los datos enviados.")
            else:
                error_code = messages.get(exc.status_code, "validation")
                message = str(exc.detail)
                if "uploads" not in message.lower():
                    message = (
                        f"{message} Use POST /api/v3/uploads y envíe el object_key "
                        "en el campo de referencia correspondiente."
                    )
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": {
                    "error_code": error_code,
                    "message": message,
                }},
                headers=exc.headers,
            )
        # Validate and coerce the operation-specific flat body. Error details
        # are intentionally generic so Pydantic cannot echo a submitted secret.
        input_body = dict(payload)
        input_body.update(credentials)
        # Las referencias de archivo se validan en ``externalizar_archivos``.
        claves_archivo = {
            campo_key: input_body.get(campo_key)
            for campo_key in KEY_FIELDS.get((bot, operation), {})
            if input_body.get(campo_key) is not None
        }
        campo_archivos = INPUT_FILE_FIELDS.get((bot, operation))
        referencias_archivo = (
            {campo_archivos: input_body[campo_archivos]}
            if campo_archivos and campo_archivos in input_body
            else {}
        )
        validacion = dict(input_body)
        # V2 allowed all three plaintext names for the credential field each
        # bot declared.  Resolve them before Pydantic enforces that bot's
        # historical required field.  Ciphertext is only a validation stand-in
        # here, and is kept encrypted for the credential custody layer.
        credential_names = ("clave", "clave_representante", "contrasena")
        canonical_credential = next(
            (name for name in credential_names if name in request_model.model_fields),
            None,
        )
        supplied_plaintext = [
            (name, validacion[name])
            for name in credential_names
            if name in validacion and validacion[name] not in (None, "")
        ]
        if supplied_plaintext and any(
            value != supplied_plaintext[0][1] for _, value in supplied_plaintext[1:]
        ):
            return _bad_payload("Los campos de credencial no coinciden.")
        validation_only_secret_fields: set[str] = set()
        if canonical_credential:
            if supplied_plaintext:
                validacion[canonical_credential] = supplied_plaintext[0][1]
            elif validacion.get("clave_encriptada") not in (None, ""):
                validacion[canonical_credential] = validacion["clave_encriptada"]
                validation_only_secret_fields.add(canonical_credential)
        try:
            typed_body = request_model.model_validate(validacion)
        except ValidationError:
            return _bad_payload("El cuerpo no coincide con el esquema de la operación.")
        # V2 serialized every model default into the request handed to its bot.
        normalized_body = typed_body.model_dump()
        # Un opcional ausente o enviado como ``null`` queda en ``None``: en V2
        # eso significaba "usar el valor por defecto". Se omite para que el
        # worker aplique su propio valor por defecto en vez de rechazar ``None``.
        normalized_body = {
            name: value for name, value in normalized_body.items()
            if value is not None
        }
        for field in validation_only_secret_fields:
            normalized_body.pop(field, None)
        normalized_body.update(claves_archivo)
        # The V2 request model treated these as multipart file parameters, not
        # JSON properties. V3 also accepts upload references, so retain their
        # object keys when the historical model serializes its declared fields.
        normalized_body.update(referencias_archivo)
        try:
            normalized_body = normalize_v2_request_body(bot, operation, normalized_body)
        except V2CuitValidationError as exc:
            return JSONResponse(status_code=400, content={"detail": exc.detail})
        except ValueError:
            return _bad_payload("Revise los períodos, las fechas y los campos requeridos.")
        if bot == "carga_portal_iva":
            # V2 used the representative CUIT when no separate represented
            # taxpayer was selected.  The worker requires this execution value.
            if not normalized_body.get("cuit_representado"):
                normalized_body["cuit_representado"] = normalized_body.get(
                    "cuit_representante"
                )
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
        job_id: Annotated[
            str,
            Path(
                description="Identificador de la ejecución devuelto al crearla.",
                examples=[_EXAMPLE_JOB_ID],
            ),
        ],
        principal: ApiPrincipal = Depends(require_api_principal),
    ) -> JSONResponse:
        if db_configurado():
            job = await _visible_job_db(job_id, principal)
        else:
            job = _visible_job(job_id, principal)
        projected = project_job(job, getattr(job, "_meta", None)) if job is not None else None
        if job is None or job.bot != bot or projected["operation"] != operation:
            from central_api.security.secret_redaction import public_error

            return JSONResponse(status_code=404, content=public_error("not_found"))
        return JSONResponse(
            status_code=200,
            content=projected,
        )

    status_bot_job.__name__ = f"status_{bot}_{operation}"
    return status_bot_job


def _cancel_handler(bot: str, operation: str):
    async def cancel_bot_job(
        job_id: Annotated[
            str,
            Path(
                description="Identificador de la ejecución que se desea cancelar.",
                examples=[_EXAMPLE_JOB_ID],
            ),
        ],
        request: Request,
        principal: ApiPrincipal = Depends(require_api_principal),
    ) -> JSONResponse:
        if db_configurado():
            job = await _visible_job_db(job_id, principal)
        else:
            job = _visible_job(job_id, principal)
        projected = project_job(job, getattr(job, "_meta", None)) if job is not None else None
        if job is None or job.bot != bot or projected["operation"] != operation:
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
    hidden_schema_paths = {
        # Both V2 ARBA prefixes used the same request, operation and log model.
        # Keep the longer historical path as the sole documented route.
        "/arba/consulta",
        # V2's /vep_archivo/carga was an alias of /vep/carga.
        "/vep_archivo/carga",
        # Historical canonical bot keys remain usable for old jobs, but V2
        # exposed these capabilities only through mis_comprobantes.
        "/comprobantes/consultar",
        "/comprobantes/solicitar",
        "/comprobantes/historial",
    }

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
            include_in_schema = full_route not in hidden_schema_paths
            model = get_request_model(bot, operation)
            body_doc = _body_openapi(bot, operation, alias_path)
            file_responses = body_doc.pop("responses", {})
            safe_name = full_route.strip("/").replace("/", "_").replace("-", "_")

            bot_router.add_api_route(
                operation_path,
                _create_handler(bot, operation, full_route, model),
                methods=["POST"],
                include_in_schema=include_in_schema,
                status_code=202,
                response_model=BotRouteCreateResponse,
                openapi_extra=body_doc,
                responses={
                    **file_responses,
                    202: {
                        "description": (
                            "Ejecución aceptada. La respuesta incluye el identificador, el estado inicial "
                            "y enlaces para consultar o cancelar la ejecución."
                        ),
                        "content": {"application/json": {"example": {
                            "success": True,
                            "job_id": _EXAMPLE_JOB_ID,
                            "status": "PENDIENTE",
                            "links": {
                                "job": f"/api/v3/jobs/{_EXAMPLE_JOB_ID}",
                                "status": f"/api/v3{full_route}/{_EXAMPLE_JOB_ID}",
                                "cancel": f"/api/v3{full_route}/cancelar/{_EXAMPLE_JOB_ID}",
                            },
                        }}},
                    },
                    401: _auth_response(),
                    404: _openapi_error(
                        "El bot o la operación no están disponibles. Consulte GET /api/v3/bots y use una operación publicada.",
                        "not_found",
                        "Bot u operación no encontrados.",
                    ),
                    409: _idempotency_response(),
                    422: _validation_response(
                        "Los datos enviados no cumplen el formato requerido o falta información obligatoria. Corrija la solicitud y, para archivos, use una referencia válida de POST /api/v3/uploads."
                    ),
                    429: _openapi_error(
                        "La cuenta agotó la cuota disponible para crear ejecuciones. Espere antes de reintentar; Retry-After indica el intervalo sugerido.",
                        "quota_exhausted",
                        "Cuota agotada.",
                        headers={"Retry-After": {
                            "description": "Segundos sugeridos antes de reintentar.",
                            "schema": {"type": "string", "example": "60"},
                        }},
                    ),
                    503: _unavailable_response(),
                },
                summary=f"Crear ejecución de {operation} para {bot}",
                description=(
                    f"Envía los datos requeridos para la operación {operation} del bot {bot} "
                    "y registra una ejecución asíncrona. La respuesta devuelve `job_id`, el "
                    "estado inicial `PENDIENTE` y enlaces de consulta y cancelación. Consulte "
                    "el enlace de estado para conocer el avance y el resultado. Envíe "
                    "`Idempotency-Key` para identificar reintentos: con la misma clave y los "
                    "mismos datos se recupera la ejecución creada; si reutiliza la clave con "
                    "datos distintos, recibirá `409 idempotency_conflict`."
                    + (
                        " " + OPERATION_DESCRIPTIONS[(bot, operation)]
                        if (bot, operation) in OPERATION_DESCRIPTIONS else ""
                    )
                ),
                name=f"{bot}_{operation}_{safe_name}_create",
            )
            bot_router.add_api_route(
                f"{operation_path}/{{job_id}}",
                _status_handler(bot, operation),
                methods=["GET"],
                include_in_schema=include_in_schema,
                response_model=JobStatusResponse,
                responses={
                    200: _job_status_response(),
                    401: _auth_response(),
                    404: _not_found_response(),
                    422: _validation_response(
                        "El identificador de la ejecución no tiene un formato válido."
                    ),
                    503: _unavailable_response(),
                },
                summary=f"Consultar estado y resultado de {bot}",
                description=(
                    f"Consulta el estado y el resultado de una ejecución de {operation} del bot {bot}. "
                    "Use el `job_id` devuelto al crearla. La respuesta puede indicar PENDIENTE, "
                    "CORRIENDO, COMPLETO con resultado OK/PARCIAL/ERROR, o CANCELADO; incluye "
                    "los archivos disponibles y el detalle de error cuando corresponda. La ruta "
                    "de consulta también figura en los enlaces devueltos al crear la ejecución."
                ),
                name=f"{bot}_{operation}_{safe_name}_status",
            )
            bot_router.add_api_route(
                f"{operation_path}/cancelar/{{job_id}}",
                _cancel_handler(bot, operation),
                methods=["POST"],
                include_in_schema=include_in_schema,
                response_model=JobStatusResponse,
                openapi_extra={
                    "requestBody": {
                        "required": False,
                        "content": {
                            "application/json": {
                                "schema": CancelBody.model_json_schema(),
                                "examples": {
                                    "motivo": {
                                        "summary": "Motivo opcional de cancelación",
                                        "value": {"motivo": "Ya no necesito esta ejecución."},
                                    }
                                },
                            }
                        },
                    }
                },
                responses={
                    200: _job_status_response(),
                    401: _auth_response(),
                    404: _not_found_response(),
                    422: _validation_response(
                        "El cuerpo de cancelación o su campo motivo no tienen un formato válido."
                    ),
                    503: _unavailable_response(),
                },
                summary=f"Cancelar ejecución de {operation} para {bot}",
                description=(
                    f"Solicita cancelar una ejecución de {operation} del bot {bot}, identificada por `job_id`. "
                    "Puede enviar un motivo opcional. La respuesta devuelve el estado actualizado; "
                    "si la ejecución ya comenzó, la cancelación puede completarse de forma cooperativa. "
                    "Use el enlace de cancelación que se devuelve al crear la ejecución."
                ),
                name=f"{bot}_{operation}_{safe_name}_cancel",
            )
        generated.append(bot_router)
    return generated


bot_routers = build_bot_routers()
for _bot_router in bot_routers:
    router.include_router(_bot_router)


__all__ = ["bot_routers", "build_bot_routers", "router"]
