"""Controlador asyncio supervisado del autoscaler EC2.

El SDK y el adaptador EC2 se cargan únicamente al primer uso. Todas las
operaciones potencialmente bloqueantes del fleet van a ``asyncio.to_thread``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from statistics import fmean
from typing import Any

from central_api.autoscaler import state as policy_state
from central_api.autoscaler.policy import Decision, decide
from central_api.db import db_configurado, nueva_sesion
from central_api.settings import get_settings
from central_api.store import (
    ADMIN_NODES,
    EPHEMERAL_WORKERS,
    JOBS,
    PAUSED_NODES,
    WORKERS,
    utcnow,
)
from central_api.worker_nodes import merge_nodes

log = logging.getLogger(__name__)
EVENTS: deque[dict] = deque(maxlen=200)
BOOT_SECONDS_SAMPLES: deque[float] = deque(maxlen=32)
FLEET: Any | None = None  # injectable in tests; production instance is lazy
# Flotas reales por zona (una por clave de ZONES): un cliente EC2 está atado a
# su región, así que cada zona necesita la suya. Vacío mientras FLEET (fake) esté
# inyectado.
FLEETS: dict[str, Any] = {}
RUNTIME_ENABLED: bool | None = None
LAST_ACTION_AT: datetime | None = None
LAST_SCALE_UP_PENDING: int | None = None
QUEUE_PRESSURE_SINCE: datetime | None = None
AUTOSCALER_TASK: asyncio.Task | None = None
DRAIN_TASK: asyncio.Task | None = None
# Catálogo de tipos por zona con TTL: describe_instance_types es lento y
# paginado (São Paulo tiene cientos de tipos) y no cambia entre consultas.
CATALOG_TTL_SECONDS = int(os.environ.get("AUTOSCALE_CATALOG_TTL_SECONDS", "300"))
_CATALOG: dict[str, tuple[float, list[dict]]] = {}


async def catalog_for(zone: str | None = None, *, force: bool = False) -> list[dict]:
    """Tipos lanzables de la zona (cacheado por TTL), para el panel."""
    key = resolve_zone(zone)
    now = time.monotonic()
    cached = _CATALOG.get(key)
    if not force and cached is not None and now - cached[0] < CATALOG_TTL_SECONDS:
        return list(cached[1])
    fleet = _make_fleet(key)
    rows = list(await asyncio.to_thread(fleet.list_instance_types))
    _CATALOG[key] = (now, rows)
    return list(rows)


def clear_catalog_cache() -> None:
    """Invalida la caché de catálogos (pruebas y cambios de configuración)."""
    _CATALOG.clear()


def _settings() -> Any:
    """Settings del entorno con los overrides de política del panel aplicados."""
    return policy_state.effective_settings()


def set_fleet_for_testing(fleet: Any | None) -> None:
    """Inyecta un FakeFleet, sin importar ni construir el adaptador AWS.

    Un fake inyectado se devuelve para cualquier zona (``_make_fleet`` ignora el
    argumento) y descarta las flotas reales por zona en caché.
    """
    global FLEET
    FLEET = fleet
    FLEETS.clear()


def known_zones() -> tuple[str, ...]:
    """Claves de zona soportadas por el adaptador EC2."""
    from central_api.autoscaler.ec2 import ZONES

    return tuple(ZONES)


def resolve_zone(zone: str | None = None) -> str:
    """Valida la zona pedida; sin zona devuelve la activa del panel."""
    key = str(zone or "").strip() or policy_state.active_zone()
    if key not in known_zones():
        raise ValueError(
            f"zona desconocida: {key!r} (válidas: {', '.join(sorted(known_zones()))})"
        )
    return key


def record_zone(record: dict) -> str:
    """Zona de una instancia registrada; sin dato se asume la activa."""
    return str(record.get("zone") or policy_state.active_zone())


def instances_in_zone(zone: str) -> int:
    """Cantidad de efímeras registradas en una zona (el tope es por zona)."""
    return sum(1 for record in EPHEMERAL_WORKERS.values() if record_zone(record) == zone)


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


def _make_fleet(zone: str | None = None) -> Any:
    """Flota de la zona pedida (por defecto, la zona activa del panel).

    Con un fake inyectado en pruebas se devuelve tal cual, sea cual sea la zona,
    para que los tests no necesiten el adaptador AWS. En producción se construye
    (y cachea) una flota por zona, porque el cliente EC2 está atado a su región.
    """
    global FLEET
    if FLEET is not None:
        return FLEET
    key = resolve_zone(zone)
    fleet = FLEETS.get(key)
    if fleet is None:
        # Import diferido a propósito: la central no necesita boto3 si el
        # autoscaler está deshabilitado.
        from central_api.autoscaler.ec2 import Ec2Fleet, Ec2Settings, Ec2Worker

        _ = Ec2Worker  # included in the lazy adapter import contract
        fleet = Ec2Fleet(Ec2Settings.from_env().for_zone(key))
        FLEETS[key] = fleet
    return fleet


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


def _register_instance(worker: Any, *, adopted: bool = False, zone: str | None = None) -> dict | None:
    instance_id = _instance_id(worker)
    if not instance_id:
        return None
    previous = EPHEMERAL_WORKERS.get(instance_id, {})
    # La zona no se pisa si ya estaba registrada: una adopción desde otra zona
    # (o un doble listado) mandaría la baja a la región equivocada y dejaría la
    # instancia viva pero invisible al panel.
    zona_previa = str(previous.get("zone") or "")
    zona_efectiva = zona_previa or str(zone or "") or policy_state.active_zone()
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
        # Zona de lanzamiento: manda la ya registrada; un registro viejo sin
        # zona cae a la que informe quien registra o a la activa del panel.
        "zone": zona_efectiva,
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
        "manual": bool(previous.get("manual")) or instance_id in policy_state.MANUAL_IDS,
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
    policy_state.MANUAL_IDS.discard(instance_id)
    if record is not None:
        _drop_inventory(record)
        node = str(record.get("node") or "")
        if node:
            # Libera todo lo asociado al nodo: pausa, alias y leases en memoria.
            from central_api.store import WORKER_ALIAS_OVERRIDES

            PAUSED_NODES.discard(node)
            WORKER_ALIAS_OVERRIDES.pop(node, None)
            try:
                from central_api.scheduler import reaper as reaper_mod

                for job_id, lease in list(reaper_mod.LEASES.items()):
                    if lease.get("worker") == node:
                        reaper_mod.clear_lease(job_id)
                reaper_mod.WORKER_LOAD.pop(node, None)
            except Exception:  # noqa: BLE001 - limpieza best-effort
                log.debug("no se pudieron limpiar leases de %s", node, exc_info=True)
        is_fixed = node in get_settings().worker_node_list
        shared_by_ephemeral = any(
            item.get("node") == node for item in EPHEMERAL_WORKERS.values()
        )
        if record.get("inventory_owned") and node and not is_fixed and not shared_by_ephemeral:
            WORKERS.pop(node, None)


async def reconcile_managed() -> list[str]:
    """Adopta lo que el adapter confirma como Project-managed al arrancar.

    Recorre todas las zonas de ``ZONES``: cada una tiene su propia región y
    credenciales efectivas, y el fallo de una (zona caída o sin permisos) no
    debe impedir adoptar las instancias de las demás; el error se registra en el
    evento. Las adoptadas conservan su zona y siguen siendo manuales si estaban
    en la lista persistida (``_register_instance`` lo resuelve).

    La ausencia de un ID en ``list_managed`` no se interpreta como terminación:
    el adapter puede filtrar estados transitorios. Los estados no-running que
    sí vengan en la respuesta se desinventarian, pero nunca se terminan aquí.
    """
    adopted: list[str] = []
    errors: dict[str, str] = {}
    for zone in known_zones():
        try:
            fleet = _make_fleet(zone)
            workers = await asyncio.to_thread(_list_managed_all_states, fleet)
        except Exception as exc:  # noqa: BLE001 - las otras zonas siguen
            errors[zone] = type(exc).__name__
            _event("reconcile_failed", reason=type(exc).__name__, zone=zone)
            continue
        for worker in workers:
            if _instance_id(worker) in adopted:
                # Un fake inyectado en pruebas responde igual en toda zona.
                continue
            record = _register_instance(worker, adopted=True, zone=zone)
            if record is None:
                continue
            instance_id = record["instance_id"]
            adopted.append(instance_id)
            state = record["state"].strip().lower()
            if state == "terminated":
                _remove_instance(instance_id)
            elif state in {"stopping", "stopped", "shutting-down"}:
                _drop_inventory(record)
    _event(
        "reconciled",
        reason="adopción segura de instancias administradas",
        instances=len(adopted),
        zones=list(known_zones()),
        **({"errors": errors} if errors else {}),
    )
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
    settings = _settings()
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
            and entry.node not in PAUSED_NODES
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
            "zone": record_zone(record),
            "launched_at": record.get("launched_at"),
            "instance_type": record.get("instance_type", ""),
            "ram_gib": record.get("ram_gib", 0),
            "concurrency": concurrency,
            "manual": bool(record.get("manual")),
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


def _effective_max(settings: Any, fleet: Any = None, zone: str | None = None) -> int:
    """Máximo de instancias efímeras **en total** (todas las zonas).

    El tope de policy (``max_instances``) y el del adapter se aplican al total:
    es un límite de costo y el panel lo muestra como un único máximo. El conteo
    se hace sobre todas las efímeras registradas (``launch_workers``). Se prefiere
    el ``max_instances`` de la flota de esa zona y, si no está disponible, el de
    ``Ec2Settings`` de su entorno.
    """
    maximum = max(0, int(settings.autoscale_max_instances))
    limits: list[int] = []
    try:
        limits.append(int(getattr(getattr(fleet, "settings", None), "max_instances")))
    except (AttributeError, TypeError, ValueError):
        pass
    if not limits:
        # Ec2Settings puede imponer un segundo límite. Leerlo solo al usar AWS.
        try:
            from central_api.autoscaler.ec2 import Ec2Settings

            base = Ec2Settings.from_env()
            limits.append(int((base.for_zone(zone) if zone else base).max_instances))
        except (ImportError, AttributeError, TypeError, ValueError):
            pass
    return min([maximum, *limits])


def _effective_max_lifetime(settings: Any) -> int:
    seconds = max(0, int(settings.autoscale_max_lifetime_seconds))
    try:
        from central_api.autoscaler.ec2 import Ec2Settings

        seconds = min(seconds, int(Ec2Settings.from_env().max_lifetime_minutes) * 60)
    except (ImportError, AttributeError, TypeError, ValueError):
        pass
    return seconds


def _wait_until_running_and_register(
    fleet: Any, workers: list[Any], timeout: int, zone: str | None = None
) -> list[Any]:
    """Publish each assigned node immediately when its public IP becomes known."""
    running_workers = fleet.wait_until_running(workers, timeout)
    for worker in running_workers:
        # Do not perform logging, DB work, or another await between learning
        # the address and allowing the worker's first register request.
        _register_instance(worker, zone=zone)
    return running_workers


async def launch_workers(
    count: int,
    *,
    reason: str,
    dry_run: bool | None = None,
    instance_type: str | None = None,
    pending_at_decision: int | None = None,
    formula: dict[str, float | int] | None = None,
    manual: bool = False,
    concurrency: int | None = None,
    zone: str | None = None,
) -> list[dict]:
    """Lanza y registra instancias en la zona pedida (por defecto la activa).

    La espera por EC2 se mantiene fuera del loop. Cada registro guarda su
    ``zone`` para que bajas, adopción y catálogos se resuelvan por zona; el cupo
    máximo de instancias, en cambio, es un total entre todas las zonas.
    """
    global LAST_ACTION_AT, LAST_SCALE_UP_PENDING
    settings = _settings()
    count = max(0, int(count))
    zone_key = resolve_zone(zone)
    fleet = _make_fleet(zone_key)
    maximum = _effective_max(settings, fleet, zone=zone_key)
    # El cupo es TOTAL (suma de todas las zonas), igual que el de la política:
    # el panel anuncia un único máximo y es lo que se factura.
    remaining = max(0, maximum - len(EPHEMERAL_WORKERS))
    count = min(count, remaining)
    if count <= 0:
        # Aplica enfriamiento: sin esto el ciclo automático reintenta y registra
        # el mismo "no se pudo" en cada intervalo.
        LAST_ACTION_AT = utcnow()
        _event(
            "scale_up_skipped",
            reason="máximo de instancias alcanzado",
            maximum=maximum,
            zone=zone_key,
            registered=len(EPHEMERAL_WORKERS),
        )
        return []
    if dry_run is None:
        dry_run = settings.autoscale_dry_run
    if dry_run:
        _event("dry_run", reason=reason, scale_up=count, zone=zone_key)
        return []
    launch_kwargs: dict[str, Any] = {}
    if instance_type:
        launch_kwargs["instance_type"] = instance_type
    if concurrency:
        launch_kwargs["concurrency"] = int(concurrency)
    if launch_kwargs:
        try:
            workers = await asyncio.to_thread(fleet.launch, count, **launch_kwargs)
        except TypeError as exc:
            # Flotas de prueba sin soporte de estos argumentos.
            if "instance_type" not in str(exc) and "concurrency" not in str(exc):
                raise
            workers = await asyncio.to_thread(fleet.launch, count)
    else:
        workers = await asyncio.to_thread(fleet.launch, count)
    if manual:
        # Marcar antes de registrar: el autoscaler automatico no las retira.
        for worker in workers:
            if _instance_id(worker):
                policy_state.MANUAL_IDS.add(_instance_id(worker))
        await policy_state.save_manual()
    records = [
        record for worker in workers
        if (record := _register_instance(worker, zone=zone_key)) is not None
    ]
    limit = getattr(fleet, "last_launch_limit", None)
    if limit or len(workers) < count:
        # Pedimos ``count`` y AWS entrego menos (cuota vCPU o capacidad): no es un exito.
        _event(
            "scale_up_short",
            reason=f"se pidieron {count} y AWS entrego {len(workers)}" + (f" ({limit})" if limit else ""),
            requested=count, launched=len(workers), limit=limit, zone=zone_key,
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
        zone=zone_key,
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
                zone_key,
            )
        except Exception as exc:  # noqa: BLE001 - el siguiente ciclo reconcilia
            _event("wait_failed", reason=type(exc).__name__, zone=zone_key)
    return records


async def _release_jobs_hard(record: dict) -> int:
    """Finaliza ya los jobs de un nodo: vence sus leases para que el reaper actue.

    El reaper existente reencola cada job si le quedan intentos y no hay
    resultado durable, o lo cierra como FALLIDO en caso contrario (sin replay
    de efectos ambiguos). Devuelve la cantidad de jobs liberados.
    """
    node = str(record.get("node") or "")
    if not node:
        return 0
    released = 0
    if db_configurado():
        try:
            from sqlalchemy import text

            async with nueva_sesion() as session:
                result = await session.execute(
                    text(
                        "UPDATE jobs SET lease_expires_at = CURRENT_TIMESTAMP - interval '1 second' "
                        "WHERE status IN ('ASIGNADO','CORRIENDO') AND worker_id IN "
                        "(SELECT id FROM workers WHERE endpoint = :node)"
                    ),
                    {"node": node},
                )
                released = int(result.rowcount or 0)
        except Exception as exc:  # noqa: BLE001 - se reintenta en el siguiente ciclo
            log.warning("no se pudieron vencer leases de %s: %s", node, type(exc).__name__)
    try:
        from datetime import timedelta

        from central_api.scheduler import reaper as reaper_mod

        past = utcnow() - timedelta(seconds=1)
        for lease in reaper_mod.LEASES.values():
            if lease.get("worker") == node:
                lease["expires_at"] = past
                released += 0 if db_configurado() else 1
        # Procesar ya: sin esto los leases se borrarian al retirar la instancia
        # y los jobs quedarian ASIGNADO/CORRIENDO sin dueno.
        reaper_mod.reap_expired(utcnow())
    except Exception:  # noqa: BLE001
        log.debug("no se pudieron vencer leases en memoria de %s", node, exc_info=True)
    if db_configurado():
        try:
            from central_api.scheduler.loop import reap_pg_vencidas

            await reap_pg_vencidas()
        except Exception:  # noqa: BLE001 - el scheduler lo repite en su ciclo
            log.debug("reaper PG inmediato fallo para %s", node, exc_info=True)
    return released


async def _active_jobs(record: dict) -> int:
    node = str(record.get("node") or "")
    if not node:
        return 0
    if record.get("hard_terminate"):
        # Terminacion forzada: ya se vencieron los leases, no se espera a nadie.
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


def _group_by_zone(instance_ids: list[str]) -> dict[str, list[str]]:
    """Agrupa IDs por la zona de su registro (sin zona: la activa)."""
    groups: dict[str, list[str]] = {}
    for instance_id in instance_ids:
        record = EPHEMERAL_WORKERS.get(instance_id) or {}
        groups.setdefault(record_zone(record), []).append(instance_id)
    return groups


async def terminate_workers(
    instance_ids: list[str], *, reason: str, mode: str = "soft"
) -> dict:
    """Drena primero, y retira el nodo solo al observar EC2 ``terminated``.

    ``mode="soft"`` espera a que los jobs terminen. ``mode="hard"`` corta el
    dispatch, libera de inmediato los jobs del nodo (reencolados o fallidos
    por el reaper) y termina la instancia sin esperar.

    Las instancias se agrupan por su zona registrada y cada grupo se opera con
    la flota de esa zona: un fallo en una zona no impide terminar las demás, y
    el resultado agregado conserva las mismas claves más ``error`` solo si
    alguna zona falló (``{zona: tipo_de_error}``).
    """
    global LAST_ACTION_AT
    if mode not in {"soft", "hard"}:
        raise ValueError("mode debe ser soft o hard")
    known = [iid for iid in dict.fromkeys(instance_ids) if iid in EPHEMERAL_WORKERS]
    if not known:
        return {"terminated": [], "draining": [], "ignored": list(instance_ids)}
    for iid in known:
        if not EPHEMERAL_WORKERS[iid].get("drain_requested"):
            await _mark_draining(EPHEMERAL_WORKERS[iid])
    if mode == "hard":
        for iid in known:
            record = EPHEMERAL_WORKERS[iid]
            if not record.get("hard_terminate"):
                released = await _release_jobs_hard(record)
                record["hard_terminate"] = True
                _event("hard_terminate", reason=reason, instance=iid, jobs_released=released)
    ready: list[str] = []
    draining: list[str] = []
    for iid in known:
        record = EPHEMERAL_WORKERS[iid]
        if await _active_jobs(record) == 0:
            ready.append(iid)
        else:
            draining.append(iid)

    errors: dict[str, str] = {}
    by_zone = _group_by_zone(ready)
    accepted: set[str] = set()
    for zone, ids in by_zone.items():
        fleet = _make_fleet(zone)
        to_request = [
            iid for iid in ids
            if not EPHEMERAL_WORKERS[iid].get("termination_requested")
        ]
        if not to_request:
            continue
        try:
            accepted.update(
                str(iid) for iid in
                (await asyncio.to_thread(fleet.terminate, to_request) or [])
            )
        except Exception as exc:  # noqa: BLE001 - las otras zonas siguen
            errors[zone] = type(exc).__name__
            _event("terminate_failed", reason=type(exc).__name__, instances=to_request,
                   zone=zone)
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
    confirmadas: set[str] = set()
    for zone, ids in _group_by_zone(awaiting_confirmation).items():
        # El adapter confirma estados con la flota (y región) de cada zona.
        fleet = _make_fleet(zone)
        try:
            managed = await asyncio.to_thread(_list_managed_all_states, fleet)
        except Exception as exc:  # noqa: BLE001 - la baja aceptada sigue pendiente
            errors[zone] = type(exc).__name__
            _event("termination_confirmation_failed", reason=type(exc).__name__,
                   instances=ids, zone=zone)
            continue
        confirmadas.add(zone)
        state_by_id.update({
            _instance_id(worker): str(getattr(worker, "state", "unknown")).lower()
            for worker in managed
        })

    removed: list[str] = []
    removed_by_zone: dict[str, list[str]] = {}
    ausentes: list[str] = []
    pending_confirmation: list[str] = []
    for iid in awaiting_confirmation:
        state = state_by_id.get(iid)
        record = EPHEMERAL_WORKERS.get(iid) or {}
        if state is None and record_zone(record) in confirmadas:
            # EC2 no lo informa ni siquiera como 'terminated': la instancia ya
            # no existe (la terminó un tercero, por ejemplo). Sin esto el
            # registro queda vivo, muestra una instancia inexistente en el
            # panel y bloquea el cupo de la zona.
            removed_by_zone.setdefault(record_zone(record), []).append(iid)
            _remove_instance(iid)
            removed.append(iid)
            ausentes.append(iid)
            continue
        if state == "terminated":
            removed_by_zone.setdefault(record_zone(record), []).append(iid)
            _remove_instance(iid)
            removed.append(iid)
        else:
            if state in {"stopping", "stopped", "shutting-down"}:
                record["state"] = state
                _drop_inventory(record)
            pending_confirmation.append(iid)
    if ausentes:
        _event("instances_gone",
               reason="no figuran en EC2: se quitaron del inventario",
               instances=ausentes)
    if removed:
        _event("terminated", reason=reason, instances=removed)
        for zone, ids in removed_by_zone.items():
            await _cleanup_resources(_make_fleet(zone), ids)
    if draining:
        _event("draining", reason=reason, instances=draining)
    if pending_confirmation or draining or errors:
        # Una zona con error se reintenta en el siguiente tick del monitor.
        _ensure_drain_monitor()
    ignored = [iid for iid in ready if iid not in awaiting_confirmation]
    result = {"terminated": removed, "draining": draining,
              "pending": pending_confirmation, "ignored": ignored}
    if errors:
        result["error"] = errors
    return result


async def _cleanup_resources(fleet: Any, instance_ids: list[str]) -> None:
    """Libera lo que EC2 pudo dejar colgado de las instancias eliminadas.

    Disco raiz e interfaz de red se borran con la instancia
    (``DeleteOnTermination``). Esto barre volumenes huerfanos etiquetados con
    el proyecto. Es best-effort: un fallo no revierte la baja.
    """
    cleanup = getattr(fleet, "cleanup_orphans", None)
    if not callable(cleanup):
        return
    try:
        report = await asyncio.to_thread(cleanup)
    except Exception as exc:  # noqa: BLE001
        _event("cleanup_failed", reason=type(exc).__name__, instances=instance_ids)
        return
    if report:
        _event("cleanup", reason="recursos huerfanos liberados", instances=instance_ids,
               resources=report)


async def _process_draining() -> None:
    requested = [
        iid for iid, record in EPHEMERAL_WORKERS.items()
        if record.get("drain_requested")
    ]
    if requested:
        soft = [iid for iid in requested if not EPHEMERAL_WORKERS[iid].get("hard_terminate")]
        hard = [iid for iid in requested if EPHEMERAL_WORKERS[iid].get("hard_terminate")]
        if soft:
            await terminate_workers(soft, reason="drenaje previamente solicitado")
        if hard:
            await terminate_workers(hard, reason="terminación forzada pendiente", mode="hard")


def _ensure_drain_monitor() -> None:
    global DRAIN_TASK
    if DRAIN_TASK is None or DRAIN_TASK.done():
        DRAIN_TASK = asyncio.create_task(_drain_monitor_loop())


async def _drain_monitor_loop() -> None:
    """Finish requested drains even while automatic scaling is disabled."""
    settings = _settings()
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
    settings = _settings()
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
    settings = _settings()
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
