"""Aceptación de la custodia cifrada y copia controlada de API keys."""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.admin import users as admin_users  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.security.api_key_vault import (  # noqa: E402
    ApiKeyVaultError,
    decrypt_api_key,
    encrypt_api_key,
)
from central_api.settings import get_settings  # noqa: E402


def _private_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")


@pytest.fixture
def copy_panel(monkeypatch):
    from central_api.admin.audit import AUDIT_LOG

    monkeypatch.setenv("ADMIN_TOKEN", "api-key-copy-test-token")
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "test-hmac-secret-for-api-key-vault")
    monkeypatch.setenv("RSA_PRIVATE_KEY", _private_pem())
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    admin_users.CREDIT_LEDGER.clear()
    AUDIT_LOG.clear()
    yield TestClient(create_app())
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    admin_users.CREDIT_LEDGER.clear()
    AUDIT_LOG.clear()
    get_settings.cache_clear()


def _admin_headers() -> dict[str, str]:
    return {"Authorization": "Bearer api-key-copy-test-token"}


def test_hybrid_vault_handles_long_keys_and_rejects_corruption(monkeypatch) -> None:
    monkeypatch.setenv("RSA_PRIVATE_KEY", _private_pem())
    get_settings.cache_clear()
    secret = "very-long-api-key-value-" * 30
    envelope = encrypt_api_key(secret)
    assert secret not in envelope
    assert "RSA-OAEP-SHA256+Fernet" in envelope
    assert decrypt_api_key(envelope) == secret

    malformed = json.loads(envelope)
    malformed["ciphertext"] = "not-base64!"
    with pytest.raises(ApiKeyVaultError):
        decrypt_api_key(json.dumps(malformed))
    get_settings.cache_clear()


def test_admin_copia_clave_cifrada_sin_exponerla_en_listados(copy_panel) -> None:
    cleartext = "manual-api-key-secret-2026"
    created = copy_panel.post(
        "/admin/users",
        json={
            "email": "copy-test@example.com",
            "api_key": cleartext,
            "estado": "habilitado",
            "motivo": "crear usuario para prueba de copia",
        },
        headers=_admin_headers(),
    )
    assert created.status_code == 201
    user_id = created.json()["usuario"]["id"]

    listed = copy_panel.get("/admin/users", headers=_admin_headers())
    assert listed.status_code == 200
    assert listed.headers["cache-control"] == "private, no-store"
    row = next(user for user in listed.json()["usuarios"] if user["id"] == user_id)
    key = next(key for key in row["claves_api"] if key["revelable"])
    serialized = json.dumps(row)
    assert cleartext not in serialized
    assert "encrypted_value" not in serialized
    assert "valor_cifrado" not in serialized

    unauthorized = copy_panel.post(
        f"/admin/users/{user_id}/api-keys/{key['id']}/reveal"
    )
    assert unauthorized.status_code == 401

    reveal = copy_panel.post(
        f"/admin/users/{user_id}/api-keys/{key['id']}/reveal",
        headers=_admin_headers(),
    )
    assert reveal.status_code == 200
    assert reveal.headers["cache-control"] == "private, no-store"
    assert reveal.json() == {"success": True, "api_key": cleartext}

    event = copy_panel.get("/admin/audit?limit=20", headers=_admin_headers())
    assert event.status_code == 200
    revealed_events = [item for item in event.json()["eventos"] if item["action"] == "user.api_key.revealed"]
    assert revealed_events
    assert cleartext not in json.dumps(revealed_events)

    disabled = copy_panel.post(
        f"/admin/users/{user_id}/disable",
        json={"motivo": "desactivar usuario tras copia de clave"},
        headers=_admin_headers(),
    )
    assert disabled.status_code == 200
    blocked = copy_panel.post(
        f"/admin/users/{user_id}/api-keys/{key['id']}/reveal",
        headers=_admin_headers(),
    )
    assert blocked.status_code == 409


