from __future__ import annotations

import pytest

from bot_worker.config import WorkerConfig
from bot_worker.scheduler.supervisor import JobSupervisor


@pytest.mark.parametrize("concurrency", range(6, 11))
def test_worker_accepts_concurrency_six_through_ten(concurrency: int) -> None:
    config = WorkerConfig(
        central_url="http://central-api:8000",
        advertised_url="http://127.0.0.1:8080",
        worker_concurrency=concurrency,
        worker_local_queue_limit=10,
    )
    supervisor = JobSupervisor(configured=config.worker_concurrency, queue_limit=10)
    assert supervisor.capacity == concurrency
    assert supervisor.queue_limit == 10


def test_worker_rejects_concurrency_or_queue_limit_eleven() -> None:
    with pytest.raises(ValueError):
        WorkerConfig(
            central_url="http://central-api:8000",
            advertised_url="http://127.0.0.1:8080",
            worker_concurrency=11,
        )
    with pytest.raises(ValueError):
        WorkerConfig(
            central_url="http://central-api:8000",
            advertised_url="http://127.0.0.1:8080",
            worker_local_queue_limit=11,
        )
    with pytest.raises(ValueError):
        JobSupervisor(configured=11)
