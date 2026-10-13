"""V2-compatible contract tests for public V3 job-status projections."""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api import jobs as jobs_api  # noqa: E402
from central_api.api.dependencies import JOB_META, project_job, require_api_principal  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.security.principals import ApiPrincipal  # noqa: E402
from central_api.store import JOBS, Job, new_job_id  # noqa: E402

USER_ID = "job-contract-owner"
V2_JOB_KEYS = {
    "job_id", "status", "result", "bot", "operation", "created_at",
    "started_at", "finished_at", "cancel_reason", "cancelled_by", "error",
    "files", "data",
}
V3_JOB_KEYS = {"status_detail", "error_detail"}


def _job(status: str, *, result=None, operation: str = "consulta") -> SimpleNamespace:
    return SimpleNamespace(
        id="0190f0c0-7f5b-7b2e-9f7e-123456789abc",
        bot="mis_comprobantes",
        operation=operation,
        status=status,
        created_at=datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc),
        started_at=None,
        finished_at=None,
        result=result,
    )


@pytest.mark.parametrize(
    ("actual", "legacy", "detail", "result"),
    [
        ("PENDIENTE", "PENDIENTE", "PENDIENTE", None),
        ("ASIGNADO", "PENDIENTE", "ASIGNADO", None),
        ("CORRIENDO", "CORRIENDO", "CORRIENDO", None),
        ("COMPLETO", "COMPLETO", "COMPLETO", "OK"),
        ("COMPLETO", "COMPLETO", "COMPLETO", "PARCIAL"),
        ("FALLIDO", "COMPLETO", "FALLIDO", "ERROR"),
        ("CANCELADO", "CANCELADO", "CANCELADO", None),
    ],
)
def test_project_job_preserves_v2_status_values_and_adds_v3_detail(
    actual: str, legacy: str, detail: str, result: str | None
) -> None:
    job_result = {"result": result} if result else None
    response = project_job(_job(actual, result=job_result), {"files": []})

    assert response["status"] == legacy
    assert response["status_detail"] == detail
    assert response["result"] == result
    assert V2_JOB_KEYS <= response.keys()
    assert V3_JOB_KEYS <= response.keys()
    assert response["files"] == []
    assert response["finished_at"] is None
    assert response["operation"] == "consultar"
    assert response["operation_legacy"] == "consulta"
    assert response["created_at"].endswith("Z")


def test_project_job_translates_errors_and_projects_legacy_and_v3_file_fields() -> None:
    response = project_job(
        _job(
            "FALLIDO",
            result={
                "result": "ERROR",
                "error": {
                    "error_code": "navigation",
                    "message": "Playwright failure at private.internal/path",
                    "cause": "internal exception detail",
                    "correlation_id": "corr-job-123",
                },
            },
        ),
        {
            "error": {
                "error_code": "navigation",
                "message": "Playwright failure at private.internal/path",
                "cause": "internal exception detail",
                "correlation_id": "corr-job-123",
            },
            "files": [
                {
                    "artifact_id": "artifact-123",
                    "filename": "reporte original.csv",
                    "content_type": "text/csv",
                    "size_bytes": 123,
                    "sha256": "a" * 64,
                    "download_url": "/api/v3/jobs/job/artifacts/artifact-123/download",
                    "url": "https://storage.example/reporte.csv?X-Amz-Expires=3600",
                    "url_expires_in": 3600,
                }
            ],
        },
    )

    assert response["status"] == "COMPLETO"
    assert response["status_detail"] == "FALLIDO"
    assert response["result"] == "ERROR"
    assert response["error"] == [
        "Verifique los datos de entrada e inténtelo nuevamente."
    ]
    assert response["error_detail"] == {
        "error_code": "navigation",
        "cause": "No se pudo acceder a la información solicitada.",
        "message": "Verifique los datos de entrada e inténtelo nuevamente.",
        "correlation_id": "corr-job-123",
    }
    serialized = str(response)
    assert "Playwright" not in serialized
    assert "private.internal" not in serialized
    assert "internal exception" not in serialized
    assert response["files"] == [
        {
            "name": "reporte original.csv",
            "url": "https://storage.example/reporte.csv?X-Amz-Expires=3600",
            "size": 123,
            "artifact_id": "artifact-123",
            "filename": "reporte original.csv",
            "content_type": "text/csv",
            "size_bytes": 123,
            "sha256": "a" * 64,
            "download_url": "/api/v3/jobs/job/artifacts/artifact-123/download",
            "url_expires_in": 3600,
        }
    ]


def test_project_job_does_not_expose_finish_time_while_nonterminal() -> None:
    response = project_job(
        _job("CORRIENDO"),
        {"files": [], "finished_at": "2026-10-12T18:00:00Z"},
    )

    assert response["finished_at"] is None


def test_file_expiry_is_null_when_direct_url_is_unavailable() -> None:
    response = project_job(
        _job("COMPLETO", result={"result": "OK"}),
        {
            "files": [{
                "name": "resultado.csv",
                "url": None,
                "size": 12,
                "download_url": "/api/v3/jobs/job/artifacts/artifact/download",
            }],
            "data": {},
        },
    )

    assert response["files"][0]["name"] == "resultado.csv"
    assert response["files"][0]["url"] == ""
    assert response["files"][0]["url_expires_in"] is None


