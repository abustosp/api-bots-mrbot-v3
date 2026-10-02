from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import math

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
    autoscale_avg_job_seconds=75,
    autoscale_boot_estimate_seconds=150,
    autoscale_target_work_seconds=300,
    autoscale_instance_type_large="",
    autoscale_boot_grace_seconds=240,
    autoscale_idle_seconds=45,
    autoscale_cooldown_seconds=60,
    autoscale_max_lifetime_seconds=7200,
)


def snapshot(**overrides):
    result = {
        "pendientes": 0,
        "en_ejecucion": 0,
        "capacidad_libre_fija": 0,
        "capacidad_total_fija": 0,
        "avg_job_seconds": 75,
        "boot_seconds": 150,
        "capacidad_nueva": 5,
        "instance_type": "c6i.xlarge",
        "efimeros": [],
        "edad_pendiente_mas_antiguo": None,
        "queue_pressure_seconds": 0,
        "last_action_at": None,
        "last_scale_up_pending": None,
    }
    result.update(overrides)
    return result


def ephemeral(
    instance_id="i-1",
    *,
    age=500,
    busy_age=None,
    jobs=0,
    healthy=True,
    state="running",
    never_registered=False,
    concurrency=5,
):
    return {
        "instance_id": instance_id,
        "node": "10.0.0.1:8080",
        "launched_at": NOW - timedelta(seconds=age),
        "jobs_activos": jobs,
        "last_busy_at": NOW - timedelta(seconds=busy_age) if busy_age is not None else None,
        "sano": healthy,
        "state": state,
        "never_registered": never_registered,
        "concurrency": concurrency,
    }


@pytest.mark.parametrize(
    ("data", "expected_up", "expected_down", "reason"),
    [
        (snapshot(), 0, [], "cola vacía"),
        (snapshot(pendientes=10, queue_pressure_seconds=60), 0, [], "umbral inclusivo"),
        (snapshot(pendientes=15, queue_pressure_seconds=30), 1, [], "un worker por horizonte"),
        (
            snapshot(
                pendientes=15,
                queue_pressure_seconds=30,
                last_action_at=NOW - timedelta(seconds=10),
            ),
            0,
            [],
            "cooldown no complementario",
        ),
        (
            snapshot(
                pendientes=100,
                queue_pressure_seconds=30,
                efimeros=[ephemeral(f"i-{n}", jobs=1) for n in range(4)],
            ),
            0,
            [],
            "max instances sin capacidad de escalar más",
        ),
        (
            snapshot(
                pendientes=20,
                queue_pressure_seconds=30,
                efimeros=[
                    ephemeral("boot", age=60, healthy=False, state="pending", never_registered=True)
                ],
            ),
            1,
            [],
            "slots booteando cuentan en S",
        ),
        (
            snapshot(
                pendientes=0,
                capacidad_libre_fija=5,
                capacidad_total_fija=5,
                efimeros=[ephemeral("idle", age=400, busy_age=46)],
            ),
            0,
            ["idle"],
            "ocioso al alcanzar 45 segundos",
        ),
        (
            snapshot(
                pendientes=0,
                capacidad_libre_fija=5,
                capacidad_total_fija=5,
                efimeros=[ephemeral("busy", age=400, busy_age=121, jobs=1)],
            ),
            0,
            [],
            "no drenar un worker ocupado sin cola",
        ),
        (
            snapshot(
                pendientes=0,
                efimeros=[
                    ephemeral("dead", age=500, healthy=False, never_registered=True)
                ],
            ),
            0,
            ["dead"],
            "dead man",
        ),
        (
            snapshot(
                pendientes=12,
                capacidad_libre_fija=5,
                capacidad_total_fija=5,
                efimeros=[ephemeral("needed", age=500, busy_age=200)],
            ),
            0,
            [],
            "no reducir slots necesarios para la cola",
        ),
    ],
)
def test_policy_table(data, expected_up, expected_down, reason):
    decision = decide(data, CFG, NOW)
    assert decision.scale_up == expected_up, reason
    assert decision.scale_down_ids == expected_down, reason


def test_queue_work_math_with_ten_fixed_free_slots_needs_no_worker():
    decision = decide(
        snapshot(
            pendientes=20,
            capacidad_libre_fija=10,
            capacidad_total_fija=10,
            queue_pressure_seconds=30,
        ),
        CFG,
        NOW,
    )
    assert decision.scale_up == 0
    assert decision.work_seconds == 1500
    assert decision.current_slots == 10
    assert decision.remaining_work_seconds == 0


@pytest.mark.parametrize("pending", [300, 664])
def test_large_queue_launches_all_needed_in_one_cycle_when_step_is_unlimited(pending):
    cfg = SimpleNamespace(**{**vars(CFG), "autoscale_step": 0, "autoscale_max_instances": 50})
    decision = decide(snapshot(pendientes=pending, queue_pressure_seconds=30), cfg, NOW)
    expected = math.ceil((pending * 75) / (5 * 300))
    assert decision.scale_up == expected and expected > 2
    assert decision.work_seconds == pending * 75
    assert decision.current_slots == 0


def test_configured_step_still_caps_launches_per_cycle_and_max_instances_caps_total():
    capped = SimpleNamespace(**{**vars(CFG), "autoscale_step": 2, "autoscale_max_instances": 50})
    assert decide(snapshot(pendientes=664, queue_pressure_seconds=30), capped, NOW).scale_up == 2
    maxed = SimpleNamespace(**{**vars(CFG), "autoscale_step": 0, "autoscale_max_instances": 4})
    assert decide(snapshot(pendientes=664, queue_pressure_seconds=30), maxed, NOW).scale_up == 4


