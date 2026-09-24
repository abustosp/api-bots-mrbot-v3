"""Contrato OpenAPI público de los aliases V2 mantenidos por la central."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api.bot_compat import BOT_ROUTE_ALIASES  # noqa: E402
from central_api.api.bot_payloads import V1_SCHEMA_BY_ALIAS  # noqa: E402
from central_api.main import create_app  # noqa: E402

V1_SNAPSHOT = json.loads(
    (SRC / "central_api" / "api" / "v1_request_schemas.json").read_text(
        encoding="utf-8"
    )
)


def test_todos_los_aliases_reproducen_exactamente_los_schemas_v1() -> None:
    schema = create_app().openapi()

    for route_path, _bot, _operation in BOT_ROUTE_ALIASES:
        operation = schema["paths"][f"/api/v3{route_path}"]["post"]
        request_body = operation["requestBody"]
        assert request_body["required"] is True
        body_schema = request_body["content"]["application/json"]["schema"]
        expected = V1_SNAPSHOT[V1_SCHEMA_BY_ALIAS[route_path]]["schema"]
        assert body_schema == expected
        assert list(body_schema["properties"]) == V1_SNAPSHOT[
            V1_SCHEMA_BY_ALIAS[route_path]
        ]["fields"]


def test_ccma_muestra_campos_requeridos_y_cancelacion() -> None:
    schema = create_app().openapi()

    create_schema = schema["paths"]["/api/v3/ccma/consulta"]["post"]
    body_schema = create_schema["requestBody"]["content"]["application/json"]["schema"]
    assert "cuit_representado" in body_schema["required"]
    assert body_schema["properties"]["cuit_representado"]["example"] == "20123456789"
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
        json={"representado_cuit": "20123456789", "periodo": "202608"},
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
    assert body["example"]["payload"]["representado_cuit"] == "20123456789"
    assert body["example"]["credentials"]["clave"] == "REEMPLAZAR_CON_CREDENCIAL_SELLADA"
    for payload_schema in body["properties"]["payload"]["oneOf"]:
        assert set(payload_schema["properties"]) == set(payload_schema["examples"][0])
        assert "credentials" not in payload_schema["properties"]
        assert "credentials" not in payload_schema["examples"][0]