def test_get_batch_and_list_share_the_compatibility_projection(monkeypatch) -> None:
    old_jobs, old_meta = dict(JOBS), dict(JOB_META)
    JOBS.clear()
    JOB_META.clear()
    monkeypatch.setattr(jobs_api, "db_configurado", lambda: False)
    failed_id = new_job_id()
    complete_id = new_job_id()
    JOBS[failed_id] = Job(
        id=failed_id,
        bot="mis_comprobantes",
        operation="consulta",
        payload={},
        status="FALLIDO",
        result={"result": "ERROR", "error": {"error_code": "authentication"}},
        created_at=datetime(2026, 10, 12, 18, 1, tzinfo=timezone.utc),
    )
    JOB_META[failed_id] = {"owner": USER_ID, "files": [], "error": {"error_code": "authentication"}}
    JOBS[complete_id] = Job(
        id=complete_id,
        bot="mis_comprobantes",
        operation="consultar",
        payload={},
        status="COMPLETO",
        result={"result": "OK"},
        created_at=datetime(2026, 10, 12, 18, 2, tzinfo=timezone.utc),
    )
    JOB_META[complete_id] = {"owner": USER_ID, "files": [], "data": ["fila-1"]}

    app = create_app()
    app.dependency_overrides[require_api_principal] = lambda: ApiPrincipal(
        user_id=USER_ID, key_id="job-contract-test"
    )
    try:
        client = TestClient(app)
        get_response = client.get(f"/api/v3/jobs/{failed_id}")
        batch_response = client.post(
            "/api/v3/jobs/estado:lote",
            json={"job_ids": [failed_id, complete_id, "missing-job"]},
        )
        list_response = client.get("/api/v3/jobs?limit=10")
        alias_response = client.get(
            f"/api/v3/mis_comprobantes/consulta/{failed_id}"
        )
    finally:
        app.dependency_overrides.clear()
        JOBS.clear()
        JOBS.update(old_jobs)
        JOB_META.clear()
        JOB_META.update(old_meta)

    assert get_response.status_code == 200
    failed = get_response.json()
    assert V2_JOB_KEYS <= failed.keys()
    assert V3_JOB_KEYS <= failed.keys()
    assert failed["status"] == "COMPLETO"
    assert failed["status_detail"] == "FALLIDO"
    assert failed["result"] == "ERROR"
    assert failed["operation"] == "consultar"
    assert failed["operation_legacy"] == "consulta"
    assert failed["finished_at"] is None
    assert batch_response.status_code == 200
    batch_by_id = {item["job_id"]: item for item in batch_response.json()["items"]}
    assert batch_by_id[failed_id]["status"] == "COMPLETO"
    assert batch_by_id[failed_id]["status_detail"] == "FALLIDO"
    assert batch_by_id[complete_id]["data"] == ["fila-1"]
    assert batch_by_id["missing-job"]["error"] == "not_found"
    assert batch_by_id["missing-job"]["error_detail"]["error_code"] == "not_found"
    assert list_response.status_code == 200
    listed = {item["job_id"]: item for item in list_response.json()["items"]}
    assert listed[failed_id]["status"] == "COMPLETO"
    assert listed[failed_id]["result"] == "ERROR"
    assert listed[complete_id]["data"] == ["fila-1"]
    assert alias_response.status_code == 200
    assert alias_response.json()["operation"] == "consultar"


def test_openapi_documents_legacy_file_fields_states_and_idempotency_difference() -> None:
    openapi = create_app().openapi()
    schemas = openapi["components"]["schemas"]
    status_schema = schemas["JobStatusResponse"]
    artifact_schema = schemas["JobArtifactResponse"]

    assert status_schema["properties"]["created_at"]["format"] == "date-time"
    assert status_schema["properties"]["data"]
    assert {"status_detail", "error_detail", "operation_legacy"} <= set(
        status_schema["properties"]
    )
    assert {"BatchStatusResponse", "BatchJobNotFoundResponse", "JobListResponse",
            "JobListSummary", "JobQueueResponse", "JobQueueItem"} <= set(schemas)
    assert {"name", "url", "size", "artifact_id", "filename", "content_type",
            "size_bytes", "sha256", "download_url", "url_expires_in"} <= set(
        artifact_schema["properties"]
    )
    assert "3600" in artifact_schema["properties"]["url_expires_in"]["description"]

    get_examples = openapi["paths"]["/api/v3/jobs/{job_id}"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]
    assert {"pendiente", "asignado", "corriendo", "completo_ok", "completo_parcial", "completo_error", "cancelado"} <= set(get_examples)
    batch_examples = openapi["paths"]["/api/v3/jobs/estado:lote"]["post"]["responses"]["200"]["content"]["application/json"]["examples"]
    list_examples = openapi["paths"]["/api/v3/jobs"]["get"]["responses"]["200"]["content"]["application/json"]["examples"]
    assert set(get_examples) <= set(batch_examples)
    assert set(get_examples) <= set(list_examples)
    assert "409" in openapi["paths"]["/api/v3/bots/{bot}/{operacion}"]["post"]["responses"]
    assert "idempotency_conflict" in str(
        openapi["paths"]["/api/v3/bots/{bot}/{operacion}"]["post"]["responses"]["409"]
    )
    assert "409" in openapi["paths"]["/api/v3/mis_comprobantes/consulta"]["post"]["responses"]
    batch_response_schema = openapi["paths"]["/api/v3/jobs/estado:lote"]["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    list_response_schema = openapi["paths"]["/api/v3/jobs"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    queue_response_schema = openapi["paths"]["/api/v3/jobs/cola"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert batch_response_schema["$ref"].endswith("/BatchStatusResponse")
    assert list_response_schema["$ref"].endswith("/JobListResponse")
    assert queue_response_schema["$ref"].endswith("/JobQueueResponse")
