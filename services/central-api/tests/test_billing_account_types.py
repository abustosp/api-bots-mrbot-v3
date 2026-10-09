"""Pruebas de los tres tipos de cuenta: PRUEBA, TESTING y NORMAL."""

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
    ACCOUNT_TYPES,
    BILLING_ACCOUNTS,
    PERIODS,
    account_summary,
    close_expired_periods,
    current_period,
    get_billing_account,
    month_bounds,
    normalize_assignment,
    set_billing_account,
)
from central_api.billing.reservation import (  # noqa: E402
    USAGE,
    QuotaExhausted,
    confirm_usage,
    refund_usage,
    release_usage,
    reserve_for_job,
)
from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


def _id() -> str:
    return str(uuid.uuid4())


def _reserve(user_id: str):
    return reserve_for_job(user_id, _id(), "consulta_cuit", "consultar")


def test_only_three_account_types_exist() -> None:
    assert ACCOUNT_TYPES == ("PRUEBA", "TESTING", "NORMAL")
    for legacy in ("CREDITOS", "TIER", "GRATUITA", "LIBRE", "free"):
        with pytest.raises(ValueError):
            normalize_assignment(legacy, 10)


def test_assignment_normalization() -> None:
    assert normalize_assignment("prueba", 5) == ("PRUEBA", 100)  # cupo fijo
    assert normalize_assignment("TESTING", 5) == ("TESTING", None)
    assert normalize_assignment("normal", 250) == ("NORMAL", 250)
    for bad in (None, 0, -1, 10_000_001):
        with pytest.raises(ValueError):
            normalize_assignment("NORMAL", bad)


def test_unassigned_user_defaults_to_prueba() -> None:
    user_id = _id()
    account = get_billing_account(user_id)
    assert (account.account_type, account.max_requests) == ("PRUEBA", 100)


def test_prueba_exhausted_becomes_normal_and_user_is_disabled() -> None:
    user_id = _id()
    USERS[user_id] = AdminUser(id=user_id, email="fin-prueba@example.test")
    try:
        set_billing_account(user_id, "PRUEBA")
        jobs = [_reserve(user_id).job_id for _ in range(100)]
        with pytest.raises(QuotaExhausted):
            _reserve(user_id)  # los 100 estan reservados
        for job_id in jobs[:99]:
            confirm_usage(job_id)
        assert get_billing_account(user_id).account_type == "PRUEBA"
        assert USERS[user_id].estado == "habilitado"
        confirm_usage(jobs[99])  # el ultimo cierra la prueba
        account = get_billing_account(user_id)
        assert account.account_type == "NORMAL"
        assert (account.max_requests, account.requests_consumed) == (100, 0)
        assert USERS[user_id].estado == "deshabilitado"
        assert USERS[user_id].account_type == "NORMAL"
        assert any(e.action == "billing.prueba.finished" and e.target_id == user_id
                   for e in AUDIT_LOG)
        # Rehabilitar es manual y ya no hay prueba que renovar.
        assert not close_expired_periods(datetime.now(timezone.utc) + timedelta(days=90))
    finally:
        USERS.pop(user_id, None)
        AUDIT_LOG[:] = [e for e in AUDIT_LOG if e.target_id != user_id]


def test_prueba_release_and_refund_return_the_slot() -> None:
    user_id = _id()
    set_billing_account(user_id, "PRUEBA")
    reservations = [_reserve(user_id) for _ in range(100)]
    with pytest.raises(QuotaExhausted):
        _reserve(user_id)
    assert release_usage(reservations[0].job_id).estado == "LIBERADO"
    assert refund_usage(reservations[1].job_id, "fallo de infraestructura").estado == "REEMBOLSADO"
    _reserve(user_id)
    _reserve(user_id)
    with pytest.raises(QuotaExhausted):
        _reserve(user_id)
    assert get_billing_account(user_id).requests_consumed == 0


def test_settlement_is_idempotent() -> None:
    user_id = _id()
    set_billing_account(user_id, "PRUEBA")
    entry = _reserve(user_id)
    assert confirm_usage(entry.job_id).estado == "CONFIRMADO"
    assert release_usage(entry.job_id).estado == "CONFIRMADO"  # no se revierte
    assert confirm_usage(entry.job_id).estado == "CONFIRMADO"
    account = get_billing_account(user_id)
    assert (account.requests_consumed, account.requests_reserved) == (1, 0)


