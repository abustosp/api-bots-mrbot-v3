"""Forzado de ejecución desde el panel: único bypass del cupo.

Cubre que el selector omite capacidad solo con ``force``, que el sobre
forzado se firma bajo ``assign-force`` (y no verifica como ``assign``) y
que el endpoint hidrata, audita y despacha de inmediato.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


@pytest.fixture
def admin(monkeypatch):
    from central_api.store import ADMIN_NODES, JOBS, WORKERS

    monkeypatch.setenv("ADMIN_TOKEN", "admin-force-token")
    get_settings.cache_clear()
    j, w, a = dict(JOBS), dict(WORKERS), set(ADMIN_NODES)
    JOBS.clear()
    WORKERS.clear()
    ADMIN_NODES.clear()
    yield
    JOBS.clear()
    JOBS.update(j)
    WORKERS.clear()
    WORKERS.update(w)
    ADMIN_NODES.clear()
    ADMIN_NODES.update(a)
    get_settings.cache_clear()


def _headers() -> dict:
    return {"Authorization": "Bearer admin-force-token"}


def _obrero_lleno():
    from central_api.store import WorkerEntry, utcnow

    return WorkerEntry(
        node="10.0.0.9:8080",
        worker_id=str(uuid.uuid4()),
        status="SANO",
        capacity=1,
        running_jobs=1,
        last_heartbeat_at=utcnow(),
    )


def test_selector_solo_omite_cupo_con_force() -> None:
    from central_api.scheduler.selector import select_worker
    from central_api.store import Job

    trabajo = Job(id=str(uuid.uuid4()), bot="ccma", operation="consultar", payload={})
    obrero = _obrero_lleno()
    assert select_worker(trabajo, [obrero]) is None
    assert select_worker(trabajo, [obrero], force=True) is obrero


def test_sobre_forzado_firma_assign_force() -> None:
    from central_api.scheduler.dispatcher import build_envelope
    from central_api.security.assignments import (
        ASSIGNMENT_SCOPE_ASSIGN,
        ASSIGNMENT_SCOPE_ASSIGN_FORCE,
        verify_with_public_key,
    )
    from central_api.store import Job, WorkerEntry, new_job_id

    trabajo = Job(id=new_job_id(), bot="ccma", operation="consultar", payload={})
    obrero = WorkerEntry(node="10.0.0.9:8080")
    normal = build_envelope(trabajo, obrero, "tok-x")
    assert normal["force"] is False
    forzado = build_envelope(trabajo, obrero, "tok-x", force=True)
    assert forzado["force"] is True

    from central_api.security.assignments import process_signing_key, sealed_hash_of

    privada, _ = process_signing_key("")
    publica = privada.public_key()
    base = dict(
        job_id=trabajo.id,
        attempt=trabajo.assignment_attempt,
        lease_id="",
        expires_at=forzado["assignment_expires_at"],
        sealed_hash=sealed_hash_of(forzado["sealed_section"]),
    )
    assert verify_with_public_key(
        publica, scope=ASSIGNMENT_SCOPE_ASSIGN_FORCE,
        signature_b64=forzado["assignment_signature"], **base,
    ) is True
    assert verify_with_public_key(
        publica, scope=ASSIGNMENT_SCOPE_ASSIGN,
        signature_b64=forzado["assignment_signature"], **base,
    ) is False


def test_endpoint_force_despacha_y_audita(admin, monkeypatch) -> None:
    from central_api.scheduler import dispatcher
    from central_api.store import ADMIN_NODES, JOBS, WORKERS, Job

    async def _acepta(*args, **kwargs) -> bool:
        assert kwargs.get("force") is True
        return True

    monkeypatch.setattr(dispatcher, "dispatch_to_worker", _acepta)
    nodo = "10.0.0.9:8080"
    ADMIN_NODES.add(nodo)
    WORKERS[nodo] = _obrero_lleno()
    trabajo = Job(
        id=str(uuid.uuid4()),
        bot="ccma", operation="consultar", payload={}, status="PENDIENTE",
    )
    JOBS[trabajo.id] = trabajo

    cliente = TestClient(create_app())
    respuesta = cliente.post(
        f"/admin/jobs/{trabajo.id}/force",
        json={"motivo": "forzado de prueba desde tests"},
        headers=_headers(),
    )
    assert respuesta.status_code == 202
    cuerpo = respuesta.json()
    assert cuerpo["success"] is True
    assert cuerpo["estado"] == "ASIGNADO"
    assert cuerpo["worker"] == nodo
    assert cuerpo["forzado"] is True
    assert JOBS[trabajo.id].status == "ASIGNADO"

    auditoria = cliente.get("/admin/audit?limit=50", headers=_headers()).json()
    assert any(
        e["action"] == "job.force.requested" and e["target_id"] == trabajo.id
        for e in auditoria["eventos"]
    )


def test_endpoint_force_rechaza_terminal_y_sin_worker(admin) -> None:
    from central_api.store import ADMIN_NODES, JOBS, WORKERS, Job

    cliente = TestClient(create_app())
    terminado = Job(
        id=str(uuid.uuid4()), bot="ccma", operation="consultar",
        payload={}, status="COMPLETO",
    )
    JOBS[terminado.id] = terminado
    respuesta = cliente.post(
        f"/admin/jobs/{terminado.id}/force",
        json={"motivo": "forzado de prueba desde tests"},
        headers=_headers(),
    )
    assert respuesta.status_code == 409

    pendiente = Job(
        id=str(uuid.uuid4()), bot="ccma", operation="consultar",
        payload={}, status="PENDIENTE",
    )
    JOBS[pendiente.id] = pendiente
    assert not WORKERS and not ADMIN_NODES
    respuesta = cliente.post(
        f"/admin/jobs/{pendiente.id}/force",
        json={"motivo": "forzado de prueba desde tests"},
        headers=_headers(),
    )
    assert respuesta.status_code == 409


def test_panel_expone_boton_forzar() -> None:
    cliente = TestClient(create_app())
    cuerpo = cliente.get("/admin/").text
    assert 'data-action="job-force"' in cuerpo
    assert ">Forzar</button>" in cuerpo
    assert "/jobs/${encodeURIComponent(jobId)}/force" in cuerpo
