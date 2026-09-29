"""Renovación periódica de la lease que manda el worker (``heartbeat_hint``).

Motivo: la central renueva la lease del intento con cada evento válido, y el
reaper en PostgreSQL mira ``jobs.lease_expires_at``. Sin estos avisos, una
etapa del plugin que no emite progreso (descargas largas, armado de ZIP, espera
del portal) deja vencer la lease y el job se reencola o falla por
``lease_expired`` aunque el worker esté sano. Observado el 29/09/2026 con
``liquidacion_granos`` y ``retper_iibb_agip``.

La suite del proyecto no usa ``pytest-asyncio``: cada caso maneja su propio
``asyncio.run``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bot_worker import main as worker_main


class _ClienteFalso:
    def __init__(self) -> None:
        self.llamadas: list[dict[str, Any]] = []

    async def post(self, url: str, **kwargs: Any) -> None:
        self.llamadas.append({"url": url, **kwargs})


class _SettingsFalso:
    central_url = "http://central-api:8000"


class _AppFalsa:
    def __init__(self, cliente: Any, *, nodo: str = "bot-worker:8080") -> None:
        self.state = type("_Estado", (), {})()
        self.state.http = cliente
        self.state.settings = _SettingsFalso()
        self.state.worker_node = nodo
        self.state.service_token = ""


class _JobFalso:
    job_id = "01a0ed6d-409c-7e1a-91b2-fac04f64f6bc"
    attempt = 2


class _EnvFalso:
    assignment_token = "token-de-prueba"


def _esperar_hints(cliente: _ClienteFalso, cantidad: int, intentos: int = 100) -> None:
    async def escenario() -> None:
        tarea = asyncio.create_task(
            worker_main._renovar_lease_periodicamente(
                _AppFalsa(cliente), _EnvFalso(), _JobFalso()
            )
        )
        for _ in range(intentos):
            if len(cliente.llamadas) >= cantidad:
                break
            await asyncio.sleep(0.02)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea

    asyncio.run(escenario())


def test_renueva_con_heartbeat_hint_para_el_intento_vigente(monkeypatch) -> None:
    monkeypatch.setattr(worker_main, "_LEASE_RENEW_INTERVAL_SECONDS", 0.05)
    cliente = _ClienteFalso()
    _esperar_hints(cliente, cantidad=3)

    assert len(cliente.llamadas) >= 3
    eventos = [c["json"] for c in cliente.llamadas]
    assert {e["event_type"] for e in eventos} == {"heartbeat_hint"}
    assert {e["assignment_attempt"] for e in eventos} == {_JobFalso.attempt}
    assert {e["assignment_token"] for e in eventos} == {"token-de-prueba"}
    # Cada aviso necesita su propia clave: la central deduplica por
    # (job, intento, event_id) y un replay no renueva la lease.
    assert len({e["event_id"] for e in eventos}) == len(eventos)
    assert cliente.llamadas[0]["url"].endswith(
        f"/internal/v1/jobs/{_JobFalso.job_id}/events"
    )


def test_no_manda_nada_sin_cliente_http() -> None:
    app = _AppFalsa(_ClienteFalso())
    app.state.http = None

    async def escenario() -> None:
        await asyncio.wait_for(
            worker_main._renovar_lease_periodicamente(app, _EnvFalso(), _JobFalso()),
            timeout=0.5,
        )

    asyncio.run(escenario())


def test_cancelar_corta_el_lazo_sin_señal_de_error(monkeypatch) -> None:
    monkeypatch.setattr(worker_main, "_LEASE_RENEW_INTERVAL_SECONDS", 0.05)

    async def escenario() -> None:
        tarea = asyncio.create_task(
            worker_main._renovar_lease_periodicamente(
                _AppFalsa(_ClienteFalso()), _EnvFalso(), _JobFalso()
            )
        )
        await asyncio.sleep(0.12)
        tarea.cancel()
        with pytest.raises(asyncio.CancelledError):
            await tarea
        assert tarea.done()

    asyncio.run(escenario())