def test_reservation_is_idempotent_per_job() -> None:
    user_id = _id()
    set_billing_account(user_id, "PRUEBA")
    job_id = _id()
    first = reserve_for_job(user_id, job_id, "consulta_cuit", "consultar")
    again = reserve_for_job(user_id, job_id, "consulta_cuit", "consultar")
    assert first is again
    assert get_billing_account(user_id).requests_reserved == 1


def test_testing_is_unlimited_but_still_counts_usage() -> None:
    user_id = _id()
    set_billing_account(user_id, "TESTING")
    for _ in range(250):
        entry = _reserve(user_id)
        confirm_usage(entry.job_id)
    account = get_billing_account(user_id)
    assert account.requests_consumed == 250
    summary = account_summary(account)
    assert summary["ilimitado"] is True
    assert summary["limite"] is None and summary["disponibles"] is None


def test_normal_enforces_monthly_quota_and_cancellation_returns_it() -> None:
    user_id = _id()
    set_billing_account(user_id, "NORMAL", max_requests=3)
    entries = [_reserve(user_id) for _ in range(3)]
    with pytest.raises(QuotaExhausted):
        _reserve(user_id)
    period = current_period(user_id)
    assert (period.cuota_asignada, period.cuota_reservada) == (3, 3)
    release_usage(entries[0].job_id)
    confirm_usage(entries[1].job_id)
    _reserve(user_id)
    with pytest.raises(QuotaExhausted):
        _reserve(user_id)
    assert (period.cuota_consumida, period.cuota_reservada) == (1, 2)


def test_normal_quota_renews_monthly() -> None:
    user_id = _id()
    set_billing_account(user_id, "NORMAL", max_requests=2)
    for _ in range(2):
        confirm_usage(_reserve(user_id).job_id)
    with pytest.raises(QuotaExhausted):
        _reserve(user_id)
    # Simula el paso al mes siguiente: el período abierto vence.
    old = PERIODS[user_id]
    old.fin = datetime.now(timezone.utc) - timedelta(seconds=1)
    old.inicio = old.fin - timedelta(days=30)
    for _ in range(2):  # cupo completo otra vez, sin intervención del admin
        confirm_usage(_reserve(user_id).job_id)
    with pytest.raises(QuotaExhausted):
        _reserve(user_id)
    assert PERIODS[user_id] is not old


def test_normal_quota_change_applies_to_current_month() -> None:
    user_id = _id()
    set_billing_account(user_id, "NORMAL", max_requests=1)
    confirm_usage(_reserve(user_id).job_id)
    with pytest.raises(QuotaExhausted):
        _reserve(user_id)
    set_billing_account(user_id, "NORMAL", max_requests=3)  # mismo tipo: conserva consumo
    assert current_period(user_id).cuota_consumida == 1
    _reserve(user_id)
    _reserve(user_id)
    with pytest.raises(QuotaExhausted):
        _reserve(user_id)


def test_changing_type_resets_counters() -> None:
    user_id = _id()
    set_billing_account(user_id, "PRUEBA")
    for _ in range(40):
        confirm_usage(_reserve(user_id).job_id)
    assert get_billing_account(user_id).requests_consumed == 40
    account = set_billing_account(user_id, "NORMAL", max_requests=5)
    assert (account.requests_consumed, account.requests_reserved) == (0, 0)
    for _ in range(5):
        _reserve(user_id)
    with pytest.raises(QuotaExhausted):
        _reserve(user_id)


def test_month_bounds_use_argentina_calendar_month() -> None:
    start, end = month_bounds(datetime(2026, 12, 15, 12, 0, tzinfo=timezone.utc))
    assert start == datetime(2026, 12, 1, 3, 0, tzinfo=timezone.utc)
    assert end == datetime(2027, 1, 1, 3, 0, tzinfo=timezone.utc)
    # 01:00 UTC del 1 de enero sigue siendo diciembre en Argentina (UTC-3).
    start, _ = month_bounds(datetime(2027, 1, 1, 1, 0, tzinfo=timezone.utc))
    assert start == datetime(2026, 12, 1, 3, 0, tzinfo=timezone.utc)


