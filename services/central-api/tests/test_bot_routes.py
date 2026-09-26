"""Contrato de rutas V1 generadas para cada bot/operación."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api.bot_compat import BOT_ROUTE_ALIASES  # noqa: E402
from central_api.api.bot_routes import bot_routers  # noqa: E402
from central_api.api.dependencies import require_api_principal  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.security.principals import ApiPrincipal  # noqa: E402


def _client(user_id: str = "route-user") -> TestClient:
    app = create_app()
    app.dependency_overrides[require_api_principal] = lambda: ApiPrincipal(
        user_id=user_id,
        key_id="route-test-key",
    )
    return TestClient(app)


def test_generated_bot_routers_expose_create_status_cancel_triplets() -> None:
    app = create_app()
    schema = app.openapi()
    paths = schema["paths"]

    assert len(BOT_ROUTE_ALIASES) == 36
    assert len(bot_routers) >= len({path.rpartition("/")[0] for path, _, _ in BOT_ROUTE_ALIASES})
    for route_path, bot, _operation in BOT_ROUTE_ALIASES:
        create_path = f"/api/v3{route_path}"
        assert create_path in paths
        assert f"/api/v3{route_path}/{{job_id}}" in paths
        assert f"/api/v3{route_path}/cancelar/{{job_id}}" in paths
        assert paths[create_path]["post"]["tags"] == [bot]
        assert paths[create_path]["post"]["requestBody"]["required"] is True
        assert paths[create_path]["post"]["requestBody"]["content"]["application/json"]["examples"]


def test_create_returns_v1_status_cancel_links_and_scopes_job_to_operation() -> None:
    client = _client()
    created = client.post(
        "/api/v3/ccma/consulta",
        headers={"Idempotency-Key": "bot-route-links-test"},
        json={
            "cuit_representante": "20123456789",
            "clave_representante": "route-test-secret",
            "cuit_representado": "20123456789",
        },
    )

    assert created.status_code == 202
    body = created.json()
    job_id = body["job_id"]
    assert body["success"] is True
    assert body["links"] == {
        "job": f"/api/v3/jobs/{job_id}",
        "status": f"/api/v3/ccma/consulta/{job_id}",
        "cancel": f"/api/v3/ccma/consulta/cancelar/{job_id}",
    }

    status = client.get(body["links"]["status"])
    assert status.status_code == 200
    assert status.json()["job_id"] == job_id
    assert status.json()["operation"] == "consultar"

    # A job ID visible to the caller is still hidden from another bot/operation
    # alias, preventing the aliases from becoming an ownership oracle.
    wrong_operation = client.get(f"/api/v3/siper/consulta/{job_id}")
    wrong_cancel = client.post(f"/api/v3/siper/consulta/cancelar/{job_id}")
    assert wrong_operation.status_code == 404
    assert wrong_cancel.status_code == 404

    cancelled = client.post(body["links"]["cancel"])
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "CANCELADO"
