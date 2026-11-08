from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest

from central_api.internal import job_results


INFRASTRUCTURE_CATEGORIES = (
    "INFRASTRUCTURE_ERROR",
    "CHROMIUM_CRASHED",
    "WORKER_OOM",
    "ARTIFACT_UPLOAD_FAILED",
    "INTERNAL",
)


@pytest.mark.parametrize("result", job_results.TERMINAL_OK)
def test_resultado_correcto_no_se_reembolsa_por_categoria_interna(result: str) -> None:
    assert job_results._billing_status(result, "CHROMIUM_CRASHED") == "CONFIRMADO"


@pytest.mark.parametrize("category", INFRASTRUCTURE_CATEGORIES)
def test_resultado_de_falla_interna_reembolsa_cuota_en_memoria(
    monkeypatch, category: str
) -> None:
    job_id = str(uuid.uuid4())
    job = SimpleNamespace(
        id=job_id,
        bot="ccma",
        operation="consultar",
        status="CORRIENDO",
        result=None,
    )
    monkeypatch.setattr(job_results, "db_configurado", lambda: False)

    async def job_del_worker(*_args):
        return job

    monkeypatch.setattr(job_results, "job_para_worker", job_del_worker)
    monkeypatch.setattr(job_results.reaper_mod, "clear_lease", lambda *_args: None)

    import central_api.billing.reservation as reservation

    settlements: list[tuple[str, str]] = []
    monkeypatch.setattr(
        reservation,
        "confirm_usage",
        lambda value: settlements.append(("CONFIRMADO", value)),
    )
    monkeypatch.setattr(
        reservation,
        "refund_usage",
        lambda value, motivo: settlements.append(("REEMBOLSADO", value)),
    )

    asyncio.run(
        job_results.report_result(
            job_id,
            job_results.ResultBody(result="ERROR", error_category=category),
            "worker-test",
        )
    )

    assert settlements == [("REEMBOLSADO", job_id)]


@pytest.mark.parametrize(
    ("category", "expected"),
    [
        *[(category, "REEMBOLSADO") for category in INFRASTRUCTURE_CATEGORIES],
        ("TARGET_UNAVAILABLE", "CONFIRMADO"),
        ("CREDENTIALS_REJECTED", "CONFIRMADO"),
    ],
)
def test_persistencia_pg_aplica_la_misma_politica_de_cuota(
    monkeypatch, category: str, expected: str
) -> None:
    from central_api.billing import postgres
    from central_api.repositories import jobs as jobs_repository

    job_id = str(uuid.uuid4())
    session = _FakeSession()
    monkeypatch.setattr(job_results, "nueva_sesion", lambda: session)
    monkeypatch.setattr(jobs_repository, "JobRepository", _FakeJobRepository)
    settlements: list[str] = []

    async def settle(*_args, status: str, **_kwargs):
        settlements.append(status)

    monkeypatch.setattr(postgres, "settle_in_session", settle)

    asyncio.run(
        job_results._espejar_resultado_db(
            job_id,
            job_results.ResultBody(result="ERROR", error_category=category),
        )
    )

    assert settlements == [expected]


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _FakeJobRepository:
    def __init__(self, _session):
        pass

    async def persist_result(self, job_id, **_kwargs):
        return SimpleNamespace(id=job_id, status="FALLIDO"), True