def test_max_instances_caps_step_and_cooldown_allows_only_complementary_growth():
    maxed = SimpleNamespace(**{**vars(CFG), "autoscale_max_instances": 1})
    decision = decide(snapshot(pendientes=300, queue_pressure_seconds=30), maxed, NOW)
    assert decision.scale_up == 1

    complementary = decide(
        snapshot(
            pendientes=400,
            queue_pressure_seconds=30,
            last_action_at=NOW - timedelta(seconds=10),
            last_scale_up_pending=300,
            efimeros=[ephemeral("already-launched"), ephemeral("already-launched-2")],
        ),
        CFG,
        NOW,
    )
    assert complementary.scale_up == 2

    unchanged_queue = decide(
        snapshot(
            pendientes=300,
            queue_pressure_seconds=30,
            last_action_at=NOW - timedelta(seconds=10),
            last_scale_up_pending=300,
        ),
        CFG,
        NOW,
    )
    assert unchanged_queue.scale_up == 0


def test_large_instance_preferred_when_cost_per_slot_is_not_higher():
    decision = decide(
        snapshot(
            pendientes=300,
            queue_pressure_seconds=30,
            large_instance_type="c6i.2xlarge",
            capacidad_tipo_grande=10,
            rate_default=0.262,
            rate_large=0.524,
        ),
        CFG,
        NOW,
    )
    assert decision.scale_up == 2
    assert decision.instance_type == "c6i.2xlarge"


def test_scale_down_removes_lowest_load_workers_first_when_slots_are_surplus():
    decision = decide(
        snapshot(
            pendientes=20,
            capacidad_libre_fija=5,
            capacidad_total_fija=5,
            efimeros=[
                ephemeral("more-work", jobs=1),
                ephemeral("less-work", jobs=0),
            ],
        ),
        CFG,
        NOW,
    )
    assert decision.scale_up == 0
    assert decision.scale_down_ids == ["less-work"]


def test_boot_estimate_changes_queue_need():
    long_boot = decide(
        snapshot(
            pendientes=20,
            capacidad_libre_fija=5,
            capacidad_total_fija=5,
            boot_seconds=300,
            queue_pressure_seconds=30,
        ),
        CFG,
        NOW,
    )
    short_boot = decide(
        snapshot(
            pendientes=20,
            capacidad_libre_fija=5,
            capacidad_total_fija=5,
            boot_seconds=150,
            queue_pressure_seconds=30,
        ),
        CFG,
        NOW,
    )
    assert long_boot.scale_up == 0
    assert short_boot.scale_up == 1


# --- Modo por conteo: 1 worker cada N trabajos (AUTOSCALE_JOBS_PER_WORKER) ---

COUNT_CFG = SimpleNamespace(**{**vars(CFG), "autoscale_step": 0, "autoscale_max_instances": 50,
                               "autoscale_jobs_per_worker": 30, "autoscale_max_total_workers": 20})


@pytest.mark.parametrize(
    "pending,fixed,expected",
    [
        (0, 2, 0),      # sin cola
        (30, 2, 0),     # 1 worker alcanza y ya hay 2 fijos
        (60, 2, 0),     # total=2 == fijos
        (61, 2, 1),     # total=3 -> 1 efimero
        (300, 2, 8),    # total=10 -> 8 efimeros
        (664, 2, 18),   # total=23 -> tope 20 -> 18 efimeros
        (5000, 2, 18),  # el tope total manda
    ],
)
def test_count_mode_one_worker_per_30_jobs_capped_at_20_total(pending, fixed, expected):
    d = decide(snapshot(pendientes=pending, workers_fijos=fixed, queue_pressure_seconds=30), COUNT_CFG, NOW)
    assert d.scale_up == expected
    assert d.scale_down_ids == []


def test_count_mode_counts_booting_instances_so_it_never_relaunches():
    booting = [ephemeral(f"i-{n}", age=30, healthy=False) for n in range(10)]
    d = decide(snapshot(pendientes=664, workers_fijos=2, queue_pressure_seconds=30, efimeros=booting), COUNT_CFG, NOW)
    assert d.scale_up == 8  # objetivo 18, ya hay 10


def test_count_mode_waits_for_sustained_pressure_and_respects_threshold():
    d = decide(snapshot(pendientes=664, workers_fijos=2, queue_pressure_seconds=5), COUNT_CFG, NOW)
    assert d.scale_up == 0 and "sostenida" in d.reason
    d = decide(snapshot(pendientes=8, workers_fijos=0, queue_pressure_seconds=30), COUNT_CFG, NOW)
    assert d.scale_up == 0  # bajo el umbral de cola


def test_count_mode_drains_surplus_least_loaded_first_when_queue_shrinks():
    fleet = [ephemeral(f"i-{n}", jobs=n) for n in range(6)]
    d = decide(snapshot(pendientes=90, workers_fijos=2, queue_pressure_seconds=30, efimeros=fleet), COUNT_CFG, NOW)
    # 90/30=3 totales, 2 fijos => 1 efimero; sobran 5, los de menos carga primero
    assert d.scale_down_ids == ["i-0", "i-1", "i-2", "i-3", "i-4"]


def test_count_mode_off_keeps_the_time_based_formula():
    off = SimpleNamespace(**{**vars(COUNT_CFG), "autoscale_jobs_per_worker": 0})
    d = decide(snapshot(pendientes=300, queue_pressure_seconds=30), off, NOW)
    assert d.scale_up == min(math.ceil(300 * 75 / (5 * 300)), 50)
