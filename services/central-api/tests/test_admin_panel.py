"""Regresión del panel web administrativo V3 inspirado en la V2."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


def test_panel_admin_renderiza_navegacion_y_vistas() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/html")
    cuerpo = respuesta.text
    for vista in ("dashboard", "users", "jobs", "executions", "fleet", "audit"):
        assert f'data-view="{vista}"' in cuerpo
        assert f'data-panel="{vista}"' in cuerpo
    for endpoint in (
        "/admin/users",
        "/admin/jobs",
        "/admin/records",
        "/admin/tables",
        "/admin/jobs/metrics",
        "/admin/fleet",
        "/admin/audit",
    ):
        assert endpoint in cuerpo
    assert "sessionStorage" in cuerpo
    assert "mrbot_admin_token" in cuerpo
    assert "valor_unica_vez" in cuerpo
    assert "credential-detail" in cuerpo
    assert "Artefactos MinIO" in cuerpo
    assert "Asignado" in cuerpo
    assert "Finalizado" in cuerpo
    assert "Tablas por bot" in cuerpo
    assert "Request y response por cada bot" in cuerpo
    assert "bot-sections" in cuerpo
    assert "Ver request" in cuerpo
    assert "Ver response" in cuerpo
    assert "executions-table-select" in cuerpo
    assert "executions-previous" in cuerpo
    assert "/admin/table-catalog" in cuerpo
    assert "Solo se carga la selección actual" in cuerpo
    assert "renderPhysicalBotSection" in cuerpo
    assert "request_payload" in cuerpo
    assert "response_payload" in cuerpo


def test_panel_tables_es_alias_v2_y_abre_registros() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/tables")

    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/html")
    assert "Tablas / registros" in respuesta.text
    assert 'window.location.pathname.endsWith("/tables")' in respuesta.text


def test_panel_login_es_alias_html_y_admin_queda_fuera_de_openapi() -> None:
    cliente = TestClient(create_app())

    login = cliente.get("/admin/login")
    assert login.status_code == 200
    assert "Administración V3" in login.text

    esquema = cliente.get("/openapi.json").json()
    assert not any(path.startswith("/admin") for path in esquema["paths"])


def test_panel_no_reemplaza_la_autorizacion_json(monkeypatch) -> None:
    """Los endpoints JSON siguen exigiendo Bearer aunque exista el panel."""
    monkeypatch.setenv("ADMIN_TOKEN", "test-admin-token")
    get_settings.cache_clear()
    try:
        cliente = TestClient(create_app())

        respuesta = cliente.get("/admin/users")
        assert respuesta.status_code == 401
    finally:
        get_settings.cache_clear()
