from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.autoscaler import controller  # noqa: E402
from central_api.settings import get_settings  # noqa: E402
from central_api.store import (  # noqa: E402
    ADMIN_NODES,
    EPHEMERAL_WORKERS,
    JOBS,
    WORKERS,
    Job,
    WorkerEntry,
    utcnow,
)


@dataclass
class FakeWorker:
    instance_id: str
    state: str
    public_ip: str | None
    private_ip: str | None = "10.0.0.9"
    launched_at: datetime | None = None
    az: str | None = "sa-east-1a"
    worker_port: int = 8080
    instance_type: str = "c6i.xlarge"
    ram_gib: float = 8.0
    concurrency: int = 5

    def __post_init__(self):
        self.launched_at = self.launched_at or utcnow()

    @property
    def node(self):
        return f"{self.public_ip}:{self.worker_port}" if self.public_ip else None


class FakeFleet:
    def __init__(self):
        self.workers: list[FakeWorker] = []
        self.launch_calls: list[int] = []
        self.terminate_calls: list[list[str]] = []
        self.settings = type("Settings", (), {
            "region": "sa-east-1",
            "instance_type": "c6i.xlarge",
            "worker_port": 8080,
            "worker_concurrency": 5,
            "max_lifetime_minutes": 120,
            "max_instances": 4,
            "project_tag": "unit-test-project",
        })()

    def launch(self, count: int):
        self.launch_calls.append(count)
        created = [
            FakeWorker(
                f"i-new-{len(self.workers) + index}",
                "pending",
                f"198.51.100.{20 + index}",
            )
            for index in range(count)
        ]
        self.workers.extend(created)
        return created

    def wait_until_running(self, workers, timeout):
        for worker in workers:
            worker.state = "running"
        return workers

    def list_managed(self, states=None):
        if states is None:
            return list(self.workers)
        return [worker for worker in self.workers if worker.state in states]

    def terminate(self, instance_ids):
        ids = list(instance_ids)
        self.terminate_calls.append(ids)
        for worker in self.workers:
            if worker.instance_id in ids:
                worker.state = "terminated"
        return ids

    def terminate_all(self):
        return self.terminate([worker.instance_id for worker in self.workers])

    def audit_orphans(self):
        return {"instances": [], "volumes": [], "eips": []}

    def estimate_cost(self, workers, now):
        return 0.0


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    saved = {
        "workers": dict(WORKERS),
        "jobs": dict(JOBS),
        "admin_nodes": set(ADMIN_NODES),
        "ephemeral": dict(EPHEMERAL_WORKERS),
        "boot_samples": list(controller.BOOT_SECONDS_SAMPLES),
    }
    WORKERS.clear()
    JOBS.clear()
    ADMIN_NODES.clear()
    EPHEMERAL_WORKERS.clear()
    for name, value in {
        "DATABASE_URL": "",
        "AUTOSCALE_ENABLED": "false",
        "AUTOSCALE_DRY_RUN": "false",
        "AUTOSCALE_QUEUE_THRESHOLD": "10",
        "AUTOSCALE_UP_AFTER_SECONDS": "0",
        "AUTOSCALE_MAX_INSTANCES": "4",
        "AUTOSCALE_STEP": "2",
        "AUTOSCALE_BOOT_GRACE_SECONDS": "240",
        "AUTOSCALE_IDLE_SECONDS": "120",
        "AUTOSCALE_INTERVAL_SECONDS": "1",
        "AUTOSCALE_COOLDOWN_SECONDS": "0",
        "AUTOSCALE_CAPACITY_PER_WORKER": "5",
        "AUTOSCALE_AVG_JOB_SECONDS": "75",
        "AUTOSCALE_BOOT_ESTIMATE_SECONDS": "150",
        "AUTOSCALE_TARGET_WORK_SECONDS": "300",
        "AUTOSCALE_INSTANCE_TYPE_LARGE": "",
        "WORKER_NODES": "",
    }.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    controller.set_fleet_for_testing(None)
    controller.RUNTIME_ENABLED = None
    controller.LAST_ACTION_AT = None
    controller.LAST_SCALE_UP_PENDING = None
    controller.QUEUE_PRESSURE_SINCE = None
    controller.DRAIN_TASK = None
    controller.AUTOSCALER_TASK = None
    controller.EVENTS.clear()
    controller.BOOT_SECONDS_SAMPLES.clear()
    yield
    try:
        asyncio.run(controller.shutdown_tasks())
    except RuntimeError:
        pass
    controller.set_fleet_for_testing(None)
    controller.RUNTIME_ENABLED = None
    controller.LAST_ACTION_AT = None
    controller.LAST_SCALE_UP_PENDING = None
    controller.QUEUE_PRESSURE_SINCE = None
    controller.DRAIN_TASK = None
    controller.AUTOSCALER_TASK = None
    controller.EVENTS.clear()
    controller.BOOT_SECONDS_SAMPLES.clear()
    controller.BOOT_SECONDS_SAMPLES.extend(saved["boot_samples"])
    WORKERS.clear()
    WORKERS.update(saved["workers"])
    JOBS.clear()
    JOBS.update(saved["jobs"])
    ADMIN_NODES.clear()
    ADMIN_NODES.update(saved["admin_nodes"])
    EPHEMERAL_WORKERS.clear()
    EPHEMERAL_WORKERS.update(saved["ephemeral"])
    get_settings.cache_clear()


