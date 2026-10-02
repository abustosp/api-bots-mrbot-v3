from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.autoscaler.policy import decide  # noqa: E402

NOW = datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)
CFG = SimpleNamespace(
    autoscale_queue_threshold=10,
    autoscale_up_after_seconds=20,
    autoscale_max_instances=4,
    autoscale_step=2,
    autoscale_capacity_per_worker=5,
    autoscale_boot_grace_seconds=240,
    autoscale_idle_seconds=120,
    autoscale_cooldown_seconds=60,
    autoscale_max_lifetime_seconds=7200,
)


def snapshot(**overrides):
    result = {
        "pendientes": 0,
        "en_ejecucion": 0,
        "capacidad_libre_fija": 0,
        "capacidad_total_fija": 0,
        "efimeros": [],
        "edad_pendiente_mas_antiguo": None,
        "queue_pressure_seconds": 0,
        "last_action_at": None,
    }
    result.update(overrides)
    return result


def ephemeral(instance_id="i-1", *, age=500, busy_age=None, jobs=0,
              healthy=True, state="running", never_registered=False):
    return {
        "instance_id": instance_id,
        "node": "10.0.0.1:8080",
        "launched_at": NOW - timedelta(seconds=age),
        "jobs_activos": jobs,
        "last_busy_at": NOW - timedelta(seconds=busy_age) if busy_age is not None else None,
        "sano": healthy,
        "state": state,
        "never_registered": never_registered,
    }


@pytest.mark.parametrize(
    ("data", "expected_up", "expected_down", "reason"),
    [
        (snapshot(), 0, [], "sin cola"),
        (snapshot(pendientes=10, capacidad_libre_fija=0, queue_pressure_seconds=60), 0, [], "umbral inclusivo"),
        (snapshot(pendientes=15, capacidad_libre_fija=0, queue_pressure_seconds=30), 2, [], "cola grande y sostenida"),
        (snapshot(pendientes=15, capacidad_libre_fija=0, queue_pressure_seconds=30,
                  last_action_at=NOW - timedelta(seconds=10)), 0, [], "cooldown"),
        (snapshot(pendientes=30, capacidad_libre_fija=0, queue_pressure_seconds=30,
                  efimeros=[ephemeral(f"i-{n}") for n in range(4)]), 0, [], "max instances"),
        (snapshot(pendientes=22, capacidad_libre_fija=0, queue_pressure_seconds=30,
                  efimeros=[ephemeral("boot", age=60, healthy=False, state="pending",
                                      never_registered=True)]), 2, [], "booting ya descontado"),
        (snapshot(pendientes=0, capacidad_libre_fija=5,
                  efimeros=[ephemeral("idle", age=400, busy_age=121)]), 0, ["idle"], "idle"),
        (snapshot(pendientes=0, capacidad_libre_fija=5,
                  efimeros=[ephemeral("busy", age=400, busy_age=121, jobs=1)]), 0, [], "no drenar activo"),
        (snapshot(pendientes=0, capacidad_libre_fija=0,
                  efimeros=[ephemeral("dead", age=500, healthy=False,
                                      never_registered=True)]), 0, ["dead"], "dead man"),
        (snapshot(pendientes=12, capacidad_libre_fija=5,
                  efimeros=[ephemeral("needed", age=500, busy_age=200)]), 0, [], "pending necesita capacidad"),
    ],
)
def test_policy_table(data, expected_up, expected_down, reason):
    decision = decide(data, CFG, NOW)
    assert decision.scale_up == expected_up, reason
    assert decision.scale_down_ids == expected_down, reason


def test_policy_respects_step_and_rounds_capacity_need_up():
    decision = decide(
        snapshot(pendientes=14, capacidad_libre_fija=3, queue_pressure_seconds=30),
        CFG,
        NOW,
    )
    # ceil((14 - 3) / 5) == 3, limited to AUTOSCALE_STEP=2.
    assert decision.scale_up == 2


def test_policy_waits_for_sustained_pressure_and_boot_grace():
    not_sustained = decide(
        snapshot(pendientes=20, queue_pressure_seconds=19), CFG, NOW
    )
    booting = decide(
        snapshot(
            pendientes=20,
            queue_pressure_seconds=30,
            efimeros=[ephemeral("boot", age=239, healthy=False,
                                state="pending", never_registered=True)],
        ),
        CFG,
        NOW,
    )
    assert not_sustained.scale_up == 0
    assert booting.scale_up == 2


def test_policy_deadman_and_max_lifetime_override_idle_demand():
    decision = decide(
        snapshot(
            pendientes=20,
            capacidad_libre_fija=0,
            queue_pressure_seconds=30,
            efimeros=[ephemeral("old", age=7201, jobs=1)],
        ),
        CFG,
        NOW,
    )
    assert decision.scale_down_ids == ["old"]
    assert decision.scale_up == 0
