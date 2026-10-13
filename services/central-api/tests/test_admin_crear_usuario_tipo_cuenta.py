"""Alta de usuarios con tipo de cuenta (PRUEBA, TESTING o NORMAL con cupo)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402

H = {"Authorization": "Bearer admin-debug-token"}


@pytest.fixture
def admin(monkeypatch):
    from central_api.admin import users as admin_users
    from central_api.billing import entitlements

    monkeypatch.setenv("ADMIN_TOKEN", "admin-debug-token")
    get_settings.cache_clear()
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    entitlements.BILLING_ACCOUNTS.clear()
    yield admin_users
    admin_users.USERS.clear()
    admin_users.API_KEYS.clear()
    entitlements.BILLING_ACCOUNTS.clear()
    get_settings.cache_clear()


def _crear(cliente, email, **extra):
    cuerpo = {"email": email, "motivo": "alta de prueba desde tests", **extra}
    return cliente.post("/admin/users", json=cuerpo, headers=H)


def _cuenta(cliente, user_id):
    r = cliente.get(f"/admin/billing/accounts/{user_id}", headers=H)
    assert r.status_code == 200, r.text
    return r.json()["cuenta"]


def test_por_defecto_el_usuario_nace_en_prueba(admin):
    c = TestClient(create_app())
    r = _crear(c, "defecto@example.com")
    assert r.status_code == 201, r.text
    cuenta = _cuenta(c, r.json()["usuario"]["id"])
    assert cuenta["tipo_cuenta"]["account_type"] == "PRUEBA"
    assert cuenta["tipo_cuenta"]["max_requests"] == 100


def test_testing_es_ilimitada(admin):
    c = TestClient(create_app())
    r = _crear(c, "testing@example.com", account_type="TESTING")
    assert r.status_code == 201, r.text
    cuenta = _cuenta(c, r.json()["usuario"]["id"])
    assert cuenta["tipo_cuenta"]["account_type"] == "TESTING"
    assert cuenta["tipo_cuenta"]["ilimitado"] is True


def test_normal_usa_el_cupo_mensual_indicado(admin):
    c = TestClient(create_app())
    r = _crear(c, "normal@example.com", account_type="NORMAL", max_requests=2500)
    assert r.status_code == 201, r.text
    cuenta = _cuenta(c, r.json()["usuario"]["id"])
    assert cuenta["tipo_cuenta"]["account_type"] == "NORMAL"
    assert cuenta["tipo_cuenta"]["max_requests"] == 2500
    assert cuenta["tipo_cuenta"]["renovable"] is True


def test_normal_sin_cupo_se_rechaza_y_no_crea_el_usuario(admin):
    c = TestClient(create_app())
    r = _crear(c, "sincupo@example.com", account_type="NORMAL")
    assert r.status_code == 422
    assert "max_requests" in r.text
    assert not any(u.email == "sincupo@example.com" for u in admin.USERS.values())


@pytest.mark.parametrize("cupo", [0, -5, 10_000_001])
def test_cupo_fuera_de_rango_se_rechaza(admin, cupo):
    c = TestClient(create_app())
    assert _crear(c, f"rango{abs(cupo)}@example.com", account_type="NORMAL", max_requests=cupo).status_code == 422


def test_tipo_de_cuenta_invalido(admin):
    c = TestClient(create_app())
    assert _crear(c, "malo@example.com", account_type="FREE").status_code == 422


def test_el_campo_plan_obsoleto_se_ignora(admin):
    c = TestClient(create_app())
    r = _crear(c, "plan@example.com", plan="empresa")
    assert r.status_code == 201
    assert _cuenta(c, r.json()["usuario"]["id"])["tipo_cuenta"]["account_type"] == "PRUEBA"


def test_el_panel_ofrece_tipos_de_cuenta_y_no_los_planes_viejos():
    html = TestClient(create_app()).get("/admin/").text
    assert 'id="new-account-type"' in html
    for valor in ("PRUEBA", "TESTING", "NORMAL"):
        assert f'<option value="{valor}">' in html
    assert 'id="new-max-requests"' in html
    assert 'id="new-plan"' not in html
    for viejo in ("<option>free</option>", "<option>basico</option>", "<option>empresa</option>"):
        assert viejo not in html
