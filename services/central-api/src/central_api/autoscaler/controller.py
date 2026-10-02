"""Controlador asyncio supervisado del autoscaler EC2.

El SDK y el adaptador EC2 se cargan únicamente al primer uso. Todas las
operaciones potencialmente bloqueantes del fleet van a ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timezone
from statistics import fmean
from typing import Any

from central_api.autoscaler.policy import Decision, decide
from central_api.db import db_configurado, nueva_sesion
from central_api.settings import get_settings
from central_api.store import ADMIN_NODES, EPHEMERAL_WORKERS, JOBS, WORKERS, utcnow
from central_api.worker_nodes import merge_nodes

log = logging.getLogger(__name__)
EVENTS: deque[dict] = deque(maxlen=200)
BOOT_SECONDS_SAMPLES: deque[float] = deque(maxlen=32)
FLEET: Any | None = None  # injectable in tests; production instance is lazy
RUNTIME_ENABLED: bool | None = None
LAST_ACTION_AT: datetime | None = None
LAST_SCALE_UP_PENDING: int | None = None
QUEUE_PRESSURE_SINCE: datetime | None = None
AUTOSCALER_TASK: asyncio.Task | None = None
DRAIN_TASK: asyncio.Task | None = None


def set_fleet_for_testing(fleet: Any | None) -> None:
    """Inyecta un FakeFleet, sin importar ni construir el adaptador AWS."""
    global FLEET
    FLEET = fleet


def effective_enabled() -> bool:
    return get_settings().autoscale_enabled if RUNTIME_ENABLED is None else RUNTIME_ENABLED


def set_enabled(enabled: bool) -> None:
    global RUNTIME_ENABLED
    RUNTIME_ENABLED = bool(enabled)


def start_loop_task() -> asyncio.Task:
    """Create the supervised task on runtime enable if startup was disabled."""
    global AUTOSCALER_TASK
    if AUTOSCALER_TASK is None or AUTOSCALER_TASK.done():
        AUTOSCALER_TASK = asyncio.create_task(autoscaler_loop())
    return AUTOSCALER_TASK


async def stop_loop_task() -> None:
    """Stop the loop without terminating EC2 instances."""
    global AUTOSCALER_TASK
    task = AUTOSCALER_TASK
    AUTOSCALER_TASK = None
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def shutdown_tasks() -> None:
    """Cancel background tasks only; deliberately leave EC2 instances untouched."""
    global DRAIN_TASK
    await stop_loop_task()
    task = DRAIN_TASK
    DRAIN_TASK = None
    if task is not None and task is not asyncio.current_task() and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def _event(action: str, *, reason: str = "", **details: Any) -> dict:
    entry = {
        "at": utcnow().isoformat(),
        "action": action,
        "reason": reason,
        **details,
    }
    EVENTS.appendleft(entry)
    log.info("autoscaler %s: %s", action, reason)
    try:
        from central_api.admin.audit import log_event

        log_event(
            f"autoscaler.{action}", actor_id="system:autoscaler",
            target_type="ec2-fleet", target_id="all", reason=reason,
            metadata={key: value for key, value in details.items()},
            actor_type="system",
        )
    except Exception:  # noqa: BLE001 - auditoría auxiliar no debe tumbar el control
        log.debug("no se pudo agregar evento de autoscaler a audit_log", exc_info=True)
    return entry


def event_list(limit: int = 100) -> list[dict]:
    return list(EVENTS)[: max(0, min(limit, EVENTS.maxlen or 200))]


def _make_fleet() -> Any:
    global FLEET
    if FLEET is None:
        # Import diferido a propósito: la central no necesita boto3 si el
        # autoscaler está deshabilitado.
        from central_api.autoscaler.ec2 import Ec2Fleet, Ec2Settings, Ec2Worker

        _ = Ec2Worker  # included in the lazy adapter import contract
        FLEET = Ec2Fleet(Ec2Settings.from_env())
    return FLEET


def _instance_id(worker: Any) -> str:
    return str(getattr(worker, "instance_id", "") or "")


def _worker_node(worker: Any) -> str:
    return str(getattr(worker, "node", "") or "")


def _parse_dt(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback or utcnow()


def _register_instance(worker: Any, *, adopted: bool = False) -> dict | None:
    instance_id = _instance_id(worker)
    if not instance_id:
        return None
    previous = EPHEMERAL_WORKERS.get(instance_id, {})
    node = _worker_node(worker) or str(previous.get("node") or "")
    launched_at = _parse_dt(
        getattr(worker, "launched_at", None),
        _parse_dt(previous.get("launched_at"), utcnow()),
    )
    if node:
        inventory_owned = bool(previous.get("inventory_owned", node not in ADMIN_NODES))
        if node not in ADMIN_NODES:
            ADMIN_NODES.add(node)
            inventory_owned = True
    else:
        inventory_owned = bool(previous.get("inventory_owned", False))
    state = str(getattr(worker, "state", "unknown") or "unknown").lower()
    try:
        ram_gib = float(getattr(worker, "ram_gib", previous.get("ram_gib", 0)) or 0)
    except (TypeError, ValueError):
        ram_gib = float(previous.get("ram_gib", 0) or 0)
    try:
        concurrency = int(getattr(worker, "concurrency", previous.get("concurrency", 5)) or 5)
    except (TypeError, ValueError):
        concurrency = int(previous.get("concurrency", 5) or 5)
    record = {
        **previous,
        "instance_id": instance_id,
        "node": node,
        "launched_at": launched_at,
        "instance_type": str(
            getattr(worker, "instance_type", previous.get("instance_type", "")) or ""
        ),
        "ram_gib": ram_gib,
        "concurrency": concurrency,
        "state": state,
        "last_busy_at": previous.get("last_busy_at"),
        "last_heartbeat_at": previous.get("last_heartbeat_at"),
        "registered_at": previous.get("registered_at"),
        "first_healthy_at": previous.get("first_healthy_at"),
        "inventory_owned": inventory_owned,
        "adopted": bool(previous.get("adopted", adopted)),
    }
    EPHEMERAL_WORKERS[instance_id] = record
    return record


def _is_running(state: str) -> bool:
    return state.strip().lower() in {"running", "sano", "healthy"}


def _list_managed_all_states(fleet: Any) -> list[Any]:
    """Ask for all meaningful EC2 states where the adapter supports it.

    The adapter's no-argument default returns only pending/running, so a
    stopped worker would otherwise be indistinguishable from an absent ID.
    """
    states = ("pending", "running", "stopping", "stopped", "shutting-down", "terminated")
    try:
        return fleet.list_managed(states=states)
    except TypeError:
        return fleet.list_managed()


def _drop_inventory(record: dict) -> None:
    node = str(record.get("node") or "")
    if node and record.get("inventory_owned"):
        fixed_nodes = set(get_settings().worker_node_list)
        if node not in fixed_nodes:
            ADMIN_NODES.discard(node)


def _remove_instance(instance_id: str) -> None:
    record = EPHEMERAL_WORKERS.pop(instance_id, None)
    if record is not None:
        _drop_inventory(record)
        node = str(record.get("node") or "")
        is_fixed = node in get_settings().worker_node_list
        shared_by_ephemeral = any(
            item.get("node") == node for item in EPHEMERAL_WORKERS.values()
        )
        if record.get("inventory_owned") and node and not is_fixed and not shared_by_ephemeral:
            WORKERS.pop(node, None)


async def reconcile_managed() -> list[str]:
    """Adopta lo que el adapter confirma como Project-managed al arrancar.

    La ausencia de un ID en ``list_managed`` no se interpreta como terminación:
    el adapter puede filtrar estados transitorios. Los estados no-running que
    sí vengan en la respuesta se desinventarian, pero nunca se terminan aquí.
    """
    fleet = _make_fleet()
    workers = await asyncio.to_thread(_list_managed_all_states, fleet)
    adopted: list[str] = []
    for worker in workers:
        record = _register_instance(worker, adopted=True)
        if record is None:
            continue
        adopted.append(record["instance_id"])
        state = record["state"].strip().lower()
        if state == "terminated":
            _remove_instance(record["instance_id"])
        elif state in {"stopping", "stopped", "shutting-down"}:
            _drop_inventory(record)
    _event("reconciled", reason="adopción segura de instancias administradas", instances=len(adopted))
    return adopted


async def _queue_metrics() -> tuple[int, int, datetime | None, str]:
    """Métricas equivalentes a /admin/jobs/metrics y fecha del PENDIENTE más viejo."""
    from central_api.admin.jobs import _conteo_estados_db

    counts = await _conteo_estados_db()
    oldest: datetime | None = None
    source = "postgresql" if counts is not None else "memoria"
    if counts is None:
        cutoff = utcnow().timestamp() - get_settings().worker_down_after_seconds
        counts = {}
        for job in JOBS.values():
            if job.status == "CORRIENDO":
                worker = WORKERS.get(job.worker_node or "")
                heartbeat = worker.last_heartbeat_at if worker else None
                if heartbeat is None or heartbeat.timestamp() < cutoff:
                    continue
            counts[job.status] = counts.get(job.status, 0) + 1
        pending_jobs = [job for job in JOBS.values() if job.status == "PENDIENTE"]
        if pending_jobs:
            oldest = min((job.created_at for job in pending_jobs), default=None)
    elif db_configurado():
        try:
            from sqlalchemy import func, select

            from central_api.models.execution import Job as PersistedJob

            async with nueva_sesion() as session:
                oldest = (await session.execute(
                    select(func.min(PersistedJob.created_at)).where(
                        PersistedJob.status == "PENDIENTE"
                    )
                )).scalar_one_or_none()
        except Exception as exc:  # noqa: BLE001 - next controller tick retries
            log.warning("no se pudo medir antigüedad de cola PG: %s", type(exc).__name__)
    return (
        int(counts.get("PENDIENTE", 0)),
        int(counts.get("CORRIENDO", 0)),
        oldest,
        source,
    )


async def _db_active_by_node() -> dict[str, int] | None:
    """Jobs ASIGNADO/CORRIENDO por worker segun PostgreSQL; ``None`` sin base o ante error."""
    if not db_configurado():
        return None
    try:
        from sqlalchemy import func, select

        from central_api.models.execution import Job as PersistedJob
        from central_api.models.fleet import Worker

        async with nueva_sesion() as session:
            rows = (await session.execute(
                select(Worker.endpoint, func.count(PersistedJob.id))
                .join(PersistedJob, PersistedJob.worker_id == Worker.id)
                .where(PersistedJob.status.in_(("ASIGNADO", "CORRIENDO")))
                .group_by(Worker.endpoint)
            )).all()
        return {str(node): int(count) for node, count in rows}
    except Exception as exc:  # noqa: BLE001 - se cae a la cache en memoria
        log.warning("no se pudieron consultar jobs activos de flota: %s", type(exc).__name__)
        return None


def _cache_active_by_node(nodes) -> dict[str, int]:
    return {
        node: sum(
            1 for job in JOBS.values()
            if job.worker_node == node and job.status in ("ASIGNADO", "CORRIENDO")
        )
        for node in nodes if node
    }


async def _active_jobs_by_node() -> dict[str, int]:
    """Con PostgreSQL la base es la autoridad: la cache ``JOBS`` puede quedar
    desfasada (p. ej. un job ya FALLIDO en la base que la cache sigue viendo
    CORRIENDO) y eso bloquearia para siempre la baja de una EC2 que cuesta
    dinero. Sin base se usa la cache."""
    nodes = [item.get("node") for item in EPHEMERAL_WORKERS.values()]
    canonical = await _db_active_by_node()
    if canonical is not None:
        return {node: canonical.get(node, 0) for node in nodes if node}
    return _cache_active_by_node(nodes)


def _elapsed_seconds(start: Any, end: Any) -> float | None:
    if start is None or end is None:
        return None
    try:
        start_at = _parse_dt(start)
        end_at = _parse_dt(end)
    except (TypeError, ValueError):
        return None
    seconds = (end_at - start_at).total_seconds()
    return seconds if seconds > 0 else None


async def _average_job_seconds(default: float) -> float:
    """Mean duration of up to 100 recent terminal jobs from PG or memory."""
    samples: list[float] = []
    if db_configurado():
        try:
            from sqlalchemy import select

            from central_api.models.execution import Job as PersistedJob

            async with nueva_sesion() as session:
                rows = (
                    await session.execute(
                        select(PersistedJob.started_at, PersistedJob.finished_at)
                        .where(
                            PersistedJob.status.in_(("COMPLETO", "FALLIDO", "CANCELADO")),
                            PersistedJob.started_at.is_not(None),
                            PersistedJob.finished_at.is_not(None),
                        )
                        .order_by(PersistedJob.finished_at.desc())
                        .limit(100)
                    )
                ).all()
            samples = [
                seconds
                for started_at, finished_at in rows
                if (seconds := _elapsed_seconds(started_at, finished_at)) is not None
            ]
        except Exception as exc:  # noqa: BLE001 - memory mirror is fallback
            log.warning("no se pudo medir duración media en PG: %s", type(exc).__name__)
    if not samples:
        try:
            from central_api.api.dependencies import JOB_META

            for job in JOBS.values():
                if job.status not in ("COMPLETO", "FALLIDO", "CANCELADO"):
                    continue
                meta = JOB_META.get(str(job.id), {})
                seconds = _elapsed_seconds(meta.get("started_at"), meta.get("finished_at"))
                if seconds is not None:
                    samples.append(seconds)
        except Exception:  # noqa: BLE001 - use configured default
            pass
    return fmean(samples[-100:]) if samples else float(default)


def _instance_policy_info(settings: Any) -> dict[str, Any]:
    """Return offline RAM/slot/cost hints for the pure policy."""
    from central_api.autoscaler.ec2 import (
        Ec2Fleet,
        Ec2Settings,
        INSTANCE_TYPE_RAM_GIB,
    )

    ec2 = Ec2Settings.from_env()
    base_type = ec2.instance_type
    large_type = str(settings.autoscale_instance_type_large or ec2.large_instance_type or "")
    base_ram = INSTANCE_TYPE_RAM_GIB.get(base_type, 8.0)
    base_slots = ec2.concurrency_for_type(base_type, base_ram)
    large_ram = INSTANCE_TYPE_RAM_GIB.get(large_type, 8.0) if large_type else 0.0
    large_slots = ec2.concurrency_for_type(large_type, large_ram) if large_type else 0
    rates = Ec2Fleet._ON_DEMAND_USD_HOUR_SA_EAST_1
    return {
        "instance_type": base_type,
        "capacidad_nueva": base_slots,
        "large_instance_type": large_type,
        "capacidad_tipo_grande": large_slots,
        "rate_default": rates.get(base_type, 0.0),
        "rate_large": rates.get(large_type, 0.0),
    }


async def build_snapshot(now: datetime | None = None) -> dict:
    now = now or utcnow()
    settings = get_settings()
    pending, running, oldest, source = await _queue_metrics()
    active_by_node = await _active_jobs_by_node()
    ephem_nodes = {str(info.get("node") or "") for info in EPHEMERAL_WORKERS.values()}
    fixed_nodes = [
        node for node in merge_nodes(settings.worker_node_list, ADMIN_NODES)
        if node and node not in ephem_nodes
    ]
    healthy_fixed: list[Any] = []
    fresh_cutoff = now.timestamp() - settings.worker_down_after_seconds
    for node in fixed_nodes:
        entry = WORKERS.get(node)
        if (
            entry is not None
            and entry.status == "SANO"
            and entry.last_heartbeat_at is not None
            and entry.last_heartbeat_at.timestamp() >= fresh_cutoff
        ):
            healthy_fixed.append(entry)
    fixed_total = sum(max(0, int(entry.capacity)) for entry in healthy_fixed)
    fixed_free = sum(
        max(0, int(entry.capacity) - int(entry.running_jobs) - int(entry.reserved_slots))
        for entry in healthy_fixed
    )
    avg_job_seconds = await _average_job_seconds(settings.autoscale_avg_job_seconds)
    ephemerals: list[dict] = []
    for instance_id, record in list(EPHEMERAL_WORKERS.items()):
        node = str(record.get("node") or "")
        entry = WORKERS.get(node) if node else None
        active = max(
            active_by_node.get(node, 0),
            (entry.running_jobs + entry.reserved_slots) if entry else 0,
        )
        if active > 0:
            record["last_busy_at"] = now
        heartbeat = entry.last_heartbeat_at if entry else None
        if heartbeat is not None:
            record["last_heartbeat_at"] = heartbeat
            record["registered_at"] = record.get("registered_at") or heartbeat
            if record.get("state") in {"pending", "booting", "unknown"}:
                record["state"] = "running"
        healthy = bool(
            entry is not None
            and entry.status == "SANO"
            and heartbeat is not None
            and heartbeat.timestamp() >= fresh_cutoff
        )
        if healthy and record.get("first_healthy_at") is None:
            healthy_at = heartbeat or now
            record["first_healthy_at"] = healthy_at
            boot_sample = _elapsed_seconds(record.get("launched_at"), healthy_at)
            if boot_sample is not None:
                BOOT_SECONDS_SAMPLES.append(boot_sample)
        concurrency = max(
            1,
            int(
                record.get("concurrency")
                or (entry.capacity if entry is not None else settings.autoscale_capacity_per_worker)
                or 5
            ),
        )
        ephemerals.append({
            "instance_id": instance_id,
            "node": node,
            "launched_at": record.get("launched_at"),
            "instance_type": record.get("instance_type", ""),
            "ram_gib": record.get("ram_gib", 0),
            "concurrency": concurrency,
            "jobs_activos": active,
            "last_busy_at": record.get("last_busy_at"),
            "sano": healthy,
            # Solo "nunca se registro" si jamas hubo registro ni latido. Un worker
            # SATURADO (a tope de trabajo) ya no es SANO pero SI se registro: sin
            # este dato la politica lo mataba como "fallo de arranque".
            "never_registered": record.get("registered_at") is None and heartbeat is None,
            "state": record.get("state", "unknown"),
        })
    instance_info = _instance_policy_info(settings)
    boot_seconds = (
        fmean(BOOT_SECONDS_SAMPLES)
        if BOOT_SECONDS_SAMPLES
        else float(settings.autoscale_boot_estimate_seconds)
    )
    return {
        "pendientes": pending,
        "en_ejecucion": running,
        "capacidad_libre_fija": fixed_free,
        "capacidad_total_fija": fixed_total,
        "workers_fijos": len(healthy_fixed),
        "avg_job_seconds": avg_job_seconds,
        "boot_seconds": boot_seconds,
        "efimeros": ephemerals,
        "edad_pendiente_mas_antiguo": max(0.0, (now - _parse_dt(oldest, now)).total_seconds()) if oldest else None,
        "fuente": source,
        "fixed_nodes": fixed_nodes,
        **instance_info,
    }


def _effective_max(settings: Any, fleet: Any) -> int:
    maximum = max(0, int(settings.autoscale_max_instances))
    # Ec2Settings puede imponer un segundo límite. Leerlo solo al usar AWS.
    try:
        from central_api.autoscaler.ec2 import Ec2Settings

        aws_max = int(Ec2Settings.from_env().max_instances)
        maximum = min(maximum, aws_max)
    except (ImportError, AttributeError, TypeError, ValueError):
        pass
    return maximum


def _effective_max_lifetime(settings: Any) -> int:
    seconds = max(0, int(settings.autoscale_max_lifetime_seconds))
    try:
        from central_api.autoscaler.ec2 import Ec2Settings

        seconds = min(seconds, int(Ec2Settings.from_env().max_lifetime_minutes) * 60)
    except (ImportError, AttributeError, TypeError, ValueError):
        pass
    return seconds


def _wait_until_running_and_register(fleet: Any, workers: list[Any], timeout: int) -> list[Any]:
    """Publish each assigned node immediately when its public IP becomes known."""
    running_workers = fleet.wait_until_running(workers, timeout)
    for worker in running_workers:
        # Do not perform logging, DB work, or another await between learning
        # the address and allowing the worker's first register request.
        _register_instance(worker)
    return running_workers


async def launch_workers(
    count: int,
    *,
    reason: str,
    dry_run: bool | None = None,
    instance_type: str | None = None,
    pending_at_decision: int | None = None,
    formula: dict[str, float | int] | None = None,
) -> list[dict]:
    """Lanza y registra instancias. La espera por EC2 se mantiene fuera del loop."""
    global LAST_ACTION_AT, LAST_SCALE_UP_PENDING
    settings = get_settings()
    count = max(0, int(count))
    fleet = _make_fleet()
    maximum = _effective_max(settings, fleet)
    remaining = max(0, maximum - len(EPHEMERAL_WORKERS))
    count = min(count, remaining)
    if count <= 0:
        _event("scale_up_skipped", reason="máximo de instancias alcanzado", maximum=maximum)
        return []
    if dry_run is None:
        dry_run = settings.autoscale_dry_run
    if dry_run:
        _event("dry_run", reason=reason, scale_up=count)
        return []
    if instance_type:
        try:
            workers = await asyncio.to_thread(
                fleet.launch, count, instance_type=instance_type
            )
        except TypeError as exc:
            if "instance_type" not in str(exc):
                raise
            workers = await asyncio.to_thread(fleet.launch, count)
    else:
        workers = await asyncio.to_thread(fleet.launch, count)
    records = [record for worker in workers if (record := _register_instance(worker)) is not None]
    limit = getattr(fleet, "last_launch_limit", None)
    if limit or len(workers) < count:
        # Pedimos ``count`` y AWS entrego menos (cuota vCPU o capacidad): no es un exito.
        _event(
            "scale_up_short",
            reason=f"se pidieron {count} y AWS entrego {len(workers)}" + (f" ({limit})" if limit else ""),
            requested=count, launched=len(workers), limit=limit,
        )
    if not workers:
        return []
    if workers:
        LAST_ACTION_AT = utcnow()
        LAST_SCALE_UP_PENDING = (
            int(pending_at_decision) if pending_at_decision is not None else None
        )
    _event(
        "scale_up_launched",
        reason=reason,
        instances=[r["instance_id"] for r in records],
        instance_type=instance_type,
        **(formula or {}),
    )
    # launch() puede devolver nodos sin IP hasta que estén running. Esperar
    # fuera del event loop y reinsertar los datos más recientes.
    if workers:
        try:
            await asyncio.to_thread(
                _wait_until_running_and_register,
                fleet,
                workers,
                settings.autoscale_boot_grace_seconds,
            )
        except Exception as exc:  # noqa: BLE001 - el siguiente ciclo reconcilia
            _event("wait_failed", reason=type(exc).__name__)
    return records


async def _active_jobs(record: dict) -> int:
    node = str(record.get("node") or "")
    if not node:
        return 0
    canonical = await _db_active_by_node()
    if canonical is not None:
        # Base como autoridad; el heartbeat del worker solo puede sumar (no restar).
        entry = WORKERS.get(node)
        heartbeat = entry.running_jobs if entry else 0
        return max(canonical.get(node, 0), heartbeat)
    entry = WORKERS.get(node)
    count = entry.running_jobs + entry.reserved_slots if entry else 0
    return max(count, _cache_active_by_node([node]).get(node, 0))


async def _mark_draining(record: dict) -> None:
    node = str(record.get("node") or "")
    record["drain_requested"] = True
    entry = WORKERS.get(node)
    if entry is not None:
        entry.status = "DRENANDO"
    if db_configurado() and node:
        try:
            from sqlalchemy import update

            from central_api.models.fleet import Worker

            async with nueva_sesion() as session:
                await session.execute(
                    update(Worker).where(Worker.endpoint == node).values(status="DRENANDO")
                )
                await session.commit()
        except Exception as exc:  # noqa: BLE001 - la marca local detiene dispatch in-memory
            log.warning("no se pudo persistir DRENANDO para %s: %s", node, type(exc).__name__)


async def terminate_workers(instance_ids: list[str], *, reason: str) -> dict:
    """Drena primero, y retira el nodo solo al observar EC2 ``terminated``."""
    global LAST_ACTION_AT
    known = [iid for iid in dict.fromkeys(instance_ids) if iid in EPHEMERAL_WORKERS]
    if not known:
        return {"terminated": [], "draining": [], "ignored": list(instance_ids)}
    for iid in known:
        if not EPHEMERAL_WORKERS[iid].get("drain_requested"):
            await _mark_draining(EPHEMERAL_WORKERS[iid])
    ready: list[str] = []
    draining: list[str] = []
    for iid in known:
        record = EPHEMERAL_WORKERS[iid]
        if await _active_jobs(record) == 0:
            ready.append(iid)
        else:
            draining.append(iid)
    fleet = _make_fleet()
    to_request = [
        iid for iid in ready
        if not EPHEMERAL_WORKERS[iid].get("termination_requested")
    ]
    accepted: set[str] = set()
    if to_request:
        try:
            accepted = {
                str(iid) for iid in
                (await asyncio.to_thread(fleet.terminate, to_request) or [])
            }
        except Exception as exc:  # noqa: BLE001 - retain inventory and retry next cycle
            _event("terminate_failed", reason=type(exc).__name__, instances=to_request)
            _ensure_drain_monitor()
            return {"terminated": [], "draining": draining, "pending": ready,
                    "error": type(exc).__name__}
        for iid in accepted:
            if iid in EPHEMERAL_WORKERS:
                EPHEMERAL_WORKERS[iid]["termination_requested"] = True
        if accepted:
            LAST_ACTION_AT = utcnow()
            _event("termination_requested", reason=reason, instances=sorted(accepted))

    awaiting_confirmation = [
        iid for iid in ready
        if EPHEMERAL_WORKERS[iid].get("termination_requested")
    ]
    state_by_id: dict[str, str] = {}
    if awaiting_confirmation:
        try:
            managed = await asyncio.to_thread(_list_managed_all_states, fleet)
            state_by_id = {
                _instance_id(worker): str(getattr(worker, "state", "unknown")).lower()
                for worker in managed
            }
        except Exception as exc:  # noqa: BLE001 - accepted termination remains pending
            _event("termination_confirmation_failed", reason=type(exc).__name__,
                   instances=awaiting_confirmation)

    removed: list[str] = []
    pending_confirmation: list[str] = []
    for iid in awaiting_confirmation:
        state = state_by_id.get(iid)
        if state == "terminated":
            _remove_instance(iid)
            removed.append(iid)
        else:
            record = EPHEMERAL_WORKERS.get(iid)
            if record is not None and state in {"stopping", "stopped", "shutting-down"}:
                record["state"] = state
                _drop_inventory(record)
            pending_confirmation.append(iid)
    if removed:
        _event("terminated", reason=reason, instances=removed)
    if draining:
        _event("draining", reason=reason, instances=draining)
    if pending_confirmation or draining:
        _ensure_drain_monitor()
    ignored = [iid for iid in ready if iid not in awaiting_confirmation]
    return {"terminated": removed, "draining": draining,
            "pending": pending_confirmation, "ignored": ignored}


async def _process_draining() -> None:
    requested = [
        iid for iid, record in EPHEMERAL_WORKERS.items()
        if record.get("drain_requested")
    ]
    if requested:
        await terminate_workers(requested, reason="drenaje previamente solicitado")


def _ensure_drain_monitor() -> None:
    global DRAIN_TASK
    if DRAIN_TASK is None or DRAIN_TASK.done():
        DRAIN_TASK = asyncio.create_task(_drain_monitor_loop())


async def _drain_monitor_loop() -> None:
    """Finish requested drains even while automatic scaling is disabled."""
    settings = get_settings()
    try:
        while any(item.get("drain_requested") for item in EPHEMERAL_WORKERS.values()):
            try:
                await _process_draining()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - retain records and retry
                _event("drain_tick_failed", reason=type(exc).__name__)
            await asyncio.sleep(max(1, settings.autoscale_interval_seconds))
    except asyncio.CancelledError:
        raise


async def autoscaler_tick(*, now: datetime | None = None) -> Decision:
    """Ejecuta un ciclo, separable del loop para pruebas y operaciones."""
    global LAST_ACTION_AT, QUEUE_PRESSURE_SINCE
    now = now or utcnow()
    settings = get_settings()
    snapshot = await build_snapshot(now)
    pressure = (
        snapshot["pendientes"] > settings.autoscale_queue_threshold
        and snapshot["pendientes"] > snapshot["capacidad_libre_fija"]
    )
    if pressure:
        QUEUE_PRESSURE_SINCE = QUEUE_PRESSURE_SINCE or now
    else:
        QUEUE_PRESSURE_SINCE = None
    snapshot["queue_pressure_since"] = QUEUE_PRESSURE_SINCE
    snapshot["last_action_at"] = LAST_ACTION_AT
    snapshot["last_scale_up_pending"] = LAST_SCALE_UP_PENDING
    policy_cfg = settings.model_dump()
    policy_cfg["autoscale_max_lifetime_seconds"] = _effective_max_lifetime(settings)
    decision = decide(snapshot, policy_cfg, now)
    formula: dict[str, float | int] = {
        "W": round(decision.work_seconds, 2),
        "S": decision.current_slots,
        "R": round(decision.remaining_work_seconds, 2),
        "n": decision.desired_instances,
    }
    _event(
        "decision",
        reason=decision.reason,
        scale_up=decision.scale_up,
        scale_down_ids=decision.scale_down_ids or [],
        **formula,
        dry_run=bool(settings.autoscale_dry_run),
    )
    if settings.autoscale_dry_run:
        return decision
    if decision.scale_up:
        await launch_workers(
            decision.scale_up,
            reason=decision.reason,
            dry_run=False,
            instance_type=decision.instance_type,
            pending_at_decision=snapshot["pendientes"],
            formula=formula,
        )
    elif decision.scale_down_ids:
        await terminate_workers(decision.scale_down_ids, reason=decision.reason)
    await _process_draining()
    return decision


async def autoscaler_loop() -> None:
    """Bucle resiliente: ningún error individual termina esta tarea supervisada."""
    settings = get_settings()
    try:
        try:
            await reconcile_managed()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - siguiente tick reintenta
            _event("reconcile_failed", reason=type(exc).__name__)
        while True:
            try:
                if effective_enabled():
                    await autoscaler_tick()
            except asyncio.CancelledError:
                raise
            except BaseException as exc:  # noqa: BLE001 - el controlador no debe morir
                _event("tick_failed", reason=type(exc).__name__)
                log.exception("falló tick del autoscaler")
            await asyncio.sleep(max(1, settings.autoscale_interval_seconds))
    except asyncio.CancelledError:
        raise