def test_clave_legacy_sin_ciphertext_requiere_reemision(copy_panel) -> None:
    user_id = str(uuid.uuid4())
    user = admin_users.AdminUser(id=user_id, email="legacy@example.com")
    admin_users.USERS[user_id] = user
    meta, _ = admin_users.emitir_clave(user_id, [], "", "legacy-key")
    meta.valor_cifrado = None

    listed = copy_panel.get("/admin/users", headers=_admin_headers())
    row = next(user for user in listed.json()["usuarios"] if user["id"] == user_id)
    assert row["claves_api"][0]["revelable"] is False

    reveal = copy_panel.post(
        f"/admin/users/{user_id}/api-keys/{meta.id}/reveal",
        headers=_admin_headers(),
    )
    assert reveal.status_code == 409
    assert "emita una nueva" in reveal.json()["detail"]


def test_ruta_admin_revela_ciphertext_persistido_en_fila_api_key(
    copy_panel, monkeypatch
) -> None:
    from central_api import db
    from central_api.admin.users import AdminUser, _persistir_api_key_pg, emitir_clave

    user_id = str(uuid.uuid4())
    user = AdminUser(id=user_id, email="persisted@example.com", estado="habilitado")
    meta, cleartext = emitir_clave(user_id, [], "", "abp")

    class Result:
        def __init__(self, row):
            self.row = row

        def first(self):
            return self.row

    class Session:
        def __init__(self):
            self.added = []
            self.key_row = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, key):
            return object()

        def add(self, row):
            self.added.append(row)
            self.key_row = row

        async def flush(self):
            return None

        async def execute(self, statement):
            return Result((self.key_row, True))

    session = Session()
    monkeypatch.setattr(db, "db_configurado", lambda: True)
    monkeypatch.setattr(db, "nueva_sesion", lambda: session)

    import asyncio

    asyncio.run(_persistir_api_key_pg(user, meta, cleartext))
    row = session.key_row
    assert row is not None
    assert row.encrypted_value
    assert cleartext not in row.encrypted_value
    assert decrypt_api_key(row.encrypted_value) == cleartext

    reveal = copy_panel.post(
        f"/admin/users/{user_id}/api-keys/{meta.id}/reveal",
        headers=_admin_headers(),
    )
    assert reveal.status_code == 200
    assert reveal.headers["cache-control"] == "private, no-store"
    assert reveal.json()["api_key"] == cleartext


def test_rotacion_persistente_cifra_nueva_y_revoca_anterior_en_la_misma_ruta(
    copy_panel, monkeypatch
) -> None:
    from central_api import db
    from central_api.api.dependencies import _selector_y_secreto
    from central_api.models.identity import ApiKey, User
    from central_api.security.api_keys import fingerprint_secret
    from central_api.store import utcnow

    original = "clave-persistida-inicial"
    created = copy_panel.post(
        "/admin/users",
        json={
            "email": "rotate-pg@example.com",
            "api_key": original,
            "estado": "habilitado",
            "motivo": "crear usuario para prueba de rotación persistente",
        },
        headers=_admin_headers(),
    )
    assert created.status_code == 201
    user_id = created.json()["usuario"]["id"]
    old_key_id = created.json()["credenciales"]["clave"]["id"]
    old_meta = admin_users.API_KEYS[old_key_id]
    user_row = User(id=uuid.UUID(user_id), email="rotate-pg@example.com", habilitado=True)
    selector, secret = _selector_y_secreto(original)
    old_row = ApiKey(
        id=uuid.UUID(old_key_id),
        user_id=uuid.UUID(user_id),
        key_prefix=selector,
        verifier_hmac=fingerprint_secret("test-hmac-secret-for-api-key-vault", secret),
        encrypted_value=old_meta.valor_cifrado,
        scopes=[],
        expires_at=None,
        revoked_at=None,
        created_at=utcnow(),
    )

    class Result:
        def __init__(self, row):
            self.row = row

        def scalar_one_or_none(self):
            return self.row

        def first(self):
            return self.row

    class Session:
        def __init__(self):
            self.new_rows = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, model, key):
            if model is User and key == user_row.id:
                return user_row
            if model is ApiKey and key == old_row.id:
                return old_row
            return None

        async def execute(self, statement):
            if self.new_rows:
                return Result((self.new_rows[-1], True))
            return Result(old_row)

        def add(self, row):
            self.new_rows.append(row)

        async def flush(self):
            return None

    session = Session()
    monkeypatch.setattr(db, "db_configurado", lambda: True)
    monkeypatch.setattr(db, "nueva_sesion", lambda: session)
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()

    rotated = copy_panel.post(
        f"/admin/users/{user_id}/api-keys/rotate",
        json={
            "key_id": old_key_id,
            "motivo": "rotación persistente de clave de usuario",
            "valor_fijo": "clave-nueva-persistida",
        },
        headers=_admin_headers(),
    )
    assert rotated.status_code == 200, rotated.text
    assert old_row.revoked_at is not None
    new_row = session.new_rows[-1]
    assert new_row.replaces_key_id == old_row.id
    assert new_row.encrypted_value
    assert "clave-nueva-persistida" not in new_row.encrypted_value
    assert decrypt_api_key(new_row.encrypted_value) == "clave-nueva-persistida"

    reveal = copy_panel.post(
        f"/admin/users/{user_id}/api-keys/{new_row.id}/reveal",
        headers=_admin_headers(),
    )
    assert reveal.status_code == 200, reveal.text
    assert reveal.json()["api_key"] == "clave-nueva-persistida"


