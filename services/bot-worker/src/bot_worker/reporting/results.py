"""Reporte de resultado idempotente por (job_id, attempt).

Reintenta solo el transporte con la misma clave idempotente; nunca vuelve
a ejecutar el plugin por un fallo del callback.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

log = logging.getLogger("bot_worker.results")


def idempotency_key(job_id: str, attempt: int) -> str:
    return f"job:{job_id}:attempt:{attempt}"


class ResultStore:
    """Deduplica reportes por (job_id, attempt)."""

    def __init__(self) -> None:
        self._reported: dict[tuple[str, int], dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def already_reported(
        self, job_id: str, attempt: int
    ) -> dict[str, Any] | None:
        async with self._lock:
            return self._reported.get((job_id, attempt))

    async def mark_reported(
        self, job_id: str, attempt: int, body: dict[str, Any]
    ) -> None:
        async with self._lock:
            self._reported.setdefault((job_id, attempt), body)


async def send_result(
    client: Any,
    base_url: str,
    job_id: str,
    payload: dict[str, Any],
    timeout_seconds: float = 3.0,
    max_attempts: int = 4,
    worker_node: str = "",
    service_token: str = "",
) -> bool:
    """POST idempotente con retroceso; True si la central lo confirmo.

    Con ``worker_node`` viaja ``X-Worker-Node`` y con ``service_token`` el
    Bearer [REDACTED] registro (ambos en memoria, nunca en entorno).
    """
    delays = (1.0, 2.0, 4.0)
    url = f"{base_url.rstrip('/')}/internal/v1/jobs/{job_id}/result"
    headers: dict[str, str] = {}
    if worker_node:
        headers["X-Worker-Node"] = worker_node
    if service_token:
        headers["Authorization"] = f"Bearer {service_token}"
    extra: dict[str, Any] = {"headers": headers} if headers else {}
    for n in range(max_attempts):
        try:
            resp = await client.post(
                url, json=payload, timeout=timeout_seconds, **extra
            )
            if resp.status_code < 500:
                return True
        except Exception as exc:
            log.warning(
                "reporte de resultado intento %d fallido: %s",
                n + 1,
                type(exc).__name__,
            )
        if n < max_attempts - 1:
            await asyncio.sleep(delays[min(n, len(delays) - 1)])
    return False
