"""Acceso de depuración y gestión de claves API desde el panel.

Cubre los cambios pedidos: alta de usuarios sin mail (p. ej. ``abp``),
emisión con valor fijo (p. ej. ``testing``) autenticable por el borde
público, listado/edición/revocación de claves, clave pública RSA de la
central y su reflejo en el panel.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


@pytest.fixture
def admin(monkeypatch):
    """Token admin + stores en memoria aislados por test."""
    from central_api.admin import users as admin_users

    monkeypatch.setenv("ADMIN_TOKEN", "admin-debug-token")
    get_settings.cache_clear()
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    admin_users.CREDIT_LEDGER.clear()
    yield admin_users
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    admin_users.CREDIT_LEDGER.clear()
    get_settings.cache_clear()


def _headers() -> dict:
    return {"Authorization": "Bearer admin-debug-token"}


def _crear_usuario(cliente: TestClient, identidad: str) -> dict:
    respuesta = cliente.post(
        "/admin/users",
        json={
            "email": identidad,
            "display_name": "debug",
            "plan": "free",
            "motivo": "alta de depuración desde tests",
        },
        headers=_headers(),
    )
    return respuesta


def test_crear_usuario_debug_sin_mail(admin) -> None:
    cliente = TestClient(create_app())
    respuesta = _crear_usuario(cliente, "abp")
    assert respuesta.status_code == 201
    assert respuesta.json()["usuario"]["email"] == "abp"

    duplicado = _crear_usuario(cliente, "ABP")
    assert duplicado.status_code == 409

    invalido = _crear_usuario(cliente, "a")
    assert invalido.status_code == 400

    clasico = _crear_usuario(cliente, "debug@example.com")
    assert clasico.status_code == 201
    assert clasico.json()["usuario"]["email"] == "debug@example.com"


def test_crear_usuario_con_api_key_estado_y_envio_opcional(admin, monkeypatch) -> None:
    from central_api.admin import users as admin_users

    entregas: list[tuple[str, str, str]] = []

    def _enviar(destinatario: str, api_key: str, nombre: str = "") -> tuple[bool, str]:
        entregas.append((destinatario, api_key, nombre))
        return True, "enviado"

    monkeypatch.setattr(admin_users, "enviar_credenciales_email", _enviar)
    cliente = TestClient(create_app())

    sin_envio = cliente.post(
        "/admin/users",
        json={
            "email": "sin-envio@example.com",
            "api_key": "abc",
            "estado": "deshabilitado",
            "enviar_credenciales": False,
            "motivo": "alta con credencial definida sin envío",
        },
        headers=_headers(),
    )
    assert sin_envio.status_code == 201
    assert sin_envio.headers["cache-control"] == "private, no-store"
    cuerpo_sin_envio = sin_envio.json()
    assert cuerpo_sin_envio["usuario"]["estado"] == "deshabilitado"
    assert cuerpo_sin_envio["credenciales"]["valor_unica_vez"] == "abc"
    assert cuerpo_sin_envio["credenciales"]["enviadas"] is False
    assert entregas == []

    con_envio = cliente.post(
        "/admin/users",
        json={
            "email": "con-envio@example.com",
            "api_key": "mail-test-key",
            "habilitado": True,
            "send_api_key_email": True,
            "motivo": "alta con credencial definida y envío",
        },
        headers=_headers(),
    )
    assert con_envio.status_code == 201
    cuerpo_con_envio = con_envio.json()
    assert cuerpo_con_envio["usuario"]["estado"] == "habilitado"
    assert cuerpo_con_envio["credenciales"]["enviadas"] is True
    assert cuerpo_con_envio["credenciales"]["valor_unica_vez"] == "mail-test-key"
    assert entregas == [("con-envio@example.com", "mail-test-key", "")]

    debug_con_envio = cliente.post(
        "/admin/users",
        json={
            "email": "abp",
            "enviar_credenciales": True,
            "motivo": "rechazar envío a usuario de depuración",
        },
        headers=_headers(),
    )
    assert debug_con_envio.status_code == 400


def test_emitir_clave_valor_fijo_y_autentica_borde(admin, monkeypatch) -> None:
    from central_api.admin import users as admin_users

    # El verificador HMAC queda ligado al secreto vigente: se fija antes de emitir.
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "secreto-servidor-test")
    get_settings.cache_clear()

    cliente = TestClient(create_app())
    usuario = _crear_usuario(cliente, "abp").json()["usuario"]

    respuesta = cliente.post(
        f"/admin/users/{usuario['id']}/api-keys",
        json={
            "scopes": [],
            "motivo": "emisión de depuración desde tests",
            "valor_fijo": "abc",
        },
        headers=_headers(),
    )
    assert respuesta.status_code == 201
    assert respuesta.headers["cache-control"] == "private, no-store"
    assert respuesta.json()["valor_unica_vez"] == "abc"

    # El valor fijo queda custodiado como HMAC, nunca en claro.
    meta = next(iter(admin_users.API_KEYS.values()))
    assert meta.verificador_hmac != "abc"

    # Con secreto configurado y sin base, el borde usa el fallback en memoria.
    ok = cliente.get("/api/v3/bots", headers={"X-API-Key": "abc"})
    assert ok.status_code == 200
    mala = cliente.get("/api/v3/bots", headers={"X-API-Key": "no-existe"})
    assert mala.status_code == 401

    demasiado_corta = cliente.post(
        f"/admin/users/{usuario['id']}/api-keys",
        json={
            "scopes": [],
            "motivo": "rechazar clave demasiado corta",
            "valor_fijo": "ab",
        },
        headers=_headers(),
    )
    assert demasiado_corta.status_code == 400


def test_usuario_lista_prefijo_y_cambia_estado_activo_desactivado(admin, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "secreto-servidor-test")
    get_settings.cache_clear()
    cliente = TestClient(create_app())
    usuario = _crear_usuario(cliente, "abp").json()["usuario"]
    user_id = usuario["id"]

    emitida = cliente.post(
        f"/admin/users/{user_id}/api-keys",
        json={
            "scopes": [],
            "motivo": "clave manual para usuario de prueba",
            "valor_fijo": "abp",
        },
        headers=_headers(),
    )
    assert emitida.status_code == 201
    assert emitida.json()["valor_unica_vez"] == "abp"
    assert emitida.headers["cache-control"] == "private, no-store"

    listado = cliente.get("/admin/users", headers=_headers())
    assert listado.status_code == 200
    assert listado.headers["cache-control"] == "private, no-store"
    fila = next(item for item in listado.json()["usuarios"] if item["id"] == user_id)
    assert fila["claves_api"][0]["prefijo"] == "abp"
    assert fila["claves_api"][0]["estado"] == "activa"
    assert "verificador_hmac" not in repr(fila)
    assert "valor_unica_vez" not in repr(fila)

    desactivado = cliente.post(
        f"/admin/users/{user_id}/disable",
        json={"motivo": "desactivar usuario desde la vista de usuarios"},
        headers=_headers(),
    )
    assert desactivado.status_code == 200
    assert desactivado.json()["usuario"]["estado"] == "deshabilitado"
    denegada = cliente.get("/api/v3/bots", headers={"X-API-Key": "abp"})
    assert denegada.status_code == 401

    fila_deshabilitada = cliente.get("/admin/users", headers=_headers()).json()["usuarios"][0]
    assert fila_deshabilitada["claves_api"][0]["estado"] == "revocada"

    activado = cliente.post(
        f"/admin/users/{user_id}/enable",
        json={"motivo": "reactivar usuario desde la vista de usuarios"},
        headers=_headers(),
    )
    assert activado.status_code == 200
    assert activado.json()["usuario"]["estado"] == "habilitado"
    assert cliente.get("/admin/users/" + user_id, headers=_headers()).headers[
        "cache-control"
    ] == "private, no-store"


def test_usuarios_persistidos_se_hidratan_y_su_estado_se_alterna(admin, monkeypatch) -> None:
    from datetime import datetime, timezone
    from uuid import uuid4

    user_id = str(uuid4())
    key_id = str(uuid4())
    persisted = {"enabled": True, "revoked_at": None}

    async def filas_pg():
        return (
            [(user_id, "abp", persisted["enabled"])],
            [
                (
                    key_id,
                    user_id,
                    "selector-abp",
                    [],
                    None,
                    persisted["revoked_at"],
                    datetime.now(timezone.utc),
                )
            ],
        )

    async def persistir_estado(user_id_arg: str, estado: str) -> None:
        assert user_id_arg == user_id
        persisted["enabled"] = estado == "habilitado"
        if estado == "deshabilitado":
            persisted["revoked_at"] = datetime.now(timezone.utc)

    monkeypatch.setattr(admin, "_filas_identidad_pg", filas_pg)
    monkeypatch.setattr(admin, "_persistir_estado_pg", persistir_estado)
    cliente = TestClient(create_app())

    respuesta = cliente.get("/admin/users", headers=_headers())
    assert respuesta.status_code == 200
    assert respuesta.headers["cache-control"] == "private, no-store"
    usuario = respuesta.json()["usuarios"][0]
    assert usuario["email"] == "abp"
    assert usuario["estado"] == "habilitado"
    assert usuario["claves_api"][0]["prefijo"] == "selector-abp"
    assert usuario["claves_api"][0]["estado"] == "activa"
    assert "verifier_hmac" not in repr(usuario)

    desactivado = cliente.post(
        f"/admin/users/{user_id}/disable",
        headers=_headers(),
        json={"motivo": "desactivar usuario persistido en la prueba"},
    )
    assert desactivado.status_code == 200
    usuario_deshabilitado = cliente.get(
        "/admin/users", headers=_headers()
    ).json()["usuarios"][0]
    assert usuario_deshabilitado["estado"] == "deshabilitado"
    assert usuario_deshabilitado["claves_api"][0]["estado"] == "revocada"

    activado = cliente.post(
        f"/admin/users/{user_id}/enable",
        headers=_headers(),
        json={"motivo": "activar usuario persistido en la prueba"},
    )
    assert activado.status_code == 200
    assert cliente.get("/admin/users", headers=_headers()).json()["usuarios"][0][
        "estado"
    ] == "habilitado"


def test_emision_manual_invoca_persistencia_y_mantiene_secreto_de_una_sola_vez(
    admin, monkeypatch
) -> None:
    cliente = TestClient(create_app())
    usuario = _crear_usuario(cliente, "abp").json()["usuario"]
    persistidas: list[tuple[str, str, str]] = []

    async def persistir(usuario_admin, meta, valor: str) -> None:
        persistidas.append((usuario_admin.id, meta.id, valor))

    monkeypatch.setattr(admin, "_persistir_api_key_pg", persistir)
    emitir = cliente.post(
        f"/admin/users/{usuario['id']}/api-keys",
        headers=_headers(),
        json={"valor_fijo": "abp", "motivo": "clave abp de prueba"},
    )
    assert emitir.status_code == 201
    assert emitir.headers["cache-control"] == "private, no-store"
    assert emitir.json()["valor_unica_vez"] == "abp"
    assert persistidas[0][0] == usuario["id"]
    assert persistidas[0][2] == "abp"


def test_listar_editar_revocar_restaurar_claves(admin, monkeypatch) -> None:
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "secreto-servidor-test")
    get_settings.cache_clear()

    cliente = TestClient(create_app())
    usuario = _crear_usuario(cliente, "abp").json()["usuario"]
    emitida = cliente.post(
        f"/admin/users/{usuario['id']}/api-keys",
        json={"scopes": [], "motivo": "emisión de depuración desde tests"},
        headers=_headers(),
    )
    assert emitida.status_code == 201
    assert emitida.headers["cache-control"] == "private, no-store"
    clave = emitida.json()["clave"]
    valor = emitida.json()["valor_unica_vez"]

    listado = cliente.get("/admin/api-keys", headers=_headers())
    assert listado.status_code == 200
    assert listado.json()["total"] == 2
    assert listado.json()["claves"][0]["estado"] == "activa"
    assert all("valor_unica_vez" not in item for item in listado.json()["claves"])
    assert all("verificador_hmac" not in item for item in listado.json()["claves"])

    vigente = cliente.get("/api/v3/bots", headers={"X-API-Key": valor})
    assert vigente.status_code == 200

    editada = cliente.patch(
        f"/admin/api-keys/{clave['id']}",
        json={
            "scopes": ["jobs:create"],
            "expira_en": "",
            "motivo": "ajuste de scopes desde tests",
        },
        headers=_headers(),
    )
    assert editada.status_code == 200
    assert editada.json()["clave"]["scopes"] == ["jobs:create"]

    revocada = cliente.post(
        f"/admin/api-keys/{clave['id']}/revoke",
        json={"motivo": "revocación de prueba desde tests"},
        headers=_headers(),
    )
    assert revocada.status_code == 200
    assert revocada.json()["clave"]["estado"] == "revocada"

    # Revocada: el borde deja de aceptarla aunque el HMAC coincida.
    denegada = cliente.get("/api/v3/bots", headers={"X-API-Key": valor})
    assert denegada.status_code == 401

    restaurada = cliente.post(
        f"/admin/api-keys/{clave['id']}/restore",
        json={"motivo": "restauración de prueba desde tests"},
        headers=_headers(),
    )
    assert restaurada.status_code == 200
    assert restaurada.json()["clave"]["estado"] == "activa"

    rehabilitada = cliente.get("/api/v3/bots", headers={"X-API-Key": valor})
    assert rehabilitada.status_code == 200


def test_rotar_clave_personalizada_revoca_la_anterior_solo_si_emite(admin, monkeypatch) -> None:
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "secreto-servidor-test")
    get_settings.cache_clear()

    cliente = TestClient(create_app())
    usuario = _crear_usuario(cliente, "rotacion@example.com").json()["usuario"]
    inicial = cliente.post(
        f"/admin/users/{usuario['id']}/api-keys",
        json={
            "scopes": ["jobs:create"],
            "motivo": "emisión de clave inicial para rotación",
            "valor_fijo": "clave-anterior",
        },
        headers=_headers(),
    )
    assert inicial.status_code == 201
    key_id = inicial.json()["clave"]["id"]

    rechazada = cliente.post(
        f"/admin/users/{usuario['id']}/api-keys/rotate",
        json={
            "key_id": key_id,
            "motivo": "rechazo por secreto demasiado corto",
            "valor_fijo": "ab",
        },
        headers=_headers(),
    )
    assert rechazada.status_code == 400
    assert admin.API_KEYS[key_id].revocada is False
    assert cliente.get(
        "/api/v3/bots", headers={"X-API-Key": "clave-anterior"}
    ).status_code == 200

    rotada = cliente.post(
        f"/admin/users/{usuario['id']}/api-keys/rotate",
        json={
            "key_id": key_id,
            "motivo": "rotación segura de clave por administrador",
            "valor_fijo": "clave-nueva",
        },
        headers=_headers(),
    )
    assert rotada.status_code == 200
    assert rotada.headers["cache-control"] == "private, no-store"
    cuerpo = rotada.json()
    assert cuerpo["valor_unica_vez"] == "clave-nueva"
    assert cuerpo["revocada"] == key_id
    assert admin.API_KEYS[key_id].revocada is True
    nueva = admin.API_KEYS[cuerpo["clave"]["id"]]
    assert nueva.scopes == ["jobs:create"]
    assert cliente.get(
        "/api/v3/bots", headers={"X-API-Key": "clave-anterior"}
    ).status_code == 401
    assert cliente.get(
        "/api/v3/bots", headers={"X-API-Key": "clave-nueva"}
    ).status_code == 200


def test_clave_publica_y_custodia_rsa(monkeypatch) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    monkeypatch.delenv("RSA_PRIVATE_KEY", raising=False)
    get_settings.cache_clear()
    cliente = TestClient(create_app())
    try:
        assert (
            cliente.get("/api/v3/seguridad/clave-publica").status_code == 503
        )

        privada = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = privada.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        monkeypatch.setenv("RSA_PRIVATE_KEY", pem)
        get_settings.cache_clear()

        respuesta = cliente.get("/api/v3/seguridad/clave-publica")
        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["algoritmo"] == "RSA-OAEP-SHA256"
        assert cuerpo["campo"] == "clave_encriptada"
        assert len(cuerpo["huella_sha256"]) == 64
        assert "BEGIN PUBLIC KEY" in cuerpo["clave_publica_pem"]

        # Guardar encriptado por la pública y desencriptar como el panel.
        from central_api.security.rsa_credentials import (
            decrypt_configured_credential,
            encrypt_configured_credential,
        )

        blob = encrypt_configured_credential("clave-fiscal-123")
        assert blob and isinstance(blob, str)
        assert decrypt_configured_credential(blob) == "clave-fiscal-123"

        # El esquema público documenta el transporte encriptado.
        esquema = cliente.get("/openapi.json").json()
        assert "/api/v3/seguridad/clave-publica" in esquema["paths"]
    finally:
        get_settings.cache_clear()


def test_alta_baja_worker_desde_panel(admin) -> None:
    from central_api.store import ADMIN_NODES

    cliente = TestClient(create_app())
    nodo = "127.0.0.1:9"
    try:
        alta = cliente.post(
            "/admin/workers", json={"node": nodo}, headers=_headers()
        )
        assert alta.status_code == 201
        cuerpo = alta.json()
        assert cuerpo["node"] == nodo
        # Puerto cerrado: la sonda falla rápido pero igual se registra.
        assert cuerpo["alcanzable"] is False

        flota = cliente.get("/admin/fleet", headers=_headers())
        assert flota.status_code == 200
        assert any(w["node"] == nodo for w in flota.json()["flota"])

        inventario = cliente.get("/admin/workers", headers=_headers())
        assert any(w["node"] == nodo for w in inventario.json()["workers"])

        invalido = cliente.post(
            "/admin/workers", json={"node": "sinnada"}, headers=_headers()
        )
        assert invalido.status_code == 400

        baja = cliente.delete(f"/admin/workers/{nodo}", headers=_headers())
        assert baja.status_code == 200
        flota2 = cliente.get("/admin/fleet", headers=_headers()).json()
        assert all(w["node"] != nodo for w in flota2["flota"])
    finally:
        ADMIN_NODES.discard(nodo)


def test_crear_usuario_reutiliza_uuid_pg(admin, monkeypatch) -> None:
    import central_api.admin.users as admin_users

    async def _fijo(email: str) -> str | None:
        return "11111111-2222-4333-8444-555555555555" if email == "abp" else None

    monkeypatch.setattr(admin_users, "_id_pg_por_email", _fijo)
    cliente = TestClient(create_app())
    try:
        respuesta = cliente.post(
            "/admin/users",
            json={"email": "abp", "motivo": "alta de depuración desde tests"},
            headers={"Authorization": "Bearer admin-debug-token"},
        )
        assert respuesta.status_code == 201
        assert (
            respuesta.json()["usuario"]["id"]
            == "11111111-2222-4333-8444-555555555555"
        )
    finally:
        admin_users.USERS.clear()


def test_email_debug_para_autoprovision(admin) -> None:
    import uuid as uuid_mod

    from central_api.admin.users import USERS
    from central_api.api.bots import _email_debug

    USERS["u1"] = type("U", (), {"email": "abp"})()
    try:
        uid = uuid_mod.uuid4()
        assert _email_debug("u1", uid) == "abp"
        assert _email_debug("nadie", uid) == f"debug-{uid.hex[:12]}"
    finally:
        USERS.pop("u1", None)


def test_panel_expone_tablas_claves_y_credenciales() -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")
    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    for vista in ("dashboard", "users", "keys", "jobs", "executions", "fleet", "audit"):
        assert f'data-view="{vista}"' in cuerpo
        assert f'data-panel="{vista}"' in cuerpo
    for endpoint in (
        "/admin/users",
        "/admin/api-keys",
        "/admin/jobs",
        "/admin/records",
        "/admin/table-catalog",
        "/admin/jobs/metrics",
        "/admin/fleet",
        "/admin/audit",
    ):
        assert endpoint in cuerpo
    assert "valor_fijo" in cuerpo
    assert "executions-job" in cuerpo
    assert "credential-detail" in cuerpo
    assert "Ver credenciales" in cuerpo
    assert "add-worker-form" in cuerpo
    assert "new-worker-node" in cuerpo
    assert "worker-remove" in cuerpo
    assert "Dar de baja" in cuerpo
    assert "/admin/workers" in cuerpo
