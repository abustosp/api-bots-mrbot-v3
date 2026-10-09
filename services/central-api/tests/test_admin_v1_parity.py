"""Cobertura de paridad V1 para las rutas administrativas nuevas sin DB."""

from __future__ import annotations

from datetime import timedelta
import uuid

import pytest
from fastapi.testclient import TestClient

from central_api.admin.audit import AUDIT_LOG
from central_api.admin.users import API_KEYS, CREDIT_LEDGER, USERS, AdminUser
from central_api.api.dependencies import JOB_META
from central_api.main import create_app
from central_api.settings import get_settings
from central_api.store import JOBS, Job, utcnow


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ADMIN_TOKEN", "admin-v1-parity-token")
    get_settings.cache_clear()
    USERS.clear()
    API_KEYS.clear()
    CREDIT_LEDGER.clear()
    AUDIT_LOG.clear()
    JOBS.clear()
    JOB_META.clear()
    with TestClient(create_app()) as test_client:
        yield test_client
    USERS.clear()
    JOBS.clear()
    JOB_META.clear()
    get_settings.cache_clear()


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin-v1-parity-token"}


def _user() -> AdminUser:
    user = AdminUser(id=str(uuid.uuid4()), email="parity@example.test", estado="habilitado")
    USERS[user.id] = user
    return user


def test_bulk_monthly_limit_and_reports_keep_v1_routes_and_audit(client: TestClient) -> None:
    user = _user()

    bulk = client.post(
        "/admin/users/bulk",
        headers=_headers(),
        data={"action": "disable", "user_ids": user.id},
    )
    assert bulk.status_code == 200
    assert bulk.json()["updated"] == [user.id]
    assert USERS[user.id].estado == "deshabilitado"

    limit = client.post(
        f"/admin/users/{user.id}/monthly-limit",
        headers=_headers(),
        data={"maximas_consultas_mensuales": "123", "motivo": "ajuste V1"},
    )
    assert limit.status_code == 200
    assert limit.json()["maximas_consultas_mensuales"] == 123
    assert USERS[user.id].max_requests == 123

    job = Job(id="01900000-0000-7000-8000-000000000001", bot="consulta_cuit", operation="consultar", payload={}, status="COMPLETO", result={"result": "OK"})
    JOBS[job.id] = job
    JOB_META[job.id] = {"owner": user.id}
    # El rango se deriva de la fecha de hoy: el job de prueba se crea con
    # ``utcnow()``, y un rango fijo dejaba de contenerlo al pasar el dia.
    hoy = utcnow().date()
    report = client.get(
        "/admin/reporting",
        headers=_headers(),
        params={
            "desde": (hoy - timedelta(days=1)).isoformat(),
            "hasta": (hoy + timedelta(days=1)).isoformat(),
            "tipo": "resumen",
        },
    )
    assert report.status_code == 200
    assert report.json()["total_rows"] == 1
    csv = client.get("/admin/reports/download", headers=_headers(), params={"fecha_desde": "2026-10-01", "fecha_hasta": "2026-10-08"})
    assert csv.status_code == 200
    assert csv.headers["content-type"].startswith("text/csv")
    assert "requests_exitosas" in csv.text
    assert any(event.action == "reporting.downloaded" for event in AUDIT_LOG)


def test_clear_and_retention_purge_require_confirmation_and_keep_active_jobs(client: TestClient) -> None:
    user = _user()
    old_terminal = Job(
        id="01900000-0000-7000-8000-000000000002", bot="consulta_cuit", operation="consultar", payload={},
        status="COMPLETO", created_at=utcnow() - timedelta(days=120), result={"result": "OK"},
    )
    active = Job(
        id="01900000-0000-7000-8000-000000000003", bot="consulta_cuit", operation="consultar", payload={}, status="PENDIENTE",
    )
    JOBS.update({old_terminal.id: old_terminal, active.id: active})
    JOB_META.update({old_terminal.id: {"owner": user.id}, active.id: {"owner": user.id}})

    denied = client.post("/admin/tables/clear-consulta-logs", headers=_headers(), data={"confirmation_text": "no"})
    assert denied.status_code == 400
    preview = client.post("/admin/tables/purge-consulta-logs", headers=_headers(), json={"retention_days": 90, "batch_size": 10})
    assert preview.status_code == 200
    assert preview.json()["dry_run"] is True
    assert preview.json()["job_ids"] == [old_terminal.id]
    purged = client.post("/admin/retention/purge", headers=_headers(), json={"retention_days": 90, "dry_run": False, "motivo": "retención probada"})
    assert purged.status_code == 200
    assert old_terminal.id not in JOBS
    assert active.id in JOBS

    cleared = client.post("/admin/tables/clear-consulta-logs", headers=_headers(), data={"confirmation_text": "ELIMINAR"})
    assert cleared.status_code == 200
    assert active.id in JOBS
