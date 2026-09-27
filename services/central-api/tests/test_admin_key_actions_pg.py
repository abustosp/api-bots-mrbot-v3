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


def _rotar(client, user_id: str, key_id: str, valor: str):
    return client.post(
        f"/admin/users/{user_id}/api-keys/rotate",
        headers=H,
        json={"key_id": key_id, "motivo": "rot", "valor_fijo": valor},
    )


def test_usuario_puede_volver_a_un_valor_revocado_como_abp(pg_panel) -> None:
    """Regresión: 'abp' -> aleatoria -> 'abp' fallaba con 'valor de clave ya registrado'."""
    client, admin_users = pg_panel
    email = f"pg-reuse-{uuid.uuid4().hex[:8]}@example.test"
    valor = f"abp{uuid.uuid4().hex[:6]}"
    creado = client.post("/admin/users", headers=H, json={"email": email, "api_key": valor, "motivo": "abc"})
    assert creado.status_code == 201, creado.text
    user_id = creado.json()["usuario"]["id"]
    k1 = creado.json()["credenciales"]["clave"]["id"]

    r2 = _rotar(client, user_id, k1, "")
    assert r2.status_code == 200, r2.text
    k2 = r2.json()["clave"]["id"]

    # Simula reinicio para que solo PostgreSQL decida.
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    r3 = _rotar(client, user_id, k2, valor)
    assert r3.status_code == 200, r3.text
    assert r3.json()["valor_unica_vez"] == valor
    assert client.get("/api/v3/bots", auth=(email, valor)).status_code == 200
    assert _fila(k1)[0] is True and _fila(k2)[0] is True


def test_reemplazar_por_el_mismo_valor_actual(pg_panel) -> None:
    client, _ = pg_panel
    email = f"pg-same-{uuid.uuid4().hex[:8]}@example.test"
    valor = f"same{uuid.uuid4().hex[:6]}"
    creado = client.post("/admin/users", headers=H, json={"email": email, "api_key": valor, "motivo": "abc"})
    user_id = creado.json()["usuario"]["id"]
    k1 = creado.json()["credenciales"]["clave"]["id"]
    r = _rotar(client, user_id, k1, valor)
    assert r.status_code == 200, r.text
    assert client.get("/api/v3/bots", auth=(email, valor)).status_code == 200
    assert _fila(k1)[0] is True


def test_valor_activo_de_otro_usuario_sigue_bloqueado(pg_panel) -> None:
    client, admin_users = pg_panel
    compartido = f"shared{uuid.uuid4().hex[:6]}"
    a = client.post("/admin/users", headers=H, json={"email": f"pg-a-{uuid.uuid4().hex[:8]}@example.test", "api_key": compartido, "motivo": "abc"})
    assert a.status_code == 201, a.text
    b = client.post("/admin/users", headers=H, json={"email": f"pg-b-{uuid.uuid4().hex[:8]}@example.test", "api_key": f"otro{uuid.uuid4().hex[:6]}", "motivo": "abc"})
    b_user = b.json()["usuario"]["id"]
    b_key = b.json()["credenciales"]["clave"]["id"]
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    r = _rotar(client, b_user, b_key, compartido)
    assert r.status_code == 409
    assert r.json()["detail"] == "valor de clave ya registrado"
    # La clave de B sigue activa: la rotación fallida no la revoca.
    assert _fila(b_key)[0] is False


def test_restaurar_con_valor_ya_activo_devuelve_409(pg_panel) -> None:
    client, admin_users = pg_panel
    valor = f"rest{uuid.uuid4().hex[:6]}"
    a = client.post("/admin/users", headers=H, json={"email": f"pg-ra-{uuid.uuid4().hex[:8]}@example.test", "api_key": valor, "motivo": "abc"})
    a_key = a.json()["credenciales"]["clave"]["id"]
    assert client.post(f"/admin/api-keys/{a_key}/revoke", headers=H, json={"motivo": "rev"}).status_code == 200
    b = client.post("/admin/users", headers=H, json={"email": f"pg-rb-{uuid.uuid4().hex[:8]}@example.test", "api_key": valor, "motivo": "abc"})
    assert b.status_code == 201, b.text
    admin_users.API_KEYS.clear()
    r = client.post(f"/admin/api-keys/{a_key}/restore", headers=H, json={"motivo": "res"})
    assert r.status_code == 409
    assert "ya está activo" in r.json()["detail"]
    assert _fila(a_key)[0] is True
