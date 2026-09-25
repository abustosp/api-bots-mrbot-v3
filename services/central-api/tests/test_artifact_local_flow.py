from __future__ import annotations

import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api import dependencies as api_dependencies  # noqa: E402
from central_api.api import jobs as jobs_api  # noqa: E402
from central_api.api import uploads as uploads_api  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.security.principals import ApiPrincipal  # noqa: E402
from central_api.storage import presign_get_url, presign_put_url  # noqa: E402


USER_ID = str(uuid.uuid4())
PRINCIPAL = ApiPrincipal(user_id=USER_ID, key_id="test-key")


def test_upload_route_emite_put_sigv4_con_content_type(monkeypatch):
    app = create_app()
    app.dependency_overrides[api_dependencies.require_api_principal] = lambda: PRINCIPAL
    monkeypatch.setattr(
        uploads_api,
        "get_settings",
        lambda: SimpleNamespace(
            object_storage_endpoint="http://127.0.0.1:9000",
            object_storage_region="us-east-1",
            object_storage_bucket="mrbot",
            object_storage_access_key="test-access",
            object_storage_secret_key="not-a-real-secret",
        ),
    )
    try:
        response = TestClient(app).post(
            "/api/v3/uploads",
            json={
                "bot": "apoc",
                "operacion": "consultar",
                "campo": "entrada",
                "filename": "entrada.txt",
                "content_type": "text/plain",
                "size_bytes": 7,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 201
    body = response.json()
    assert body["object_key"].startswith("uploads/")
    assert body["required_headers"] == {"Content-Type": "text/plain"}
    assert "X-Amz-Signature=" in body["upload_url"]
    assert "X-Amz-SignedHeaders=content-type%3Bhost" in body["upload_url"]
    assert "not-a-real-secret" not in body["upload_url"]


def test_presigned_get_y_put_tienen_firmas_distintas():
    common = dict(
        endpoint="http://127.0.0.1:9000",
        region="us-east-1",
        bucket="mrbot",
        access_key="test-access",
        secret_key="not-a-real-secret",
        object_key="jobs/one/1/out.txt",
        ahora=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    put_url = presign_put_url(**common)
    get_url = presign_get_url(**common)
    assert put_url != get_url
    assert "/mrbot/jobs/one/1/out.txt?" in get_url
    assert "X-Amz-Signature=" in get_url


def test_get_job_recupera_job_persistido_fuera_de_memoria(monkeypatch):
    app = create_app()
    app.dependency_overrides[api_dependencies.require_api_principal] = lambda: PRINCIPAL
    job_id = str(uuid.uuid4())
    artifact_id = uuid.uuid4()
    persisted = SimpleNamespace(
        id=job_id,
        status="COMPLETO",
        result="OK",
        bot="apoc",
        operation="consultar",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        started_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    persisted_result = SimpleNamespace(
        result="OK",
        payload={"resumen": "persistido"},
        summary={"error": {}},
    )
    persisted_artifact = SimpleNamespace(
        id=artifact_id,
        filename="comprobantes.csv",
        content_type="text/csv",
        size_bytes=123,
        sha256="a" * 64,
        expires_at=None,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    monkeypatch.setattr(jobs_api, "db_configurado", lambda: True)

    class FakeResult:
        def __init__(self, scalar=None, items=None):
            self._scalar = scalar
            self._items = items or []

        def scalar_one_or_none(self):
            return self._scalar

        def scalars(self):
            return self

        def all(self):
            return self._items

    class FakeSession:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return FakeResult(scalar=persisted_result)
            return FakeResult(items=[persisted_artifact])

    class FakeRepository:
        def __init__(self, session):
            pass

        async def get_scoped(self, requested_id, requested_user):
            assert requested_id == uuid.UUID(job_id)
            assert requested_user == uuid.UUID(USER_ID)
            return persisted

    monkeypatch.setattr(jobs_api, "nueva_sesion", lambda: FakeSession())
    monkeypatch.setattr(
        "central_api.repositories.jobs.JobRepository", FakeRepository
    )
    try:
        response = TestClient(app).get(f"/api/v3/jobs/{job_id}")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["job_id"] == job_id
    assert response.json()["status"] == "COMPLETO"
    assert response.json()["result"] == "OK"
    assert response.json()["data"]["resumen"] == "persistido"
    assert response.json()["files"][0]["artifact_id"] == str(artifact_id)
    assert response.json()["files"][0]["download_url"].endswith(
        f"/artifacts/{artifact_id}/download"
    )


def test_download_artifact_solo_firma_tras_scoping_del_job(monkeypatch):
    job_id, artifact_id = uuid.uuid4(), uuid.uuid4()
    object_key = f"jobs/{job_id}/1/output.pdf"
    artifact = SimpleNamespace(object_key=object_key, expires_at=None)

    class FakeResult:
        def scalar_one_or_none(self):
            return artifact

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            return FakeResult()

    class FakeRepository:
        def __init__(self, session):
            pass

        async def get_scoped(self, requested_job, requested_user):
            assert requested_job == job_id
            assert requested_user == uuid.UUID(USER_ID)
            return object()

    monkeypatch.setattr("central_api.db.nueva_sesion", lambda: FakeSession())
    monkeypatch.setattr(
        "central_api.repositories.jobs.JobRepository", FakeRepository
    )
    monkeypatch.setattr(
        uploads_api,
        "get_settings",
        lambda: SimpleNamespace(
            object_storage_endpoint="http://127.0.0.1:9000",
            object_storage_public_endpoint="http://127.0.0.1:9000",
            object_storage_region="us-east-1",
            object_storage_bucket="mrbot",
            object_storage_access_key="test-access",
            object_storage_secret_key="not-a-real-secret",
        ),
    )

    response = asyncio.run(
        uploads_api.download_artifact(str(job_id), str(artifact_id), PRINCIPAL)
    )
    assert response.status_code == 307
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["location"].startswith("http://127.0.0.1:9000/")
    assert object_key in response.headers["location"]
    assert "X-Amz-Signature=" in response.headers["location"]