def test_admin_assigns_each_type_with_reason_and_audit(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_TOKEN", "billing-account-admin")
    get_settings.cache_clear()
    user_id = _id()
    USERS[user_id] = AdminUser(id=user_id, email="account@example.test")
    client = TestClient(create_app())
    headers = {"Authorization": "Bearer billing-account-admin"}
    url = f"/admin/billing/accounts/{user_id}/account-type"
    try:
        assert client.post(url, headers=headers, json={"account_type": "PRUEBA"}).status_code == 400
        assert client.post(
            url, headers=headers, json={"account_type": "PRUEBA", "motivo": "123456789"}
        ).status_code == 400
        for legacy in ("CREDITOS", "TIER", "GRATUITA", "LIBRE"):
            assert client.post(
                url, headers=headers,
                json={"account_type": legacy, "motivo": "tipo que ya no existe"},
            ).status_code == 422
        assert client.post(
            url, headers=headers,
            json={"account_type": "NORMAL", "motivo": "falta el cupo mensual"},
        ).status_code == 422

        changes = [
            {"account_type": "TESTING", "motivo": "Habilitar entorno de pruebas"},
            {"account_type": "NORMAL", "max_requests": 321, "motivo": "Cliente con cupo mensual"},
            {"account_type": "PRUEBA", "motivo": "Volver a cuenta de prueba"},
        ]
        for body in changes:
            response = client.post(url, headers=headers, json=body)
            assert response.status_code == 200, response.text
            cuenta = response.json()["cuenta"]["tipo_cuenta"]
            assert cuenta["account_type"] == body["account_type"]
        assert cuenta["max_requests"] == 100 and cuenta["renovable"] is False
        events = [e for e in AUDIT_LOG if e.target_id == user_id
                  and e.action == "billing.account_type.changed"]
        assert len(events) == 3 and all(e.reason for e in events)

        view = client.get(f"/admin/billing/accounts/{user_id}", headers=headers)
        assert view.status_code == 200
        assert view.json()["cuenta"]["tipo_cuenta"]["disponibles"] == 100
        # Ya no existen los endpoints de pagos, créditos ni conciliación.
        for gone in ("credit", "plan", "suspend", "period/force"):
            assert client.post(
                f"/admin/billing/accounts/{user_id}/{gone}", headers=headers, json={}
            ).status_code in (404, 405)
        for gone in ("reconcile/run", "webhooks/reprocess"):
            assert client.post(
                f"/admin/billing/{gone}", headers=headers, json={}
            ).status_code in (404, 405)
    finally:
        USERS.pop(user_id, None)
        BILLING_ACCOUNTS.pop(user_id, None)
        PERIODS.pop(user_id, None)
        for job_id in [j for j, e in USAGE.items() if e.user_id == user_id]:
            USAGE.pop(job_id, None)
        AUDIT_LOG[:] = [e for e in AUDIT_LOG if e.target_id != user_id]
        get_settings.cache_clear()


def test_public_account_endpoint_reports_quota(monkeypatch) -> None:
    from central_api.api.dependencies import require_api_principal
    from central_api.security.principals import ApiPrincipal

    monkeypatch.delenv("DATABASE_URL", raising=False)
    get_settings.cache_clear()
    user_id = _id()
    set_billing_account(user_id, "NORMAL", max_requests=10)
    confirm_usage(_reserve(user_id).job_id)
    _reserve(user_id)
    app = create_app()
    app.dependency_overrides[require_api_principal] = lambda: ApiPrincipal(
        user_id=user_id, key_id="k"
    )
    client = TestClient(app)
    body = client.get("/api/v3/mi/cuenta").json()
    assert body["tipo_cuenta"] == "NORMAL" and body["renovable"] is True
    assert (body["limite"], body["consumidas"], body["reservadas"], body["disponibles"]) == (10, 1, 1, 8)
    assert body["periodo"] is not None
    assert not {"saldo_creditos", "plan"} & set(body)
    set_billing_account(user_id, "TESTING")
    body = client.get("/api/v3/mi/cuenta").json()
    assert body["ilimitado"] is True and body["disponibles"] is None
    for gone in ("/api/v3/billing/plan", "/api/v3/billing/credit-products",
                 "/api/v3/billing/webhooks/mercadopago"):
        assert client.get(gone).status_code in (404, 405)
    get_settings.cache_clear()
