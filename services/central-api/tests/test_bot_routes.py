"""Contrato de rutas V1 generadas para cada bot/operación."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient
from fastapi.routing import APIRoute

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api.bot_compat import BOT_ROUTE_ALIASES  # noqa: E402
from central_api.api.bots import CATALOGUE  # noqa: E402
from central_api.api.bot_routes import bot_routers  # noqa: E402
from central_api.api.dependencies import require_api_principal  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.security.principals import ApiPrincipal  # noqa: E402


def _client(user_id: str = "route-user") -> TestClient:
    from central_api.billing.entitlements import set_billing_account

    set_billing_account(user_id, "TESTING")
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
    runtime_routes = {
        (route.path, method): route
        for bot_router in bot_routers
        for route in bot_router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    hidden_aliases = {
        "/arba/consulta",
        "/vep_archivo/carga",
    }
    hidden_canonical_paths = {
        "/comprobantes/consultar",
        "/comprobantes/solicitar",
        "/comprobantes/historial",
    }

    assert len(BOT_ROUTE_ALIASES) == 39
    assert len(bot_routers) >= len({path.rpartition("/")[0] for path, _, _ in BOT_ROUTE_ALIASES})
    for route_path, bot, _operation in BOT_ROUTE_ALIASES:
        create_path = f"/api/v3{route_path}"
        status_path = f"{create_path}/{{job_id}}"
        cancel_path = f"{create_path}/cancelar/{{job_id}}"
        hidden = route_path in hidden_aliases
        runtime_create_path = route_path
        runtime_status_path = f"{route_path}/{{job_id}}"
        runtime_cancel_path = f"{route_path}/cancelar/{{job_id}}"
        assert (runtime_create_path, "POST") in runtime_routes
        assert (runtime_status_path, "GET") in runtime_routes
        assert (runtime_cancel_path, "POST") in runtime_routes
        assert runtime_routes[(runtime_create_path, "POST")].include_in_schema is not hidden
        assert runtime_routes[(runtime_status_path, "GET")].include_in_schema is not hidden
        assert runtime_routes[(runtime_cancel_path, "POST")].include_in_schema is not hidden
        if hidden:
            assert create_path not in paths
            assert status_path not in paths
            assert cancel_path not in paths
            continue
        assert create_path in paths
        assert status_path in paths
        assert cancel_path in paths
        assert paths[create_path]["post"]["tags"] == [bot]
        assert paths[create_path]["post"]["requestBody"]["required"] is True
        assert paths[create_path]["post"]["requestBody"]["content"]["application/json"]["examples"]

    for route_path in hidden_canonical_paths:
        create_path = f"/api/v3{route_path}"
        status_path = f"{create_path}/{{job_id}}"
        cancel_path = f"{create_path}/cancelar/{{job_id}}"
        assert create_path not in paths
        assert status_path not in paths
        assert cancel_path not in paths
        assert (route_path, "POST") in runtime_routes
        assert (f"{route_path}/{{job_id}}", "GET") in runtime_routes
        assert (f"{route_path}/cancelar/{{job_id}}", "POST") in runtime_routes
        assert runtime_routes[(route_path, "POST")].include_in_schema is False
        assert runtime_routes[(f"{route_path}/{{job_id}}", "GET")].include_in_schema is False
        assert runtime_routes[(f"{route_path}/cancelar/{{job_id}}", "POST")].include_in_schema is False

    for route_path in (
        "/api/v3/apoc/consultar",
        "/api/v3/consulta_cuit/consulta",
        "/api/v3/libros_portal_iva/descargar_libros",
        "/api/v3/portal_iva/importar",
        "/api/v3/portal_iva/gestionar",
        "/api/v3/retenciones_percepciones_iibb/arba/consulta",
        "/api/v3/vep/carga",
    ):
        assert route_path in paths


def test_openapi_publishes_each_canonical_bot_operation_once() -> None:
    schema = create_app().openapi()
    deprecated_aliases = {
        item["bot"]: item["alias_de"]
        for item in CATALOGUE
        if item.get("deprecated") and item.get("alias_de")
    }
    published: dict[tuple[str, str], str] = {}

    for path, path_item in schema["paths"].items():
        create = path_item.get("post")
        if create is None or not create.get("summary", "").startswith("Crear ejecución de "):
            continue
        operation, bot = create["summary"].removeprefix("Crear ejecución de ").rsplit(" para ", 1)
        canonical_bot = deprecated_aliases.get(bot, bot)
        capability = (canonical_bot, operation)
        assert capability not in published, (
            f"operación duplicada {capability}: {published.get(capability)} y {path}"
        )
        published[capability] = path
        assert f"{path}/{{job_id}}" in schema["paths"]
        assert f"{path}/cancelar/{{job_id}}" in schema["paths"]


def test_catalogue_marks_comprobantes_as_deprecated_alias() -> None:
    response = _client().get("/api/v3/bots")
    assert response.status_code == 200
    comprobantes = next(
        item for item in response.json()["bots"] if item["bot"] == "comprobantes"
    )
    assert comprobantes["deprecated"] is True
    assert comprobantes["alias_de"] == "mis_comprobantes"
    assert {item["bot"] for item in response.json()["bots"]} >= {
        "comprobantes",
        "mis_comprobantes",
    }
    assert "/api/v3/comprobantes/consultar" not in create_app().openapi()["paths"]


def test_create_returns_v1_status_cancel_links_and_scopes_job_to_operation() -> None:
    client = _client()
    created = client.post(
        "/api/v3/ccma/consulta",
        headers={"Idempotency-Key": "bot-route-links-test"},
        json={
            "cuit_representante": "20123456786",
            "clave_representante": "route-test-secret",
            "cuit_representado": "20123456786",
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
