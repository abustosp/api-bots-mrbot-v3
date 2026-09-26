"""Autenticación por HTTPBasic y usuario + key en headers (V1)."""

from __future__ import annotations

import base64
import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.admin import users as admin_users  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402

RUTA = "/api/v3/usuarios/me"


@pytest.fixture
def cliente(monkeypatch):
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "secreto-auth-metodos")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    user_id = str(uuid.uuid4())
    admin_users.USERS[user_id] = admin_users.AdminUser(
        id=user_id, email="metodos@example.com", display_name="M", estado="habilitado"
    )
    admin_users.emitir_clave(user_id, [], "", "clave-metodos")
    otro = str(uuid.uuid4())
    admin_users.USERS[otro] = admin_users.AdminUser(
        id=otro, email="otro@example.com", display_name="O", estado="habilitado"
    )
    admin_users.emitir_clave(otro, [], "", "clave-otro")
    yield TestClient(create_app())
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    get_settings.cache_clear()


def _basic(usuario: str, clave: str) -> dict:
    token = base64.b64encode(f"{usuario}:{clave}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_basic_valido_e_invalido(cliente) -> None:
    assert cliente.get(RUTA, headers=_basic("metodos@example.com", "clave-metodos")).status_code == 200
    assert cliente.get(RUTA, headers=_basic("METODOS@example.com", "clave-metodos")).status_code == 200
    assert cliente.get(RUTA, headers=_basic("metodos@example.com", "mala")).status_code == 401
    # La clave de otro usuario no autentica con esta identidad.
    assert cliente.get(RUTA, headers=_basic("metodos@example.com", "clave-otro")).status_code == 401


def test_bearer_ya_no_se_acepta(cliente) -> None:
    token = ".".join(
        base64.urlsafe_b64encode(v.encode()).decode() for v in ("metodos@example.com", "clave-metodos")
    )
    assert cliente.get(RUTA, headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert cliente.post(
        "/api/v3/auth/token", json={"usuario": "metodos@example.com", "api_key": "clave-metodos"}
    ).status_code in (404, 405)


def test_headers_usuario_y_key_forma_v1(cliente) -> None:
    ok = {"email": "metodos@example.com", "X-API-Key": "clave-metodos"}
    assert cliente.get(RUTA, headers=ok).status_code == 200
    cruzado = {"email": "otro@example.com", "X-API-Key": "clave-metodos"}
    assert cliente.get(RUTA, headers=cruzado).status_code == 401
    # Compatibilidad V3: X-API-Key sola sigue aceptada.
    assert cliente.get(RUTA, headers={"X-API-Key": "clave-metodos"}).status_code == 200
    # email sin key nunca autentica.
    assert cliente.get(RUTA, headers={"email": "metodos@example.com"}).status_code == 401


def test_sin_credenciales_401(cliente) -> None:
    respuesta = cliente.get(RUTA)
    assert respuesta.status_code == 401


def test_openapi_documenta_los_metodos(cliente) -> None:
    esquema = cliente.get("/openapi.json").json()
    schemes = esquema["components"]["securitySchemes"]
    assert list(schemes) == ["HTTPBasic", "V1"]
    assert schemes["HTTPBasic"]["scheme"] == "basic"
    assert schemes["V1"]["name"] == "X-API-Key"
    assert "email" in schemes["V1"]["description"]
    operacion = esquema["paths"]["/api/v3/jobs/cola"]["get"]
    assert operacion["security"] == [{"HTTPBasic": []}, {"V1": []}]
    # email no aparece como parámetro suelto: se completa en Authorize (V1).
    assert all(p["name"] != "email" for p in operacion.get("parameters", []))


def test_swagger_publico_agrega_email_al_metodo_v1(cliente) -> None:
    html = cliente.get("/docs").text
    assert "requestInterceptor: (req) => window.__mrbotRequestInterceptor(req)" in html
    assert 'headers["email"] = email' in html
    assert "mrbot-v1-email" in html
