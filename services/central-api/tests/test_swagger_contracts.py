"""Contrato OpenAPI público de los aliases V2 mantenidos por la central."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api.bot_compat import BOT_ROUTE_ALIASES  # noqa: E402
from central_api.api.bot_payloads import (  # noqa: E402
    V1_SCHEMA_BY_ALIAS,
    _HISTORICAL_FIELD_ALIASES,
    _HISTORICAL_SCHEMA_BY_OPERATION,
    _v1_request_schemas,
    public_bot_body_schema,
)
from central_api.api.bot_schemas import get_request_model  # noqa: E402
from central_api.main import create_app  # noqa: E402
from mrbot_contracts.request_fields import V2_OPERATION_FIELDS  # noqa: E402

V1_SNAPSHOT = json.loads(
    (SRC / "central_api" / "api" / "v1_request_schemas.json").read_text(
        encoding="utf-8"
    )
)


def test_aliases_documentan_solo_los_campos_historicos_de_cada_operacion() -> None:
    schema = create_app().openapi()

    for route_path, bot, operation_name in BOT_ROUTE_ALIASES:
        operation = schema["paths"][f"/api/v3{route_path}"]["post"]
        request_body = operation["requestBody"]
        assert request_body["required"] is True
        body_schema = request_body["content"]["application/json"]["schema"]
        expected = list(get_request_model(bot, operation_name).model_fields)
        assert list(body_schema["properties"]) == expected
        if (bot, operation_name) in V2_OPERATION_FIELDS:
            assert expected == list(V2_OPERATION_FIELDS[(bot, operation_name)])
        assert "eliminar_descargas" not in body_schema["properties"]
        assert "nombre_archivo" not in body_schema["properties"]
        documented = json.dumps(body_schema, ensure_ascii=False).lower()
        assert "valor-de-ejemplo" not in documented
        assert "archivo-ejemplo" not in documented
        request_examples = operation["requestBody"]["content"]["application/json"]["examples"]
        assert all(set(item["value"]) == set(expected) for item in request_examples.values())


def test_mis_comprobantes_consulta_tiene_el_contrato_v2_en_alias_y_payload_canonico() -> None:
    expected = [
        "clave_encriptada",
        "desde",
        "hasta",
        "cuit_inicio_sesion",
        "representado_nombre",
        "representado_cuit",
        "contrasena",
        "descarga_emitidos",
        "descarga_recibidos",
        "puntos_venta_emitidos",
        "puntos_venta_recibidos",
        "carga_minio",
        "carga_json",
        "timeout_mc",
        "proxy_request",
    ]
    schema = create_app().openapi()
    alias_body = schema["paths"]["/api/v3/mis_comprobantes/consulta"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    assert list(alias_body["properties"]) == expected

    canonical_body = schema["paths"]["/api/v3/bots/{bot}/{operacion}"]["post"][
        "requestBody"
    ]["content"]["application/json"]["schema"]
    payload_model = next(
        payload
        for payload in canonical_body["properties"]["payload"]["oneOf"]
        if payload["examples"][0].get("desde") == "01/01/2024"
        and "timeout_mc" in payload["properties"]
    )
    assert list(payload_model["properties"]) == expected


def test_removed_retention_and_output_name_fields_are_ignored_for_legacy_clients() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v3/mis_comprobantes/consulta",
        headers={"Idempotency-Key": "retired-fields-are-ignored"},
        json={
            "desde": "01/01/2024",
            "hasta": "31/12/2024",
            "cuit_inicio_sesion": "20123456780",
            "representado_nombre": "Empresa Ejemplo S.A.",
            "representado_cuit": "30876543210",
            "contrasena": "mi_contraseña_secreta",
            "descarga_emitidos": True,
            "descarga_recibidos": False,
            "eliminar_descargas": False,
            "conservar_descargas": True,
            "nombre_archivo": "cliente.txt",
            "nombre_archivo_descarga": "cliente.csv",
            "archivo_nombre": "cliente.txt",
            "name_hint": "cliente.pdf",
        },
    )
    assert response.status_code == 202


def test_ccma_muestra_campos_requeridos_y_cancelacion() -> None:
    schema = create_app().openapi()

    create_schema = schema["paths"]["/api/v3/ccma/consulta"]["post"]
    body_schema = create_schema["requestBody"]["content"]["application/json"]["schema"]
    assert "cuit_representado" in body_schema["required"]
    assert body_schema["examples"][0]["cuit_representado"] == "20123456789"
    assert list(body_schema["properties"]) == [
        "clave_encriptada",
        "cuit_representante",
        "clave_representante",
        "cuit_representado",
        "proxy_request",
        "movimientos",
        "pdf",
    ]

    cancel_schema = schema["paths"]["/api/v3/ccma/consulta/cancelar/{job_id}"]["post"]
    cancel_body = cancel_schema["requestBody"]["content"]["application/json"]["schema"]
    assert cancel_body["properties"]["motivo"]["maxLength"] == 500


def test_body_documentado_no_cambia_el_flujo_json_ni_multipart() -> None:
    cliente = TestClient(create_app())

    json_response = cliente.post(
        "/api/v3/ccma/consulta",
        headers={"Idempotency-Key": "swagger-contract-json"},
        json={
            "cuit_representante": "20123456789",
            "clave_representante": "DEMO_NO_USAR",
            "cuit_representado": "20123456789",
            "eliminar_descargas": False,
            "nombre_archivo": "cliente.txt",
        },
    )
    assert json_response.status_code == 202

    multipart_response = cliente.post(
        "/api/v3/portal_iva/carga",
        files={"archivo": ("ventas.txt", b"contenido", "text/plain")},
        headers={"Idempotency-Key": "swagger-contract-multipart"},
    )
    assert multipart_response.status_code == 422
    assert "uploads" in multipart_response.json()["detail"]["message"]


def test_ruta_canonica_muestra_el_envelope_y_los_payloads_de_bots() -> None:
    schema = create_app().openapi()
    operation = schema["paths"]["/api/v3/bots/{bot}/{operacion}"]["post"]
    body = operation["requestBody"]["content"]["application/json"]["schema"]

    assert body["required"] == ["payload"]
    assert len(body["properties"]["payload"]["oneOf"]) == 42
    assert body["example"]["payload"]["cuit_representado"] == "20123456789"
    assert body["example"]["credentials"]["clave"] == "clave_fiscal"
    for payload_schema in body["properties"]["payload"]["oneOf"]:
        assert set(payload_schema["properties"]) == set(payload_schema["examples"][0])
        assert "credentials" not in payload_schema["properties"]
        assert "credentials" not in payload_schema["examples"][0]


def test_catalogo_y_status_publican_schemas_y_ejemplos_completos() -> None:
    app = create_app()
    schema = app.openapi()

    catalogo = schema["paths"]["/api/v3/bots"]["get"]
    catalogo_response = catalogo["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert catalogo_response["$ref"].endswith("BotCatalogResponse")

    detalle = schema["paths"]["/api/v3/bots/{bot}"]["get"]
    detalle_response = detalle["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert detalle_response["$ref"].endswith("BotDetailResponse")

    status_response = schema["paths"]["/api/v3/ccma/consulta/{job_id}"]["get"]
    status_schema = status_response["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert status_schema["$ref"].endswith("JobStatusResponse")

    cliente = TestClient(app)
    detalle_json = cliente.get("/api/v3/bots/ccma")
    assert detalle_json.status_code == 200
    operacion = detalle_json.json()["operaciones"][0]
    assert operacion["input_schema"]["properties"]["cuit_representado"]
    assert operacion["example"]["payload"]["cuit_representado"] == "20123456789"
    assert operacion["credentials_schema"]["example"]["clave"] == "clave_fiscal"


def test_examples_canonicos_reutilizan_los_valores_historicos() -> None:
    snapshots = _v1_request_schemas()

    for (bot, operation), schema_name in _HISTORICAL_SCHEMA_BY_OPERATION.items():
        historical = snapshots[schema_name]["schema"]["properties"]
        example = public_bot_body_schema(bot, operation)["examples"][0]

        for canonical_name, value in example.items():
            if canonical_name == "credentials":
                context = next(
                    (
                        historical[name]["example"]
                        for name in (
                            "cuit_representante",
                            "cuit_login",
                            "cuit_inicio_sesion",
                        )
                        if name in historical and "example" in historical[name]
                    ),
                    None,
                )
                secret = next(
                    (
                        historical[name]["example"]
                        for name in ("clave_representante", "clave", "contrasena")
                        if name in historical and "example" in historical[name]
                    ),
                    None,
                )
                expected = (
                    {
                        "cuit_representante": context or "20123456789",
                        "clave": secret or "clave_fiscal",
                    }
                    if context is not None or secret is not None
                    else {}
                )
                assert value == expected
                continue

            candidates = (canonical_name,) + _HISTORICAL_FIELD_ALIASES.get(
                canonical_name, ()
            )
            historical_name = next(
                (
                    name
                    for name in candidates
                    if name in historical and "example" in historical[name]
                ),
                None,
            )
            if historical_name is not None:
                assert value == historical[historical_name]["example"], (
                    bot,
                    operation,
                    canonical_name,
                    historical_name,
                )
