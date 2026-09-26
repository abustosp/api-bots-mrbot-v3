"""Pruebas del conteo operativo de jobs del dashboard V3."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.admin import jobs as admin_jobs  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402
from central_api.store import JOBS, WORKERS, Job, WorkerEntry, utcnow  # noqa: E402


def test_dashboard_solo_cuenta_como_corriendo_jobs_con_worker_vivo(monkeypatch) -> None:
    """Asignados, historial y CORRIENDO huérfanos no son ejecuciones activas."""
    monkeypatch.setenv("ADMIN_TOKEN", "metrics-admin-token")
    monkeypatch.setenv("WORKER_DOWN_AFTER_SECONDS", "90")
    get_settings.cache_clear()
    jobs_anteriores = dict(JOBS)
    workers_anteriores = dict(WORKERS)
    JOBS.clear()
    WORKERS.clear()
    try:
        ahora = utcnow()
        JOBS.update({
            "assigned": Job(
                "assigned", "ccma", "consultar", {},
                status="ASIGNADO", worker_node="fresh",
            ),
            "live": Job(
                "live", "ccma", "consultar", {},
                status="CORRIENDO", worker_node="fresh",
            ),
            "stale": Job(
                "stale", "ccma", "consultar", {},
                status="CORRIENDO", worker_node="stale",
            ),
            "orphan": Job(
                "orphan", "ccma", "consultar", {},
                status="CORRIENDO", worker_node=None,
            ),
            "done": Job("done", "ccma", "consultar", {}, status="COMPLETO"),
            "retry-history": Job(
                "retry-history", "ccma", "consultar", {},
                status="FALLIDO", assignment_attempt=3,
            ),
        })
        WORKERS.update({
            "fresh": WorkerEntry("fresh", last_heartbeat_at=ahora),
            "stale": WorkerEntry("stale", last_heartbeat_at=ahora - timedelta(minutes=10)),
        })
        monkeypatch.setattr(admin_jobs, "_conteo_estados_db", _sin_db)
        response = TestClient(create_app()).get(
            "/admin/jobs/metrics",
            headers={"Authorization": "Bearer metrics-admin-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["por_estado"] == {
            "ASIGNADO": 1,
            "CORRIENDO": 1,
            "COMPLETO": 1,
            "FALLIDO": 1,
        }
        assert body["en_ejecucion"] == 1
    finally:
        JOBS.clear()
        JOBS.update(jobs_anteriores)
        WORKERS.clear()
        WORKERS.update(workers_anteriores)
        get_settings.cache_clear()


async def _sin_db() -> None:
    return None


def test_dashboard_sql_filtra_corriendo_sin_lease_o_worker_fresco(monkeypatch) -> None:
    """El conteo DB condiciona CORRIENDO por lease y heartbeat reciente."""
    monkeypatch.setenv("ADMIN_TOKEN", "metrics-admin-token")
    monkeypatch.setenv("WORKER_DOWN_AFTER_SECONDS", "90")
    get_settings.cache_clear()
    statements: list[str] = []

    class Result:
        def all(self):
            # Ejemplo de la agregación tras excluir filas stale. Los estados no
            # operativos siguen visibles, sin sumarse a en_ejecucion.
            return [("ASIGNADO", 2), ("CORRIENDO", 0), ("FALLIDO", 4)]

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement):
            statements.append(str(statement))
            return Result()

    monkeypatch.setattr(admin_jobs, "db_configurado", lambda: True)
    monkeypatch.setattr(admin_jobs, "nueva_sesion", lambda: Session())
    try:
        response = TestClient(create_app()).get(
            "/admin/jobs/metrics",
            headers={"Authorization": "Bearer metrics-admin-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["fuente"] == "postgresql"
        assert body["por_estado"] == {"ASIGNADO": 2, "CORRIENDO": 0, "FALLIDO": 4}
        assert body["en_ejecucion"] == 0
        assert len(statements) == 1
        sql = statements[0]
        assert "jobs.lease_expires_at" in sql
        assert "workers.last_heartbeat_at" in sql
        assert "jobs.worker_id = workers.id" in sql
        assert "jobs.status !=" in sql
    finally:
        get_settings.cache_clear()
