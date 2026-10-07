"""Regresión de seguridad: toda ruta /admin exige bearer ADMIN_TOKEN.

Única excepción: cascarones HTML estáticos que no contienen datos y piden el
token por fetch (login, panel y vista clásica de workers).
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from central_api.main import app

STATIC_SHELLS = {"/admin/", "/admin/login", "/admin/workers/panel"}


def test_admin_routes_reject_anonymous(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "token-de-prueba")
    cliente = TestClient(app, raise_server_exceptions=False)
    abiertas = []
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path.startswith("/admin") or path in STATIC_SHELLS:
            continue
        if path == "/admin/docs":  # HTML sin datos; el OpenAPI sí exige token
            continue
        for metodo in sorted((route.methods or set()) - {"HEAD", "OPTIONS"}):
            url = re.sub(r"\{[^}]*\}", "x", path)
            resp = cliente.request(metodo, url, json={} if metodo != "GET" else None)
            if resp.status_code not in (401, 403):
                abiertas.append((metodo, path, resp.status_code))
    assert not abiertas, f"rutas admin sin autenticación: {abiertas}"


def test_workers_panel_shell_leaks_no_data() -> None:
    cliente = TestClient(app)
    html = cliente.get("/admin/workers/panel").text
    assert "worker2.mrbot" not in html and "bot-worker:8080" not in html
