"""Sondeo HTTP de workers relay para verificar el tunel frp."""

from __future__ import annotations

import asyncio

import httpx

from central_api.store import RELAY_WORKERS, utcnow

INTERVALO_SONDEO_SEGUNDOS = 15
_TRANSPORT: httpx.AsyncBaseTransport | None = None
SONDEOS_RELAY: dict[str, dict[str, object]] = {}


def estado_sondeo(node: str) -> str | None:
    """Devuelve el estado de flota impuesto por los fallos del tunel."""
    fallos = int(SONDEOS_RELAY.get(node, {}).get("fallos_consecutivos", 0))
    if fallos >= 3:
        return "CAIDO"
    if fallos:
        return "DEGRADADO"
    return None


async def sondear_relays() -> None:
    """Actualiza el resultado del sondeo de todos los nodos relay."""
    nodos = list(RELAY_WORKERS)
    for node in SONDEOS_RELAY.keys() - RELAY_WORKERS.keys():
        del SONDEOS_RELAY[node]
    if not nodos:
        return

    async with httpx.AsyncClient(timeout=3.0, transport=_TRANSPORT) as cliente:
        respuestas = await asyncio.gather(
            *(cliente.get(f"http://{node}/internal/v1/health") for node in nodos),
            return_exceptions=True,
        )

    for node, respuesta in zip(nodos, respuestas, strict=True):
        anterior = SONDEOS_RELAY.get(node, {})
        if isinstance(respuesta, httpx.Response) and respuesta.is_success:
            SONDEOS_RELAY[node] = {"ultimo_ok": utcnow(), "fallos_consecutivos": 0}
        else:
            SONDEOS_RELAY[node] = {
                "ultimo_ok": anterior.get("ultimo_ok"),
                "fallos_consecutivos": int(anterior.get("fallos_consecutivos", 0)) + 1,
            }


async def relay_probe_loop() -> None:
    """Sondea los workers relay cada 15 segundos sin bloquear el event loop."""
    while True:
        await sondear_relays()
        await asyncio.sleep(INTERVALO_SONDEO_SEGUNDOS)
