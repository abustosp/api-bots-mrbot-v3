"""Integración real: acciones de clave del panel persisten en PostgreSQL.

Requiere CENTRAL_API_TEST_DATABASE_URL (PostgreSQL migrado a head). Sin ella se
omite. Verifica por SQL y por autenticación del cliente que revocar, restaurar
y editar no quedan solo en memoria, y que las claves persistidas (no presentes
en el cache del proceso) se pueden gestionar tras un reinicio.
"""

from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

DSN = os.environ.get("CENTRAL_API_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not DSN, reason="CENTRAL_API_TEST_DATABASE_URL no configurada")


@pytest.fixture
def pg_panel(monkeypatch):
    from central_api import db
    from central_api.admin import users as admin_users
    from central_api.main import create_app
    from central_api.settings import get_settings

    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048).private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("ADMIN_TOKEN", "pg-key-actions-token")
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "pg-key-actions-hmac")
    monkeypatch.setenv("RSA_PRIVATE_KEY", pem)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    for cache in (get_settings, db._fabrica, db._motor):
        cache.cache_clear()
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    with TestClient(create_app()) as client:
        yield client, admin_users
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    for cache in (get_settings, db._fabrica, db._motor):
        cache.cache_clear()


H = {"Authorization": "Bearer pg-key-actions-token"}


def _fila(key_id: str):
    import sqlalchemy as sa

    from central_api.db import normalizar_dsn

    engine = sa.create_engine(normalizar_dsn(DSN))
    try:
        with engine.connect() as conn:
            return conn.execute(
                sa.text(
                    "SELECT revoked_at IS NOT NULL, scopes, expires_at "
                    "FROM api_keys WHERE id = :id"
                ),
                {"id": key_id},
            ).one()
    finally:
        engine.dispose()


def test_revocar_restaurar_editar_persisten_y_afectan_autenticacion(pg_panel) -> None:
    client, admin_users = pg_panel
    email = f"pg-actions-{uuid.uuid4().hex[:8]}@example.test"
    secreto = f"pgAct{uuid.uuid4().hex[:10]}"
    creado = client.post(
        "/admin/users",
        headers=H,
        json={"email": email, "api_key": secreto, "motivo": "abc"},
    )
    assert creado.status_code == 201, creado.text
    key_id = creado.json()["credenciales"]["clave"]["id"]
    assert client.get("/api/v3/bots", auth=(email, secreto)).status_code == 200

    # Simula un reinicio: la clave solo existe en PostgreSQL.
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()

    revocada = client.post(f"/admin/api-keys/{key_id}/revoke", headers=H, json={"motivo": "rev"})
    assert revocada.status_code == 200, revocada.text
    assert _fila(key_id)[0] is True
    assert client.get("/api/v3/bots", auth=(email, secreto)).status_code == 401

    admin_users.API_KEYS.clear()
    restaurada = client.post(f"/admin/api-keys/{key_id}/restore", headers=H, json={"motivo": "res"})
    assert restaurada.status_code == 200, restaurada.text
    assert _fila(key_id)[0] is False
    assert client.get("/api/v3/bots", auth=(email, secreto)).status_code == 200

    admin_users.API_KEYS.clear()
    editada = client.patch(
        f"/admin/api-keys/{key_id}",
        headers=H,
        json={"motivo": "edi", "scopes": ["bots:read"], "expira_en": "2099-01-01T00:00:00Z"},
    )
    assert editada.status_code == 200, editada.text
    _, scopes, expires_at = _fila(key_id)
    assert list(scopes) == ["bots:read"]
    assert expires_at.astimezone(timezone.utc) == datetime(2099, 1, 1, tzinfo=timezone.utc)

    listado = client.get("/admin/users", headers=H)
    fila = next(u for u in listado.json()["usuarios"] if u["email"] == email)
    clave = next(k for k in fila["claves_api"] if k["id"] == key_id)
    assert clave["scopes"] == ["bots:read"]
    assert datetime.fromisoformat(clave["expira_en"]) == datetime(2099, 1, 1, tzinfo=timezone.utc)


def test_no_se_puede_restaurar_una_clave_reemplazada(pg_panel) -> None:
    client, _ = pg_panel
    email = f"pg-replaced-{uuid.uuid4().hex[:8]}@example.test"
    creado = client.post(
        "/admin/users",
        headers=H,
        json={"email": email, "api_key": f"pgRep{uuid.uuid4().hex[:10]}", "motivo": "abc"},
    )
    user_id = creado.json()["usuario"]["id"]
    key_id = creado.json()["credenciales"]["clave"]["id"]
    rotada = client.post(
        f"/admin/users/{user_id}/api-keys/rotate",
        headers=H,
        json={"key_id": key_id, "motivo": "rot", "valor_fijo": ""},
    )
    assert rotada.status_code == 200, rotada.text

    restaurar = client.post(f"/admin/api-keys/{key_id}/restore", headers=H, json={"motivo": "res"})
    assert restaurar.status_code == 409
    assert _fila(key_id)[0] is True


def test_clave_inexistente_devuelve_404(pg_panel) -> None:
    client, _ = pg_panel
    fantasma = str(uuid.uuid4())
    assert client.post(f"/admin/api-keys/{fantasma}/revoke", headers=H, json={"motivo": "rev"}).status_code == 404
    assert client.post("/admin/api-keys/no-es-uuid/revoke", headers=H, json={"motivo": "rev"}).status_code == 404
