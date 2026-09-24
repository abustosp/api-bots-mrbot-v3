"""Vuelta del scheduler: cota de claims fallidos y cesión de turno.

El hallazgo de auditoría era que un ``claim_and_reserve`` fallido no consumía
presupuesto de ronda: la vuelta repetía el mismo claim hasta
``scheduler_batch_size`` veces, de modo que una caída de PostgreSQL costaba
tantos round-trips fallidos por ciclo de polling. Estos tests fijan la cota y
comprueban que el camino feliz sigue agotando el lote.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.scheduler import loop as loop_mod  # noqa: E402


class _Ajustes:
    """Ajustes mínimos que consume ``scheduler_round``."""

    scheduler_batch_size = 10
    scheduler_max_empty_rounds = 2


def _job() -> SimpleNamespace:
    return SimpleNamespace(id="job-1", status="PENDIENTE", worker_node=None)


def test_round_acota_claims_fallidos_sin_worker(monkeypatch) -> None:
    """Sin worker con cupo la ronda cede el turno en vez de reintentar el lote."""
    intentos = []

    async def claim_fallido(job):
        intentos.append(job.id)
        return None, None, None, None

    monkeypatch.setattr(loop_mod, "get_settings", lambda: _Ajustes())
    monkeypatch.setattr(loop_mod, "claim_next_job", _job)
    monkeypatch.setattr(loop_mod, "claim_and_reserve", claim_fallido)

    outcome = asyncio.run(loop_mod.scheduler_round())

    assert outcome == {"asignados": 0, "vacíos": 0, "sin_claim": 2}
    assert len(intentos) == 2  # no los 10 del lote


def test_round_devuelve_el_job_a_pendiente(monkeypatch) -> None:
    """Un job no entregado no puede quedar marcado como asignado."""
    visto = {}
    job = _job()
    job.status = "ASIGNADO"
    job.worker_node = "worker-a"

    async def claim_fallido(candidato):
        visto["job"] = candidato
        return None, None, None, None

    monkeypatch.setattr(loop_mod, "get_settings", lambda: _Ajustes())
    monkeypatch.setattr(loop_mod, "claim_next_job", lambda: job)
    monkeypatch.setattr(loop_mod, "claim_and_reserve", claim_fallido)
    monkeypatch.setattr(
        loop_mod, "dispatch_claimed", lambda *a, **k: asyncio.sleep(0)
    )

    asyncio.run(loop_mod.scheduler_round())

    assert visto["job"] is job
    assert job.status == "PENDIENTE"
    assert job.worker_node is None


def test_round_agota_el_lote_cuando_hay_workers(monkeypatch) -> None:
    """Control: con entrega disponible la ronda sigue usando todo el lote."""
    entregas = []

    async def claim_ok(job):
        return SimpleNamespace(node="worker-a"), "token", "lease", None

    async def despacho(job, worker, token, lease_id="", expira=None):
        entregas.append(job.id)
        return "ASIGNADO"

    monkeypatch.setattr(loop_mod, "get_settings", lambda: _Ajustes())
    monkeypatch.setattr(loop_mod, "claim_next_job", _job)
    monkeypatch.setattr(loop_mod, "claim_and_reserve", claim_ok)
    monkeypatch.setattr(loop_mod, "dispatch_claimed", despacho)

    outcome = asyncio.run(loop_mod.scheduler_round())

    assert outcome == {"asignados": 10, "vacíos": 0, "sin_claim": 0}
    assert len(entregas) == 10


def test_round_duerme_si_la_cola_esta_vacia(monkeypatch) -> None:
    """Con la cola realmente vacía no se cuentan claims ni vacíos."""
    monkeypatch.setattr(loop_mod, "get_settings", lambda: _Ajustes())
    monkeypatch.setattr(loop_mod, "claim_next_job", lambda: None)
    monkeypatch.setattr(loop_mod, "has_schedulable_work", lambda: False)

    outcome = asyncio.run(loop_mod.scheduler_round())

    assert outcome == {"asignados": 0, "vacíos": 0, "sin_claim": 0}