def test_claves_api_lista_clave_pg_para_copiar_sin_exponer_ciphertext(
    copy_panel, monkeypatch
) -> None:
    from central_api import db
    from central_api.api.dependencies import _selector_y_secreto
    from central_api.models.identity import ApiKey, User
    from central_api.security.api_keys import fingerprint_secret
    from central_api.store import utcnow

    cleartext = "clave-persistida-para-listado"
    created = copy_panel.post(
        "/admin/users",
        json={
            "email": "keys-list@example.com",
            "api_key": cleartext,
            "estado": "habilitado",
            "motivo": "crear usuario para lista de claves persistidas",
        },
        headers=_admin_headers(),
    )
    assert created.status_code == 201
    user_id = created.json()["usuario"]["id"]
    key_id = created.json()["credenciales"]["clave"]["id"]
    meta = admin_users.API_KEYS[key_id]
    user_row = User(id=uuid.UUID(user_id), email="keys-list@example.com", habilitado=True)
    selector, secret = _selector_y_secreto(cleartext)
    key_row = ApiKey(
        id=uuid.UUID(key_id),
        user_id=uuid.UUID(user_id),
        key_prefix=selector,
        verifier_hmac=fingerprint_secret("test-hmac-secret-for-api-key-vault", secret),
        encrypted_value=meta.valor_cifrado,
        scopes=[],
        expires_at=None,
        revoked_at=None,
        created_at=utcnow(),
    )

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

        def first(self):
            return self.rows

    class Session:
        def __init__(self):
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, statement):
            self.calls += 1
            if self.calls == 1:
                return Result([(user_row.id, user_row.email, user_row.habilitado)])
            if self.calls == 2:
                return Result([(
                    key_row.id,
                    key_row.user_id,
                    key_row.key_prefix,
                    key_row.scopes,
                    key_row.expires_at,
                    key_row.revoked_at,
                    key_row.created_at,
                    True,
                )])
            return Result((key_row, True))

    session = Session()
    monkeypatch.setattr(db, "db_configurado", lambda: True)
    monkeypatch.setattr(db, "nueva_sesion", lambda: session)
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()

    listed = copy_panel.get("/admin/api-keys", headers=_admin_headers())
    assert listed.status_code == 200, listed.text
    assert listed.headers["cache-control"] == "private, no-store"
    key = next(item for item in listed.json()["claves"] if item["id"] == key_id)
    assert key["revelable"] is True
    assert key["owner_enabled"] is True
    serialized = json.dumps(key)
    assert cleartext not in serialized
    assert "encrypted_value" not in serialized
    assert "valor_cifrado" not in serialized

    reveal = copy_panel.post(
        f"/admin/users/{user_id}/api-keys/{key_id}/reveal",
        headers=_admin_headers(),
    )
    assert reveal.status_code == 200, reveal.text
    assert reveal.json() == {"success": True, "api_key": cleartext}
