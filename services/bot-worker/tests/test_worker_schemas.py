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


def test_worker_schema_models_are_local_and_have_flat_credentials_and_examples() -> None:
    schema_source = (WORKER_SRC / "bot_worker" / "schemas" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "from central_api" not in schema_source
    assert "import central_api" not in schema_source

    model = get_request_model("mis_comprobantes", "consultar")
    assert "cuit_representante" in model.model_fields
    assert "cuit_login" in model.model_fields
    assert "clave" in model.model_fields
    assert "credentials" not in model.model_fields
    assert len(model.model_json_schema()["examples"]) == 3
    assert len(get_openapi_examples("mis_comprobantes", "consultar")) == 3
    for example in get_openapi_examples("mis_comprobantes", "consultar").values():
        model.model_validate(example["value"])


def test_schema_document_is_secret_safe_and_has_openapi_example_format() -> None:
    document = get_schema_document("ccma", "consultar")
    parsed = BotSchemaDocument.model_validate(document)
    assert parsed.bot == "ccma"
    assert parsed.operation == "consultar"
    assert "cuit_representante" in parsed.request_schema["properties"]
    assert set(parsed.openapi_examples) == {
        "consulta_habitual",
        "rango_y_opciones",
        "clave_cifrada",
    }
    assert all(
        {"summary", "description", "value"} <= set(example)
        for example in parsed.openapi_examples.values()
    )
    encrypted = parsed.openapi_examples["clave_cifrada"]["value"]
    assert encrypted["clave_encriptada"] == "BASE64_RSA_OAEP_CIPHERTEXT_DEMO"
    assert "clave" not in encrypted


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
    assert "representado_cuit" in document["schema"]["properties"]
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
