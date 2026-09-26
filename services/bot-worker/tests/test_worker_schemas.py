"""Worker-local schema package and OpenAPI endpoint tests."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

WORKER_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(WORKER_SRC))

from bot_worker.config import WorkerConfig  # noqa: E402
from bot_worker.main import JobEnvelope, create_app  # noqa: E402
from bot_worker.schemas import (  # noqa: E402
    BotSchemaDocument,
    get_openapi_examples,
    get_request_model,
    get_schema_document,
)


def test_worker_schema_models_are_operation_specific_and_have_examples() -> None:
    schema_source = (WORKER_SRC / "bot_worker" / "schemas" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "from central_api" not in schema_source
    assert "import central_api" not in schema_source

    model = get_request_model("mis_comprobantes", "consulta")
    assert list(model.model_fields) == [
        "clave_encriptada", "desde", "hasta", "cuit_inicio_sesion",
        "representado_nombre", "representado_cuit", "contrasena",
        "descarga_emitidos", "descarga_recibidos", "puntos_venta_emitidos",
        "puntos_venta_recibidos", "carga_minio", "carga_json", "timeout_mc",
        "proxy_request",
    ]
    assert "credentials" not in model.model_fields
    assert len(model.model_json_schema()["examples"]) == 1
    examples = get_openapi_examples("mis_comprobantes", "consulta")
    assert len(examples) == 1
    for example in examples.values():
        model.model_validate(example["value"])


def test_schema_document_has_operation_fields_and_openapi_example_format() -> None:
    document = get_schema_document("ccma", "consultar")
    parsed = BotSchemaDocument.model_validate(document)
    assert parsed.bot == "ccma"
    assert parsed.operation == "consultar"
    assert "cuit_representado" in parsed.request_schema["properties"]
    assert set(parsed.openapi_examples) == {"consulta_habitual"}
    assert all(
        {"summary", "description", "value"} <= set(example)
        for example in parsed.openapi_examples.values()
    )


def test_worker_publishes_schema_route_and_documents_examples_without_changing_job_envelope() -> None:
    settings = WorkerConfig(
        central_url="http://central-inaccesible.local",
        advertised_url="http://127.0.0.1:8080",
        worker_concurrency=1,
    )
    app = create_app(settings)
    client = TestClient(app)

    response = client.get("/internal/v1/bots/ccma/consultar/schema")
    assert response.status_code == 200
    document = response.json()
    assert document["bot"] == "ccma"
    assert document["operation"] == "consultar"
    assert "cuit_representado" in document["schema"]["properties"]
    assert "openapi_examples" in document

    openapi = app.openapi()
    operation = openapi["paths"]["/internal/v1/bots/{bot}/{operation}/schema"]["get"]
    examples = operation["responses"]["200"]["content"]["application/json"]["examples"]
    assert "ccma_consultar" in examples
    assert "BotSchemaDocument" in openapi["components"]["schemas"]

    assignment_route = openapi["paths"]["/internal/v1/jobs"]["post"]
    body_schema = assignment_route["requestBody"]["content"]["application/json"]["schema"]
    assert body_schema["$ref"].endswith("/JobEnvelope")
    assert "assignment_signature" in JobEnvelope.model_fields
