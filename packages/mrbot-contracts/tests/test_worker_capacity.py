from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from mrbot_contracts.enums import WorkerStatus  # noqa: E402
from mrbot_contracts.worker import (  # noqa: E402
    ImageInfo,
    ResourceUsage,
    RuntimeInfo,
    SupportedBot,
    WorkerHeartbeat,
    WorkerNode,
    WorkerRegistration,
)

NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
WORKER_ID = "01890f3e-7a00-7abc-8def-123456789abc"
BOT = {"bot": "ccma", "manifest_version": "1.0.0", "operations": ["consultar"]}


def registration(capacity: int) -> WorkerRegistration:
    return WorkerRegistration(
        worker_id=WORKER_ID,
        instance_name="worker-test",
        started_at=NOW,
        image=ImageInfo(
            name="worker",
            digest="sha256:" + "0" * 64,
            app_version="3.0.0",
        ),
        supported_protocol_versions=[1],
        supported_bots=[SupportedBot(**BOT)],
        total_capacity=capacity,
        runtime=RuntimeInfo(
            python_version="3.12.0",
            playwright_version="1.0.0",
            browser_revision="test",
        ),
    )


def heartbeat(capacity: int, *, free: int | None = None) -> WorkerHeartbeat:
    return WorkerHeartbeat(
        worker_id=WORKER_ID,
        sent_at=NOW,
        state=WorkerStatus.READY,
        queued_jobs=0,
        total_capacity=capacity,
        free_capacity=capacity if free is None else free,
        image_version="3.0.0",
        image_digest="sha256:" + "0" * 64,
        protocol_version_supported=1,
        supported_bots=[SupportedBot(**BOT)],
        resources=ResourceUsage(
            cpu_percent=0,
            memory_bytes=1,
            memory_limit_bytes=2,
            disk_free_bytes=3,
            shm_free_bytes=4,
            event_loop_lag_ms=0,
            browser_processes=0,
        ),
    )


@pytest.mark.parametrize("capacity", range(6, 11))
def test_contracts_accept_worker_capacity_six_through_ten(capacity: int) -> None:
    assert registration(capacity).total_capacity == capacity
    assert heartbeat(capacity).total_capacity == capacity
    assert WorkerNode(
        worker_id=WORKER_ID,
        instance_name="worker-test",
        total_capacity=capacity,
    ).total_capacity == capacity


def test_contracts_reject_capacity_eleven() -> None:
    for model in (registration, heartbeat):
        with pytest.raises(ValidationError):
            model(11)
    with pytest.raises(ValidationError):
        heartbeat(10, free=11)
    with pytest.raises(ValidationError):
        WorkerNode(worker_id=WORKER_ID, instance_name="worker-test", total_capacity=11)
