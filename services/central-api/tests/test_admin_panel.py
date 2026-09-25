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
    for vista in ("dashboard", "users", "keys", "jobs", "executions", "fleet", "audit"):
        assert f'data-view="{vista}"' in cuerpo
        assert f'data-panel="{vista}"' in cuerpo
    for endpoint in (
        "/admin/users",
        "/admin/api-keys",
        "/admin/jobs",
        "/admin/records",
        "/admin/jobs/metrics",
        "/admin/fleet",
        "/admin/audit",
    ):
        assert endpoint in cuerpo
    assert "sessionStorage" in cuerpo
    assert "mrbot_admin_token" in cuerpo
    assert "valor_unica_vez" in cuerpo
    assert "credential-detail" in cuerpo
    assert "Archivos" in cuerpo
    assert "Asignado" in cuerpo
    assert "Finalizado" in cuerpo
    assert "bot-sections" in cuerpo
    assert "flattenRequest" in cuerpo
    assert "Request · ${field}" in cuerpo
    assert "Response JSON" in cuerpo
    assert "artifactListView" in cuerpo
    assert "executions-table-select" in cuerpo
    assert "/admin/table-catalog" in cuerpo
    assert "Solo se carga la selección actual" in cuerpo
    assert "renderPhysicalBotSection" in cuerpo
    assert "request_payload" in cuerpo
    assert "response_payload" in cuerpo
    inicio = cuerpo.index('data-panel="executions"')
    fin = cuerpo.index('data-panel="fleet"', inicio)
    explorer = cuerpo[inicio:fin]
    assert 'class="form-actions"' not in explorer
    assert 'id="execution-table-counts"' not in explorer
    assert 'id="table-description"' not in explorer


def test_panel_sin_abrir_tablas_y_con_buscador_de_tablas() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")

    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert "Abrir tablas" not in cuerpo
    assert "Tablas / registros" in cuerpo
    assert "executions-table-search" in cuerpo
    assert "filterTableOptions" in cuerpo

    assert cliente.get("/admin/tables").status_code == 404


def test_panel_selector_tablas_es_combobox_accesible_con_teclado() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")

    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert 'id="executions-table-search" type="text" role="combobox"' in cuerpo
    assert 'aria-autocomplete="list"' in cuerpo
    assert 'aria-controls="executions-table-listbox"' in cuerpo
    assert 'aria-describedby="executions-table-help"' in cuerpo
    assert 'id="executions-table-listbox"' in cuerpo
    assert 'role="listbox"' in cuerpo
    assert 'role="option"' in cuerpo
    assert 'id="executions-table-select" class="visually-hidden"' in cuerpo
    for tecla in ('"ArrowDown"', '"ArrowUp"', '"Enter"', '"Escape"'):
        assert tecla in cuerpo
    assert 'addEventListener("input"' in cuerpo
    assert 'select.dispatchEvent(new Event("change", { bubbles: true }))' in cuerpo
    assert "No hay tablas que coincidan." in cuerpo

    # La selección continúa en la vista integrada y no introduce ruta alternativa.
    assert 'data-view="executions">Tablas / registros</button>' in cuerpo
    assert "/admin/records?" in cuerpo
    assert 'data-panel="executions"' in cuerpo
    assert cliente.get("/admin/tables").status_code == 404


def test_panel_explorador_conserva_detalle_de_tablas_legacy_y_fisicas() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")

    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert "Tablas V1/V2 por bot (consulta_*_logs)" in cuerpo
    assert "renderBotSections" in cuerpo
    assert "renderPhysicalBotSection" in cuerpo
    assert '"response_payload", "response_data", "artifact_metadata"' in cuerpo
    assert '"status"' in cuerpo
    assert "Request · ${field}" in cuerpo
    assert "Response JSON" in cuerpo
    assert "Archivos" in cuerpo


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