def test_scale_up_registers_new_node_in_admin_inventory():
    fleet = FakeFleet()
    controller.set_fleet_for_testing(fleet)

    created = asyncio.run(controller.launch_workers(1, reason="prueba de provisionamiento"))

    assert fleet.launch_calls == [1]
    assert created[0]["instance_id"] == "i-new-0"
    assert created[0]["node"] in ADMIN_NODES
    assert EPHEMERAL_WORKERS["i-new-0"]["state"] == "running"
    assert EPHEMERAL_WORKERS["i-new-0"]["instance_type"] == "c6i.xlarge"
    assert EPHEMERAL_WORKERS["i-new-0"]["ram_gib"] == 8
    assert EPHEMERAL_WORKERS["i-new-0"]["concurrency"] == 5


def test_wait_until_running_registers_ip_before_any_worker_registration(monkeypatch):
    from central_api.internal import workers as internal_workers

    fleet = FakeFleet()
    worker = FakeWorker("i-order", "pending", None)
    order: list[str] = []
    fleet.launch = lambda count: [worker]

    def wait_until_running(workers, timeout):
        worker.state = "running"
        worker.public_ip = "198.51.100.55"
        order.append("wait_returned_ip")
        return workers

    fleet.wait_until_running = wait_until_running
    controller.set_fleet_for_testing(fleet)
    original_register = controller._register_instance

    def track_registration(item, *, adopted=False):
        if item.public_ip:
            order.append("inventory_registration")
            assert "wait_returned_ip" in order
        return original_register(item, adopted=adopted)

    monkeypatch.setattr(controller, "_register_instance", track_registration)

    created = asyncio.run(controller.launch_workers(1, reason="orden de registro"))
    result = asyncio.run(
        internal_workers.register_worker(
            internal_workers.RegisterBody(advertised_url=f"http://{worker.node}")
        )
    )

    assert order == ["wait_returned_ip", "inventory_registration"]
    assert worker.node in ADMIN_NODES
    assert EPHEMERAL_WORKERS[worker.instance_id]["node"] == worker.node
    assert result["accepted"] is True
    assert created[0]["instance_id"] == worker.instance_id


def test_scale_down_drains_first_and_waits_for_zero_active_jobs():
    fleet = FakeFleet()
    worker = FakeWorker("i-drain", "running", "198.51.100.30")
    fleet.workers.append(worker)
    controller.set_fleet_for_testing(fleet)
    node = worker.node
    ADMIN_NODES.add(node)
    EPHEMERAL_WORKERS[worker.instance_id] = {
        "instance_id": worker.instance_id,
        "node": node,
        "launched_at": utcnow(),
        "state": "running",
        "last_busy_at": utcnow(),
        "inventory_owned": True,
    }
    WORKERS[node] = WorkerEntry(
        node, status="SANO", running_jobs=1, capacity=5,
        last_heartbeat_at=utcnow(),
    )

    async def scenario():
        waiting = await controller.terminate_workers(
            [worker.instance_id], reason="prueba de drenaje"
        )
        assert waiting["draining"] == [worker.instance_id]
        assert fleet.terminate_calls == []
        assert WORKERS[node].status == "DRENANDO"
        assert node in ADMIN_NODES
        await controller.shutdown_tasks()
        WORKERS[node].running_jobs = 0
        return await controller.terminate_workers(
            [worker.instance_id], reason="prueba de drenaje"
        )

    result = asyncio.run(scenario())
    assert fleet.terminate_calls == [[worker.instance_id]]
    assert result["terminated"] == [worker.instance_id]
    assert worker.instance_id not in EPHEMERAL_WORKERS
    assert node not in ADMIN_NODES


