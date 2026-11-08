import asyncio
from datetime import datetime, timezone

import pytest

from bot_worker.scheduler import supervisor as module
from bot_worker.scheduler.supervisor import JobSupervisor, LocalJob, WorkerSaturated


def test_replay_window_preserves_active_and_valid_assignments(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr(module.time, "time", lambda: now)
    supervisor = JobSupervisor(configured=1)
    supervisor.MAX_ACCEPTANCES = 2

    def job(name, expiry):
        return LocalJob(
            name, 1, "lease", "bot", "op",
            assignment_expires_at=datetime.fromtimestamp(expiry, timezone.utc).isoformat(),
        )

    async def run():
        nonlocal now
        first = job("first", now + 20)
        body = {"accepted_at": "original"}
        await supervisor.accept(first, body)
        supervisor.release(first, "completado")
        second = job("second", now + 20)
        await supervisor.accept(second, {})
        supervisor.release(second, "completado")
        assert (await supervisor.accept(first, {})).duplicate
        assert supervisor.duplicate_body("first", 1, "lease") == body
        with pytest.raises(WorkerSaturated):
            await supervisor.accept(job("third", now + 20), {}, force=True)

        now += 79  # 20 seconds of validity plus the 60-second clock tolerance.
        with pytest.raises(WorkerSaturated):
            await supervisor.accept(job("third", now + 20), {})
        now += 2
        third = job("third", now - 61)
        await supervisor.accept(third, {})
        # Even an expired signature cannot evict an execution still running.
        now += 100
        assert (await supervisor.accept(third, {})).duplicate
        supervisor.release(third, "completado")
        await supervisor.accept(job("fourth", now + 20), {})
        assert supervisor.duplicate_body("third", 1, "lease") is None
        assert len(supervisor._acceptance_cache) == 1

    asyncio.run(run())


def test_replay_with_newer_expiry_extends_the_window(monkeypatch):
    now = 1_700_000_000.0
    monkeypatch.setattr(module.time, "time", lambda: now)
    supervisor = JobSupervisor(configured=1)

    async def run():
        nonlocal now
        first = LocalJob("job", 1, "lease", "bot", "op", assignment_expires_at=
                         datetime.fromtimestamp(now + 20, timezone.utc).isoformat())
        await supervisor.accept(first, {"original": True})
        supervisor.release(first, "completado")
        first.assignment_expires_at = datetime.fromtimestamp(now + 100, timezone.utc).isoformat()
        assert (await supervisor.accept(first, {})).duplicate
        now += 81
        assert (await supervisor.accept(first, {})).duplicate
        assert supervisor.duplicate_body("job", 1, "lease") == {"original": True}

    asyncio.run(run())
