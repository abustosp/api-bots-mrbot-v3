"""Pruebas de autenticación HTTPBasic, headers V1 y rechazo de Bearer."""

from __future__ import annotations

import base64
import asyncio
from contextlib import asynccontextmanager
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.admin import users as admin_users  # noqa: E402
from central_api.admin.audit import AUDIT_LOG  # noqa: E402
from central_api.api.dependencies import _principal_desde_pg  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.models.identity import User  # noqa: E402
from central_api.security import sign_secret  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


def _active_user(email: str, key: str) -> str:
    user_id = str(uuid.uuid4())
    admin_users.USERS[user_id] = admin_users.AdminUser(
        id=user_id, email=email, display_name="Test", estado="habilitado"
    )
    admin_users.emitir_clave(user_id, [], "", key)
    return user_id


def _basic(usuario: str, key: str) -> dict:
    token = base64.b64encode(f"{usuario}:{key}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_postgres_principal_requires_enabled_matching_owner(monkeypatch) -> None:
    key_id = "db-key-selector"
    api_secret = "persisted-api-secret"
    account_id = uuid.uuid4()
    key_row = SimpleNamespace(
        verifier_hmac=sign_secret("db-hmac-secret", api_secret),
        user_id=account_id,
        scopes=[],
    )
    account = SimpleNamespace(
        id=account_id, email="pg-user@example.com", habilitado=True
    )

    class Result:
        def scalar_one_or_none(self):
            return key_row

    class Session:
        async def execute(self, _statement):
            return Result()

        async def get(self, model, _identity):
            assert model is User
            return account

    @asynccontextmanager
    async def fake_session():
        yield Session()

    import central_api.db

    monkeypatch.setattr(central_api.db, "nueva_sesion", fake_session)

    async def validate(identity: str | None):
        return await _principal_desde_pg(
            "db-hmac-secret", key_id, api_secret, identity
        )

    principal = asyncio.run(validate("PG-USER@example.com"))
    assert principal is not None
    assert principal.user_id == str(account_id)
    assert asyncio.run(validate("someone-else@example.com")) is None

    account.habilitado = False
    assert asyncio.run(validate("pg-user@example.com")) is None


def test_basic_owner_validation_bearer_rejected_and_x_api_key_compat(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "bearer-auth-test-secret")
    get_settings.cache_clear()
    saved_users = dict(admin_users.USERS)
    saved_keys = dict(admin_users.API_KEYS)
    saved_audit = list(AUDIT_LOG)
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    try:
        key = "api/key+legacy"
        _active_user("case@example.com", key)
        client = TestClient(create_app())

        private = client.get("/api/v3/mi/cuenta", headers=_basic("CASE@example.com", key))
        assert private.status_code == 200
        personal = client.get("/api/v3/usuarios/me", headers=_basic("case@example.com", key))
        assert personal.status_code == 200
        assert personal.json()["usuario"] == "case@example.com"
        assert personal.json()["estado"] == "habilitado"

        legacy = client.get("/api/v3/mi/cuenta", headers={"X-API-Key": key})
        assert legacy.status_code == 200

        # El endpoint generador de tokens y el esquema Bearer ya no existen.
        assert client.post(
            "/api/v3/auth/token", json={"usuario": "case@example.com", "api_key": key}
        ).status_code in (404, 405)
        token = ".".join(
            base64.urlsafe_b64encode(v.encode()).decode() for v in ("case@example.com", key)
        )
        bearer = client.get("/api/v3/mi/cuenta", headers={"Authorization": f"Bearer {token}"})
        assert bearer.status_code == 401

        wrong_owner = client.get("/api/v3/mi/cuenta", headers=_basic("otro@example.com", key))
        invalid = client.get("/api/v3/mi/cuenta", headers=_basic("case@example.com", "bad-key"))
        assert wrong_owner.status_code == invalid.status_code == 401
        assert wrong_owner.headers["www-authenticate"] == "Basic"
        assert invalid.headers["www-authenticate"] == "Basic"
        assert wrong_owner.json()["detail"] == invalid.json()["detail"]
    finally:
        admin_users.USERS.clear()
        admin_users.USERS.update(saved_users)
        admin_users.API_KEYS.clear()
        admin_users.API_KEYS.update(saved_keys)
        AUDIT_LOG[:] = saved_audit
        get_settings.cache_clear()


def test_openapi_protected_operations_advertise_basic_and_v1_headers(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        schema = client.get("/openapi.json").json()
        schemes = schema["components"]["securitySchemes"]
        assert "HTTPBearer" not in schemes
        assert schemes["HTTPBasic"]["scheme"] == "basic"
        security = schema["paths"]["/api/v3/mi/cuenta"]["get"]["security"]
        assert {"HTTPBasic": []} in security
        assert {"ApiKeyHeader": []} in security
        assert all("HTTPBearer" not in item for item in security)
        assert "/api/v3/auth/token" not in schema["paths"]
        assert "Authorize" in schema["info"]["description"]
        docs = client.get("/docs")
        assert docs.status_code == 200
        assert "/openapi.json" in docs.text
    finally:
        get_settings.cache_clear()