def test_autoscaler_loop_survives_aws_exception(monkeypatch):
    fleet = FakeFleet()

    def broken_launch(count):
        fleet.launch_calls.append(count)
        raise RuntimeError("temporary AWS failure")

    fleet.launch = broken_launch
    controller.set_fleet_for_testing(fleet)
    monkeypatch.setenv("AUTOSCALE_ENABLED", "true")
    get_settings.cache_clear()
    calls = 0
    recovered = asyncio.Event()

    async def fail_once_then_recover():
        nonlocal calls
        calls += 1
        if calls == 1:
            await controller.launch_workers(1, reason="reintento de AWS", dry_run=False)
        recovered.set()

    async def no_reconcile():
        return []

    monkeypatch.setattr(controller, "autoscaler_tick", fail_once_then_recover)
    monkeypatch.setattr(controller, "reconcile_managed", no_reconcile)

    async def scenario():
        task = asyncio.create_task(controller.autoscaler_loop())
        try:
            await asyncio.wait_for(recovered.wait(), timeout=3)
            assert not task.done()
            assert calls >= 2
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())


def test_dry_run_does_not_call_launch(monkeypatch):
    fleet = FakeFleet()
    controller.set_fleet_for_testing(fleet)
    monkeypatch.setenv("AUTOSCALE_DRY_RUN", "true")
    monkeypatch.setenv("AUTOSCALE_QUEUE_THRESHOLD", "10")
    monkeypatch.setenv("AUTOSCALE_UP_AFTER_SECONDS", "0")
    get_settings.cache_clear()
    for index in range(11):
        job_id = f"pending-{index}"
        JOBS[job_id] = Job(job_id, "ccma", "consultar", {}, created_at=utcnow())

    decision = asyncio.run(controller.autoscaler_tick())

    assert decision.scale_up == 1
    assert fleet.launch_calls == []
    assert controller.event_list()[0]["dry_run"] is True
    assert {"W", "S", "R", "n", "reason"} <= controller.event_list()[0].keys()


def test_snapshot_measures_job_and_boot_averages_and_exposes_worker_metadata():
    from central_api.api.dependencies import JOB_META

    now = utcnow()
    saved_meta = dict(JOB_META)
    try:
        JOB_META.clear()
        for job_id, duration in (("done-60", 60), ("done-90", 90)):
            JOBS[job_id] = Job(
                job_id, "ccma", "consultar", {}, status="COMPLETO", created_at=now
            )
            JOB_META[job_id] = {
                "started_at": (now - timedelta(seconds=duration)).isoformat(),
                "finished_at": now.isoformat(),
            }

        node = "198.51.100.80:8080"
        launched_at = now - timedelta(seconds=120)
        ADMIN_NODES.add(node)
        WORKERS[node] = WorkerEntry(
            node, status="SANO", capacity=10, last_heartbeat_at=now
        )
        EPHEMERAL_WORKERS["i-metrics"] = {
            "instance_id": "i-metrics",
            "node": node,
            "launched_at": launched_at,
            "instance_type": "c6i.2xlarge",
            "ram_gib": 16,
            "concurrency": 10,
            "state": "running",
        }

        snapshot = asyncio.run(controller.build_snapshot(now))

        assert snapshot["avg_job_seconds"] == 75
        assert snapshot["boot_seconds"] == 120
        assert snapshot["efimeros"][0]["instance_type"] == "c6i.2xlarge"
        assert snapshot["efimeros"][0]["ram_gib"] == 16
        assert snapshot["efimeros"][0]["concurrency"] == 10
    finally:
        JOB_META.clear()
        JOB_META.update(saved_meta)


def test_admin_autoscale_exposes_effective_concurrency(monkeypatch):
    from central_api.admin import autoscale as admin_autoscale

    class FleetForAdmin:
        settings = type("Settings", (), {
            "region": "sa-east-1",
            "instance_type": "c6i.2xlarge",
            "worker_port": 8080,
            "max_lifetime_minutes": 120,
            "project_tag": "unit-test-project",
        })()

        def instance_runtime(self):
            return {
                "instance_type": "c6i.2xlarge",
                "ram_gib": 16.0,
                "worker_concurrency": 10,
            }

        def list_managed(self):
            return []

        def estimate_cost(self, workers, now):
            return 0.0

    fleet = FleetForAdmin()
    monkeypatch.setattr(admin_autoscale, "require_admin", lambda _authorization: None)
    monkeypatch.setattr(controller, "_make_fleet", lambda: fleet)
    monkeypatch.setattr(
        controller,
        "build_snapshot",
        lambda: asyncio.sleep(0, result={"avg_job_seconds": 80, "boot_seconds": 130}),
    )

    result = asyncio.run(admin_autoscale.get_autoscale())

    assert result["config"]["effective_worker_concurrency"] == 10
    assert result["config"]["worker_concurrency"] == 10
    assert result["config"]["ec2"]["worker_concurrency"] == 10
    assert result["snapshot"]["avg_job_seconds"] == 80
    assert result["snapshot"]["boot_seconds"] == 130


