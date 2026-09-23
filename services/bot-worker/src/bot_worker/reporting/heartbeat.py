"""Latidos hacia la central: cadencia nominal 10 s con jitter ±2 s (W-4).

El primer latido sale tras el arranque y despues de cada transicion
importante. Timeout de 3 s; ante falla, reintento con retroceso sin
bloquear la ejecucion. La central declara CAIDO tras 30 s sin latido.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

log = logging.getLogger("bot_worker.heartbeat")

START_MONO = time.monotonic()
START_WALL = time.time()


def _now_z() -> str:
    from datetime import datetime, timezone

    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        )
    )


def build_heartbeat_payload(
    *,
    worker_id: str,
    protocol_version: int,
    image_version: str,
    supervisor: Any,
    state: str,
    accepting_jobs: bool,
    sequence: int,
    oldest_queued_age_seconds: int = 0,
    instance_id: str = "",
) -> dict[str, Any]:
    running = [
        {
            "job_id": j.job_id,
            "attempt": j.attempt,
            "lease_id": j.lease_id,
            "started_at": j.started_at,
            "deadline_at": j.deadline_at,
        }
        for j in supervisor.items()
        if j.local_state != "EN_COLA"
    ]
    capacity = supervisor.capacity
    in_flight = supervisor.en_ejecucion
    queued = supervisor.en_cola
    resources = _resources()
    return {
        "protocol_version": protocol_version,
        "worker_id": worker_id,
        "instance_id": instance_id,
        "sequence": sequence,
        "sent_at": _now_z(),
        "state": state,
        "accepting_jobs": accepting_jobs,
        "capacity": capacity,
        "en_ejecucion": in_flight,
        "en_cola": queued,
        "reserved_assignments": 0,
        "available_capacity": max(0, capacity - in_flight - queued),
        "oldest_queued_age_seconds": oldest_queued_age_seconds,
        "running": running,
        "image": {
            "version": image_version,
            "playwright_version": _playwright_version(),
            "chromium_revision": "pinned-by-base-image",
        },
        "resources": resources,
        "counters_since_start": dict(supervisor.counters),
        "uptime_seconds": int(time.monotonic() - START_MONO),
        "bots_manifest_hash": _manifest_hash(),
    }


def _playwright_version() -> str:
    """Version instalada o marcador cuando la base aun no la provee."""
    try:
        from importlib.metadata import version

        return version("playwright")
    except Exception:
        return "provided-by-base-image"


def _resources() -> dict[str, Any]:
    """Memoria, /dev/shm, PIDs y CPU para distinguir la causa de falla."""
    import os

    mem_total, mem_avail = 0, 0
    try:
        with open("/proc/meminfo") as fh:
            info = {}
            for line in fh:
                parts = line.split()
                if len(parts) >= 2 and parts[0].endswith(":"):
                    info[parts[0][:-1]] = int(parts[1]) * 1024
        mem_total = info.get("MemTotal", 0)
        mem_avail = info.get("MemAvailable", 0)
    except OSError:
        pass
    mem_limit = _cgroup_memory_limit(0)
    shm_avail, shm_total = 0, 0
    try:
        st = os.statvfs("/dev/shm")
        shm_avail = st.f_bavail * st.f_frsize
        shm_total = st.f_blocks * st.f_frsize
    except OSError:
        pass
    pids_current = _read_int("/sys/fs/cgroup/pids.current")
    pids_limit = _read_int("/sys/fs/cgroup/pids.max")
    chromium = 0
    try:
        import subprocess

        out = subprocess.run(
            ["pgrep", "-c", "-f", "chrome"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if out.returncode == 0:
            chromium = int(out.stdout.strip())
    except Exception:
        chromium = 0
    return {
        "memory_available_bytes": mem_avail,
        "memory_current_bytes": max(0, mem_total - mem_avail),
        "memory_limit_bytes": mem_limit,
        "shm_available_bytes": shm_avail,
        "shm_total_bytes": shm_total,
        "chromium_processes": chromium,
        "pids_current": pids_current,
        "pids_limit": pids_limit,
        "cpu_count": os.cpu_count() or 0,
    }


def _read_int(path: str) -> int:
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return 0


def _cgroup_memory_limit(fallback: int) -> int:
    for path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            with open(path) as fh:
                text = fh.read().strip()
            if text != "max":
                return int(text)
        except (OSError, ValueError):
            continue
    return fallback


def _manifest_hash() -> str:
    try:
        from bot_worker.bots.registry import manifest_hash

        return manifest_hash()
    except Exception:  # pragma: no cover
        return "sha256:desconocido"


async def send_heartbeat(
    client: Any,
    base_url: str,
    worker_id: str,
    payload: dict[str, Any],
    service_token: str = "",
) -> bool:
    headers: dict[str, str] = (
        {"Authorization": f"Bearer {service_token}"} if service_token else {}
    )
    try:
        resp = await client.post(
            f"{base_url.rstrip('/')}/internal/v1/workers/{worker_id}/heartbeat",
            json=payload,
            headers=headers,
            timeout=3.0,
        )
        return resp.status_code < 500
    except Exception as exc:  # transporte: retrocede sin bloquear jobs
        log.warning("heartbeat fallido: %s", type(exc).__name__)
        return False


async def heartbeat_loop(
    *,
    client: Any,
    base_url: str,
    worker_id: str,
    protocol_version: int,
    image_version: str,
    supervisor: Any,
    interval: int = 10,
    jitter: int = 2,
    get_state: Any | None = None,
    stop: asyncio.Event | None = None,
    instance_id: str = "",
    service_token: str = "",
) -> None:
    """Bucle de latido con jitter y retroceso 2/5/10 s ante falla."""
    seq = 0
    backoffs = (0, 2, 5, 10)
    failures = 0
    stop = stop or asyncio.Event()
    while not stop.is_set():
        seq += 1
        state, accepting = (
            get_state() if get_state else ("SANO", not supervisor.draining)
        )
        payload = build_heartbeat_payload(
            worker_id=worker_id,
            protocol_version=protocol_version,
            image_version=image_version,
            supervisor=supervisor,
            state=state,
            accepting_jobs=accepting,
            sequence=seq,
            instance_id=instance_id,
        )
        ok = await send_heartbeat(
            client, base_url, worker_id, payload, service_token=service_token
        )
        failures = 0 if ok else failures + 1
        delay = backoffs[min(failures, len(backoffs) - 1)]
        wait = (interval if ok else delay or interval) + random.uniform(
            -jitter, jitter
        )
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.5, wait))
        except asyncio.TimeoutError:
            continue
