"""Pruebas del alta pública, activación, consulta propia y rotación de API key."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.admin import users as admin_users  # noqa: E402
from central_api.admin.audit import AUDIT_LOG  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


def test_public_signup_disables_user_and_admin_can_enable_then_rotate(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "public-users-test-secret")
    monkeypatch.setenv("ADMIN_TOKEN", "admin-users-test-token")
    monkeypatch.delenv("SMTP_SERVER", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    get_settings.cache_clear()
    saved_users = dict(admin_users.USERS)
    saved_keys = dict(admin_users.API_KEYS)
    saved_audit = list(AUDIT_LOG)
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    try:
        client = TestClient(create_app())
        created = client.post(
            "/api/v3/usuarios",
            json={"usuario": "New.User@example.com", "nombre": "Nueva", "enviar_email": False},
        )
        assert created.status_code == 201
        assert created.headers["cache-control"] == "no-store"
        body = created.json()
        assert body["usuario"] == "new.user@example.com"
        assert body["estado"] == "deshabilitado"
        api_key = body["api_key"]
        user_id = next(iter(admin_users.USERS))
        assert user_id in admin_users.USERS
        assert api_key not in repr(admin_users.API_KEYS)

        duplicate = client.post(
            "/api/v3/usuarios", json={"usuario": "NEW.USER@example.com"}
        )
        assert duplicate.status_code == 409

        rejected = client.post(
            "/api/v3/auth/token",
            json={"usuario": "new.user@example.com", "api_key": api_key},
        )
        assert rejected.status_code == 401
        assert rejected.headers["www-authenticate"] == "Bearer"

        enabled = client.post(
            f"/admin/users/{user_id}/enable",
            headers={"Authorization": "Bearer admin-users-test-token"},
            json={"motivo": "Activar cuenta recién creada"},
        )
        assert enabled.status_code == 200
        token_response = client.post(
            "/api/v3/auth/token",
            json={"usuario": "NEW.USER@example.com", "api_key": api_key},
        )
        assert token_response.status_code == 200
        bearer = token_response.json()["access_token"]
        personal = client.get(
            "/api/v3/usuarios/me", headers={"Authorization": f"Bearer {bearer}"}
        )
        assert personal.status_code == 200
        assert personal.json()["estado"] == "habilitado"
        assert personal.json()["plan"] == "free"
        assert personal.json()["consultas"]["disponibles"] >= 0

        changed = client.post(
            "/api/v3/usuarios/establecer-api-key",
            json={
                "usuario": "new.user@example.com",
                "api_key_actual": api_key,
                "api_key_nueva": "otra-clave-segura",
            },
        )
        assert changed.status_code == 200
        assert "otra-clave-segura" not in changed.text
        old_token = client.post(
            "/api/v3/auth/token",
            json={"usuario": "new.user@example.com", "api_key": api_key},
        )
        assert old_token.status_code == 401
        new_token = client.post(
            "/api/v3/auth/token",
            json={"usuario": "new.user@example.com", "api_key": "otra-clave-segura"},
        )
        assert new_token.status_code == 200
        legacy = client.get(
            "/api/v3/mi/cuenta", headers={"X-API-Key": "otra-clave-segura"}
        )
        assert legacy.status_code == 200
    finally:
        admin_users.USERS.clear()
        admin_users.USERS.update(saved_users)
        admin_users.API_KEYS.clear()
        admin_users.API_KEYS.update(saved_keys)
        AUDIT_LOG[:] = saved_audit
        get_settings.cache_clear()


def test_public_reset_is_generic_and_set_key_rejects_wrong_current(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "public-users-reset-test-secret")
    monkeypatch.delenv("SMTP_SERVER", raising=False)
    monkeypatch.delenv("SMTP_USER", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    get_settings.cache_clear()
    saved_users = dict(admin_users.USERS)
    saved_keys = dict(admin_users.API_KEYS)
    saved_audit = list(AUDIT_LOG)
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    try:
        client = TestClient(create_app())
        created = client.post(
            "/api/v3/usuarios", json={"usuario": "reset@example.com"}
        )
        assert created.status_code == 201
        api_key = created.json()["api_key"]
        user_id = next(iter(admin_users.USERS))
        admin_users.USERS[user_id].estado = "habilitado"

        known = client.post(
            "/api/v3/usuarios/restablecer-api-key",
            json={"usuario": "reset@example.com"},
        )
        unknown = client.post(
            "/api/v3/usuarios/restablecer-api-key",
            json={"usuario": "absent@example.com"},
        )
        assert known.status_code == unknown.status_code == 202
        assert known.headers["cache-control"] == "no-store"
        assert known.json() == unknown.json()
        assert api_key not in known.text

        invalid = client.post(
            "/api/v3/usuarios/establecer-api-key",
            json={
                "usuario": "reset@example.com",
                "api_key_actual": "incorrecta",
                "api_key_nueva": "nueva-clave",
            },
        )
        assert invalid.status_code == 401
        assert invalid.headers["www-authenticate"] == "Bearer"
    finally:
        admin_users.USERS.clear()
        admin_users.USERS.update(saved_users)
        admin_users.API_KEYS.clear()
        admin_users.API_KEYS.update(saved_keys)
        AUDIT_LOG[:] = saved_audit
        get_settings.cache_clear()


def test_public_reset_delivers_only_by_email_and_revokes_old_key(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "public-users-email-reset-secret")
    monkeypatch.setenv("SMTP_SERVER", "smtp.example.test")
    monkeypatch.setenv("SMTP_USER", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", "test-smtp-password")
    monkeypatch.setenv("SMTP_STARTTLS", "true")
    get_settings.cache_clear()
    saved_users = dict(admin_users.USERS)
    saved_keys = dict(admin_users.API_KEYS)
    saved_audit = list(AUDIT_LOG)
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    delivered: list[tuple[str, str]] = []

    def capture_email(destinatario: str, api_key: str, nombre: str = ""):
        delivered.append((destinatario, api_key))
        return True, "enviado"

    def failed_email(destinatario: str, api_key: str, nombre: str = ""):
        return False, "fallo SMTP"

    monkeypatch.setattr(
        "central_api.api.users.enviar_credenciales_email", failed_email
    )
    try:
        client = TestClient(create_app())
        created = client.post(
            "/api/v3/usuarios", json={"usuario": "email-reset@example.com"}
        )
        old_key = created.json()["api_key"]
        user_id = next(iter(admin_users.USERS))
        admin_users.USERS[user_id].estado = "habilitado"

        failed_reset = client.post(
            "/api/v3/usuarios/restablecer-api-key",
            json={"usuario": "email-reset@example.com"},
        )
        assert failed_reset.status_code == 202
        assert client.post(
            "/api/v3/auth/token",
            json={"usuario": "email-reset@example.com", "api_key": old_key},
        ).status_code == 200

        monkeypatch.setattr(
            "central_api.api.users.enviar_credenciales_email", capture_email
        )
        reset = client.post(
            "/api/v3/usuarios/restablecer-api-key",
            json={"usuario": "email-reset@example.com"},
        )
        assert reset.status_code == 202
        assert "api_key" not in reset.json()
        assert len(delivered) == 1
        assert delivered[0][0] == "email-reset@example.com"
        new_key = delivered[0][1]
        assert old_key != new_key
        assert new_key not in reset.text
        assert client.post(
            "/api/v3/auth/token",
            json={"usuario": "email-reset@example.com", "api_key": old_key},
        ).status_code == 401
        assert client.post(
            "/api/v3/auth/token",
            json={"usuario": "email-reset@example.com", "api_key": new_key},
        ).status_code == 200
    finally:
        admin_users.USERS.clear()
        admin_users.USERS.update(saved_users)
        admin_users.API_KEYS.clear()
        admin_users.API_KEYS.update(saved_keys)
        AUDIT_LOG[:] = saved_audit
        get_settings.cache_clear()
