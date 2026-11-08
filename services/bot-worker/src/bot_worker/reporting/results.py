"""Reporte de resultado idempotente por (job_id, attempt).

Reintenta solo el transporte con la misma clave idempotente; nunca vuelve
a ejecutar el plugin por un fallo del callback.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any

log = logging.getLogger("bot_worker.results")


def idempotency_key(job_id: str, attempt: int) -> str:
    return f"job:{job_id}:attempt:{attempt}"


class ResultStore:
    """Deduplica reportes recientes sin retener sus cuerpos completos."""

    MAX_REPORTED = 4096

    def __init__(self) -> None:
        self._reported: set[tuple[str, int]] = set()
        self._order: deque[tuple[str, int]] = deque()
        self._lock = asyncio.Lock()

    async def already_reported(
        self, job_id: str, attempt: int
    ) -> bool:
        async with self._lock:
            return (job_id, attempt) in self._reported

    async def mark_reported(
        self, job_id: str, attempt: int
    ) -> None:
        async with self._lock:
            key = (job_id, attempt)
            if key in self._reported:
                return
            self._reported.add(key)
            self._order.append(key)
            if len(self._order) > self.MAX_REPORTED:
                self._reported.discard(self._order.popleft())


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
            if 200 <= resp.status_code < 300:
                try:
                    confirmation = resp.json()
                except (AttributeError, ValueError):
                    confirmation = None
                if (
                    isinstance(confirmation, dict)
                    and confirmation.get("success") is True
                    and confirmation.get("job_id") == job_id
                ):
                    return True
                log.warning(
                    "reporte de resultado sin confirmacion valida status=%s",
                    resp.status_code,
                )
            elif resp.status_code < 500 and resp.status_code not in (408, 425, 429):
                log.warning(
                    "reporte de resultado rechazado status=%s", resp.status_code
                )
                return False
        except Exception as exc:
            log.warning(
                "reporte de resultado intento %d fallido: %s",
                n + 1,
                type(exc).__name__,
            )
        if n < max_attempts - 1:
            await asyncio.sleep(delays[min(n, len(delays) - 1)])
    return False
