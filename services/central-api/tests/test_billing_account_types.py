"""Pruebas de modos de cuenta y cuotas para la central API."""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.admin.audit import AUDIT_LOG  # noqa: E402
from central_api.admin.users import AdminUser, USERS  # noqa: E402
from central_api.billing.entitlements import (  # noqa: E402
    BILLING_ACCOUNTS,
    CREDIT_PRODUCTS,
    PLANS,
    PERIODS,
    TIER_PLAN_CODES,
    get_billing_account,
    set_billing_account,
)
from central_api.billing.reservation import (  # noqa: E402
    BALANCES,
    CREDIT_MOVES,
    USAGE,
    QuotaExhausted,
    confirm_usage,
    release_usage,
    reserve_for_job,
)
from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


def _id() -> str:
    return str(uuid.uuid4())


def test_catalog_prices_are_integer_and_unit_price_decreases() -> None:
    assert PLANS["free"].cuota_mensual == 100
    assert PLANS["gratuita"].cuota_mensual == 100
    tiers = [PLANS[code] for code in TIER_PLAN_CODES]
    assert [plan.cuota_mensual for plan in tiers] == [100, 500, 1000, 2500, 5000, 10000]
    assert [plan.precio_unitario_centavos for plan in tiers] == sorted(
        (plan.precio_unitario_centavos for plan in tiers), reverse=True
    )
    assert all(plan.precio_unitario_centavos < 20_000 for plan in tiers)
    assert all(
        plan.precio_centavos == plan.cuota_mensual * plan.precio_unitario_centavos
        for plan in tiers
    )
    assert CREDIT_PRODUCTS["pack-10"]["precio_centavos"] == 10 * 20_000
    assert CREDIT_PRODUCTS["pack-100"]["precio_centavos"] == 100 * 20_000


def test_creditos_mode_charges_one_request_and_needs_a_positive_balance() -> None:
    user_id = _id()
    account = set_billing_account(user_id, "CREDITOS")
    assert account.account_type == "CREDITOS"
    assert get_billing_account(user_id).account_type == "CREDITOS"
    with pytest.raises(QuotaExhausted):
        reserve_for_job(user_id, _id(), "portal_iva", "gestionar")

    BALANCES[user_id] = 2
    job_id = _id()
    entry = reserve_for_job(user_id, job_id, "portal_iva", "gestionar")
    assert entry.costo_snapshot == 1
    assert BALANCES[user_id] == 1
    assert release_usage(job_id).estado == "LIBERADO"
    assert BALANCES[user_id] == 2


def test_tier_monthly_quota_is_capped_at_plan_volume() -> None:
    user_id = _id()
    set_billing_account(user_id, "TIER", plan_code="tier-100")
    for _ in range(100):
        reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")
    period = PERIODS[user_id]
    assert period.cuota_asignada == 100
    assert period.cuota_reservada == 100
    with pytest.raises(QuotaExhausted):
        reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")


def test_gratuita_grants_100_requests_then_falls_back_to_creditos() -> None:
    user_id = _id()
    set_billing_account(
        user_id,
        "GRATUITA",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    for _ in range(100):
        reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")
    assert get_billing_account(user_id).account_type == "CREDITOS"
    assert PERIODS[user_id].cuota_asignada == 100
    with pytest.raises(QuotaExhausted):
        reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")


def test_expired_gratuita_falls_back_before_next_reservation() -> None:
    user_id = _id()
    account = set_billing_account(
        user_id,
        "GRATUITA",
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
    )
    account.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    with pytest.raises(QuotaExhausted):
        reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")
    assert get_billing_account(user_id).account_type == "CREDITOS"


def test_libre_enforces_manual_total_and_releases_reservations() -> None:
    user_id = _id()
    set_billing_account(user_id, "LIBRE", max_requests=2)
    first = reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")
    second = reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")
    with pytest.raises(QuotaExhausted):
        reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")
    release_usage(first.job_id)
    confirm_usage(second.job_id)
    third = reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")
    with pytest.raises(QuotaExhausted):
        reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")
    assert get_billing_account(user_id).requests_consumed == 1
    assert third.estado == "RESERVADO"


def test_admin_can_switch_each_mode_only_with_reason_and_audit(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_TOKEN", "billing-account-admin")
    get_settings.cache_clear()
    user_id = _id()
    USERS[user_id] = AdminUser(id=user_id, email="account@example.test")
    client = TestClient(create_app())
    headers = {"Authorization": "Bearer billing-account-admin"}
    try:
        missing_reason = client.post(
            f"/admin/billing/accounts/{user_id}/account-type",
            headers=headers,
            json={"account_type": "CREDITOS"},
        )
        assert missing_reason.status_code == 400

        changes = [
            {"account_type": "CREDITOS", "motivo": "Habilitar pago por consumo"},
            {"account_type": "TIER", "plan_code": "tier-500", "motivo": "Asignar plan de volumen"},
            {
                "account_type": "GRATUITA",
                "expires_at": (datetime.now(timezone.utc) + timedelta(days=15)).isoformat(),
                "motivo": "Conceder acceso gratuito temporal",
            },
            {"account_type": "LIBRE", "max_requests": 321, "motivo": "Habilitar entorno de pruebas"},
        ]
        for expected, body in zip(("CREDITOS", "TIER", "GRATUITA", "LIBRE"), changes):
            response = client.post(
                f"/admin/billing/accounts/{user_id}/account-type",
                headers=headers,
                json=body,
            )
            assert response.status_code == 200, response.text
            assert response.json()["cuenta"]["tipo_cuenta"]["account_type"] == expected
        events = [event for event in AUDIT_LOG if event.target_id == user_id]
        assert len([event for event in events if event.action == "billing.account_type.changed"]) == 4
        assert all(event.reason for event in events if event.action == "billing.account_type.changed")
        assert USERS[user_id].max_requests == 321
    finally:
        USERS.pop(user_id, None)
        BILLING_ACCOUNTS.pop(user_id, None)
        PERIODS.pop(user_id, None)
        for job_id in [job for job, entry in USAGE.items() if entry.user_id == user_id]:
            USAGE.pop(job_id, None)
        BALANCES.pop(user_id, None)
        CREDIT_MOVES[:] = [move for move in CREDIT_MOVES if move.user_id != user_id]
        AUDIT_LOG[:] = [event for event in AUDIT_LOG if event.target_id != user_id]
        get_settings.cache_clear()
