"""El estado de workers relay refleja la disponibilidad del tunel frp."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))


class TransporteFalso(httpx.AsyncBaseTransport):
    def __init__(self, resultados: list[int | str]) -> None:
        self.resultados = iter(resultados)
        self.solicitudes: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.solicitudes.append(request)
        resultado = next(self.resultados)
        if resultado == "error":
            raise httpx.ConnectError("sin tunel", request=request)
        return httpx.Response(int(resultado), request=request)


@pytest.fixture
def relay(monkeypatch):
    from central_api import relay_probe
    from central_api.store import RELAY_WORKERS

    anteriores = dict(RELAY_WORKERS)
    sondeos_anteriores = dict(relay_probe.SONDEOS_RELAY)
    RELAY_WORKERS.clear()
    RELAY_WORKERS["relay:20001"] = {}
    relay_probe.SONDEOS_RELAY.clear()
    monkeypatch.setattr(relay_probe, "_TRANSPORT", None)
    yield relay_probe
    RELAY_WORKERS.clear()
    RELAY_WORKERS.update(anteriores)
    relay_probe.SONDEOS_RELAY.clear()
    relay_probe.SONDEOS_RELAY.update(sondeos_anteriores)


def test_sondeo_del_tunel_modifica_estado_y_se_recupera(relay, monkeypatch) -> None:
    from central_api.admin.workers import derive_state
    from central_api.store import WorkerEntry, utcnow

    transporte = TransporteFalso([200, 503, "error", 503, 200])
    monkeypatch.setattr(relay, "_TRANSPORT", transporte)
    entrada = WorkerEntry(node="relay:20001", last_heartbeat_at=utcnow())

    asyncio.run(relay.sondear_relays())
    ultimo_ok = relay.SONDEOS_RELAY[entrada.node]["ultimo_ok"]
    assert derive_state(entrada) == "SANO"

    asyncio.run(relay.sondear_relays())
    assert relay.SONDEOS_RELAY[entrada.node]["fallos_consecutivos"] == 1
    assert relay.SONDEOS_RELAY[entrada.node]["ultimo_ok"] == ultimo_ok
    assert derive_state(entrada) == "DEGRADADO"

    asyncio.run(relay.sondear_relays())
    asyncio.run(relay.sondear_relays())
    assert relay.SONDEOS_RELAY[entrada.node]["fallos_consecutivos"] == 3
    assert relay.SONDEOS_RELAY[entrada.node]["ultimo_ok"] == ultimo_ok
    assert derive_state(entrada) == "CAIDO"

    asyncio.run(relay.sondear_relays())
    assert relay.SONDEOS_RELAY[entrada.node]["fallos_consecutivos"] == 0
    assert derive_state(entrada) == "SANO"
    assert [str(s.url) for s in transporte.solicitudes] == [
        "http://relay:20001/internal/v1/health",
    ] * 5


def test_sondeo_no_cambia_estado_de_worker_no_relay(relay) -> None:
    from central_api.admin.workers import derive_state
    from central_api.store import WorkerEntry, utcnow

    relay.SONDEOS_RELAY["relay:20001"] = {
        "ultimo_ok": None,
        "fallos_consecutivos": 3,
    }
    entrada = WorkerEntry(node="directo:8080", last_heartbeat_at=utcnow())
    assert derive_state(entrada) == "SANO"
