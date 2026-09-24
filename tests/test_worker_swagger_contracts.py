"""Contrato OpenAPI del plano privado central-worker."""

from __future__ import annotations

from bot_worker.config import WorkerConfig
from bot_worker.main import create_app


def _app():
    return create_app(
        WorkerConfig(
            central_url="http://central.invalid",
            advertised_url="http://127.0.0.1:8080",
        )
    )


def test_worker_documenta_envelope_firmado_y_cancelacion() -> None:
    schema = _app().openapi()

    assignment = schema["paths"]["/internal/v1/jobs"]["post"]
    assert assignment["requestBody"]["required"] is True
    assert assignment["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/JobEnvelope"
    }
    envelope = schema["components"]["schemas"]["JobEnvelope"]
    assert "Contrato interno central-worker" in envelope["description"]
    assert envelope["examples"][0]["protocol_version"] == 1
    assert envelope["examples"][0]["payload"]["representado_cuit"] == "20123456789"

    cancel = schema["paths"]["/internal/v1/jobs/{job_id}/cancel"]["post"]
    assert cancel["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/CancelIn"
    }
    cancel_schema = schema["components"]["schemas"]["CancelIn"]
    assert "La central firma" in cancel_schema["description"]
    assert cancel_schema["examples"][0]["reason"] == "cancelado_por_usuario"