def test_reconcile_adopts_pending_worker_without_removing_registration_inventory():
    fleet = FakeFleet()
    worker = FakeWorker("i-boot", "pending", "198.51.100.40")
    fleet.workers.append(worker)
    controller.set_fleet_for_testing(fleet)

    adopted = asyncio.run(controller.reconcile_managed())

    assert adopted == [worker.instance_id]
    assert worker.node in ADMIN_NODES
    assert EPHEMERAL_WORKERS[worker.instance_id]["state"] == "pending"


def test_draining_worker_heartbeats_update_load_without_reentering_scheduler():
    from central_api.internal import workers as internal_workers

    node = "198.51.100.41:8080"
    now = utcnow()
    ADMIN_NODES.add(node)
    EPHEMERAL_WORKERS["i-drain-heartbeat"] = {
        "instance_id": "i-drain-heartbeat",
        "node": node,
        "launched_at": now,
        "state": "running",
        "drain_requested": True,
        "inventory_owned": True,
    }
    WORKERS[node] = WorkerEntry(
        node, status="DRENANDO", running_jobs=2, capacity=5, last_heartbeat_at=now
    )

    async def scenario():
        result = await internal_workers.worker_heartbeat(
            node,
            internal_workers.HeartbeatBody(status="SANO", capacity=5, en_ejecucion=0),
        )
        assert result["status"] == "DRENANDO"

    asyncio.run(scenario())
    assert WORKERS[node].status == "DRENANDO"
    assert WORKERS[node].running_jobs == 0
    assert EPHEMERAL_WORKERS["i-drain-heartbeat"]["registered_at"] is not None
    assert EPHEMERAL_WORKERS["i-drain-heartbeat"]["last_heartbeat_at"] is not None


def test_postgres_es_autoridad_de_jobs_activos_y_no_la_cache_desfasada(monkeypatch):
    """Hallazgo real: un job FALLIDO en la base seguia CORRIENDO en ``JOBS`` y la
    EC2 drenando nunca se terminaba (costo indefinido). Con base, manda la base."""
    from central_api.store import JOBS, Job

    node = "18.1.1.1:8080"
    EPHEMERAL_WORKERS["i-fantasma"] = {"node": node, "instance_id": "i-fantasma"}
    JOBS["job-fantasma"] = Job(
        id="job-fantasma", bot="mis_comprobantes", operation="consultar",
        payload={}, credentials={}, status="CORRIENDO", worker_node=node,
    )

    async def base_dice_cero():
        return {}

    monkeypatch.setattr(controller, "_db_active_by_node", base_dice_cero)
    try:
        assert asyncio.run(controller._active_jobs({"node": node})) == 0
        assert asyncio.run(controller._active_jobs_by_node()) == {node: 0}

        async def base_dice_dos():
            return {node: 2}

        monkeypatch.setattr(controller, "_db_active_by_node", base_dice_dos)
        assert asyncio.run(controller._active_jobs({"node": node})) == 2

        async def sin_base():
            return None

        monkeypatch.setattr(controller, "_db_active_by_node", sin_base)
        assert asyncio.run(controller._active_jobs({"node": node})) == 1  # sin base: cache
    finally:
        JOBS.pop("job-fantasma", None)
        EPHEMERAL_WORKERS.pop("i-fantasma", None)


def test_cuota_vcpu_agotada_se_registra_como_scale_up_short_y_no_como_exito(monkeypatch):
    """Hallazgo real: AWS devolvio 4 de 18 por VcpuLimitExceeded y la central
    registro 'scale_up_launched' 20 veces, ocultando que faltaban 14 workers."""
    eventos = []
    monkeypatch.setattr(controller, "_event", lambda action, **kw: eventos.append((action, kw)))

    class SinCuota(FakeFleet):
        last_launch_limit = "VcpuLimitExceeded"

        def launch(self, count, *a, **k):
            return []

    monkeypatch.setattr(controller, "_make_fleet", lambda: SinCuota())
    out = asyncio.run(controller.launch_workers(5, reason="t", dry_run=False))
    assert out == []
    acciones = [a for a, _ in eventos]
    assert "scale_up_short" in acciones and "scale_up_launched" not in acciones
    assert [kw for a, kw in eventos if a == "scale_up_short"][0]["limit"] == "VcpuLimitExceeded"
