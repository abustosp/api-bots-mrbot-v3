"""El listado hidrata una página completa con consultas acotadas."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from central_api import datos_archivo, mc_json
from central_api.api import jobs
from central_api.api.dependencies import require_api_principal
from central_api.main import create_app
from central_api.security.principals import ApiPrincipal


def test_list_hydrates_only_page_in_three_queries(monkeypatch):
    owner = uuid4()
    ids = [UUID(f"00000000-0000-7000-8000-{n:012d}") for n in (3, 2, 1)]
    now = datetime.now(timezone.utc)
    rows = [SimpleNamespace(
        id=job_id, status="COMPLETO", bot="ccma", operation="consultar",
        created_at=now, started_at=None, finished_at=now, error_message=None,
        request_payload={}, cancel_reason=None, cancelled_by=None,
    ) for job_id in ids]
    results = [SimpleNamespace(job_id=ids[0], result="OK", payload={"valor": 1}, summary={})]
    artifacts = [SimpleNamespace(
        job_id=ids[0], id=uuid4(), filename="salida.csv", size_bytes=10,
        content_type="text/csv", sha256=None, object_key=None, expires_at=None,
    ), SimpleNamespace(
        job_id=ids[1], id=uuid4(), filename="vencido.csv", size_bytes=10,
        content_type="text/csv", sha256=None, object_key=None,
        expires_at=now - timedelta(seconds=1),
    )]

    class Session:
        def __init__(self):
            self.responses = iter((rows, results, artifacts))
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: next(self.responses)))

    session = Session()

    @asynccontextmanager
    async def nueva_sesion():
        yield session

    async def reconstruir(**kwargs):
        return kwargs["datos"]

    monkeypatch.setattr(jobs, "db_configurado", lambda: True)
    monkeypatch.setattr(jobs, "nueva_sesion", nueva_sesion)
    monkeypatch.setattr(mc_json, "reconstruir", reconstruir)
    monkeypatch.setattr(datos_archivo, "reconstruir", reconstruir)
    app = create_app()
    app.dependency_overrides[require_api_principal] = lambda: ApiPrincipal(user_id=str(owner), key_id="test")

    response = TestClient(app).get("/api/v3/jobs?limit=2&bot=ccma")

    assert response.status_code == 200
    body = response.json()
    assert [item["job_id"] for item in body["items"]] == list(map(str, ids[:2]))
    assert body["has_more"] and jobs._decode_cursor(body["next_cursor"]) == str(ids[1])
    assert body["items"][0]["data"] == {"schema_version": 1, "valor": 1}
    assert [item["filename"] for item in body["items"][0]["files"]] == ["salida.csv"]
    assert body["items"][1]["files"] == []
    assert len(session.statements) == 3
    assert owner in session.statements[0].compile().params.values()
    for statement in session.statements[1:]:
        assert ids[:2] in statement.compile().params.values()
