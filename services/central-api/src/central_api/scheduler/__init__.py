"""Scheduler: reclamo, selección, despacho push y reaper (plan 02 §6)."""

from central_api.scheduler.claim import (
    CLAIM_AND_RESERVE_SQL,
    CLAIM_NEXT_JOB_SQL,
    REAPER_EXPIRED_SQL,
)
from central_api.scheduler.selector import select_worker

__all__ = [
    "CLAIM_NEXT_JOB_SQL",
    "CLAIM_AND_RESERVE_SQL",
    "REAPER_EXPIRED_SQL",
    "dispatch_to_worker",
    "probe_worker",
    "select_worker",
    "scheduler_loop",
    "scheduler_round",
    "reap_expired",
    "record_success",
    "record_failure",
]


def __getattr__(name: str):  # lazy: módulos con httpx/async solo al usarse
    if name in ("dispatch_to_worker", "probe_worker", "list_worker_jobs"):
        from central_api.scheduler import dispatcher

        return getattr(dispatcher, name)
    if name in (
        "scheduler_loop", "scheduler_round", "claim_next_job",
        "claim_and_reserve", "has_schedulable_work",
    ):
        from central_api.scheduler import loop

        return getattr(loop, name)
    if name in (
        "reap_expired", "grant_lease", "renew_lease", "clear_lease",
        "expired_batch",
    ):
        from central_api.scheduler import reaper

        return getattr(reaper, name)
    if name in ("record_success", "record_failure", "record_heartbeat"):
        from central_api.scheduler import circuit_breaker

        return getattr(circuit_breaker, name)
    raise AttributeError(name)
