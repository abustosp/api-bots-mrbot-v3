"""Queue positions, filtering, and cancellation across memory/PostgreSQL."""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api import jobs as jobs_api  # noqa: E402
from central_api.api.dependencies import JOB_META, require_api_principal  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.security.principals import ApiPrincipal  # noqa: E402
from central_api.store import JOBS, Job  # noqa: E402


def _client(user_id: str) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_api_principal] = lambda: ApiPrincipal(
        user_id=user_id,
        key_id="jobs-test-key",
    )
    return TestClient(app)


def _memory_job(job_id: str, user_id: str, status: str, created_at: datetime) -> Job:
    job = Job(
        id=job_id,
        bot="ccma",
        operation="consultar",
        payload={},
        status=status,
        created_at=created_at,
    )
    JOBS[job_id] = job
    JOB_META[job_id] = {"owner": user_id}
    return job


def test_queue_lists_only_users_live_jobs_with_filtered_global_position() -> None:
    user_id = "queue-owner"
    jobs_before = dict(JOBS)
    meta_before = dict(JOB_META)
    JOBS.clear()
    JOB_META.clear()
    now = datetime.now(timezone.utc)
    foreign_id, own_id, active_id = str(uuid4()), str(uuid4()), str(uuid4())
    foreign = _memory_job(foreign_id, "other-user", "PENDIENTE", now)
    own = _memory_job(own_id, user_id, "PENDIENTE", now + timedelta(seconds=1))
    active = _memory_job(active_id, user_id, "CORRIENDO", now + timedelta(seconds=2))
    try:
        client = _client(user_id)
        response = client.get("/api/v3/jobs/cola?bot=ccma")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 2
        items = {item["job_id"]: item for item in body["jobs"]}
        assert set(items) == {own.id, active.id}
        assert items[own.id]["status"] == "PENDIENTE"
        assert items[own.id]["position"] == 2
        assert items[own.id]["queue_position"] == 2
        assert items[active.id]["position"] is None

        only_pending = client.get("/api/v3/jobs/cola?status=PENDIENTE")
        assert [item["job_id"] for item in only_pending.json()["jobs"]] == [own.id]
        terminal = client.get("/api/v3/jobs/cola?status=COMPLETO")
        assert terminal.json()["jobs"] == []
    finally:
        JOBS.clear()
        JOBS.update(jobs_before)
        JOB_META.clear()
        JOB_META.update(meta_before)


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def all(self):
        return self.value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, results):
        self.results = iter(results)
        self.calls = 0

    async def execute(self, _statement):
        self.calls += 1
        return _FakeResult(next(self.results))


@asynccontextmanager
async def _session_for(fake_session):
    yield fake_session


def test_queue_reads_pending_positions_and_own_rows_from_postgresql(monkeypatch) -> None:
    user_id = UUID("00000000-0000-4000-8000-000000000111")
    owner_id, other_id = uuid4(), uuid4()
    own_row = SimpleNamespace(
        id=owner_id,
        status="PENDIENTE",
        bot="ccma",
        operation="consultar",
        priority=100,
        created_at=datetime.now(timezone.utc),
    )
    fake_session = _FakeSession([[other_id, owner_id], [own_row]])
    monkeypatch.setattr(jobs_api, "db_configurado", lambda: True)
    monkeypatch.setattr(
        jobs_api,
        "nueva_sesion",
        lambda: _session_for(fake_session),
    )

    response = _client(str(user_id)).get("/api/v3/jobs/cola?bot=ccma")

    assert response.status_code == 200
    assert response.json()["jobs"] == [
        {
            "job_id": str(owner_id),
            "status": "PENDIENTE",
            "bot": "ccma",
            "operation": "consultar",
            "operacion": "consultar",
            "position": 2,
            "queue_position": 2,
        }
    ]
    assert fake_session.calls == 2


def test_delete_cancels_postgresql_job_by_id_and_persists_state(monkeypatch) -> None:
    user_id = UUID("00000000-0000-4000-8000-000000000112")
    job_id = uuid4()
    row = SimpleNamespace(
        id=job_id,
        user_id=user_id,
        status="PENDIENTE",
        result=None,
        bot="ccma",
        operation="consultar",
        created_at=datetime.now(timezone.utc),
        started_at=None,
        finished_at=None,
        cancel_reason=None,
        cancelled_by=None,
        error_message=None,
    )
    fake_session = _FakeSession([row])
    monkeypatch.setattr(jobs_api, "db_configurado", lambda: True)
    monkeypatch.setattr(
        jobs_api,
        "nueva_sesion",
        lambda: _session_for(fake_session),
    )

    response = _client(str(user_id)).request(
        "DELETE",
        f"/api/v3/jobs/{job_id}",
        json={"motivo": "no hace falta"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELADO"
    assert response.json()["cancel_reason"] == "no hace falta"
    assert response.json()["cancelled_by"] == "USER"
    assert row.status == "CANCELADO"
    assert row.finished_at is not None
    assert fake_session.calls == 1
