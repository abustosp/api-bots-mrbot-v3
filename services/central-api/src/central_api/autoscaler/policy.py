"""Decisión pura de autoscaling para workers EC2 efímeros.

La política estima slot-segundos pendientes, descuenta el trabajo que puede
absorber la capacidad ya disponible durante el boot y calcula instancias
suficientes para un horizonte objetivo. No hace I/O ni modifica el snapshot.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping


@dataclass(frozen=True)
class Decision:
    scale_up: int = 0
    scale_down_ids: list[str] | None = None
    reason: str = "sin cambios"
    instance_type: str | None = None
    work_seconds: float = 0.0
    current_slots: int = 0
    remaining_work_seconds: float = 0.0
    desired_instances: int = 0

    def __post_init__(self) -> None:
        if self.scale_down_ids is None:
            object.__setattr__(self, "scale_down_ids", [])


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        if key in source:
            return source[key]
        return source.get(key.upper(), default)
    return getattr(source, key, getattr(source, key.upper(), default))


def _seconds(value: Any, now: datetime) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return max(0.0, (now - value).total_seconds())
    return None


def _ephemeral_parts(item: Any) -> tuple[str, Any, int, Any, bool, str, bool]:
    """Normalize public tuple (id,node,launched,jobs,last_busy,healthy) or dict."""
    if isinstance(item, Mapping):
        instance_id = str(item.get("instance_id", item.get("id", "")))
        launched = item.get("launched_at")
        jobs = int(item.get("jobs_activos", item.get("active_jobs", 0)) or 0)
        last_busy = item.get("last_busy_at")
        healthy = bool(item.get("sano", item.get("healthy", False)))
        state = str(item.get("state", ""))
        never_registered = bool(item.get("never_registered", not healthy))
    else:
        values = tuple(item)
        values += (None,) * max(0, 6 - len(values))
        instance_id, _node, launched, jobs, last_busy, healthy = values[:6]
        state = ""
        instance_id = str(instance_id)
        jobs = int(jobs or 0)
        healthy = bool(healthy)
        never_registered = not healthy
    return instance_id, launched, jobs, last_busy, healthy, state, never_registered


def _slots(item: Any, default: int) -> int:
    if isinstance(item, Mapping):
        value = item.get("concurrency", item.get("capacity", item.get("slots", default)))
    else:
        value = getattr(item, "concurrency", default)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return max(1, default)


def _decision(
    *,
    reason: str,
    scale_up: int = 0,
    scale_down_ids: list[str] | None = None,
    instance_type: str | None = None,
    work: float = 0,
    slots: int = 0,
    remaining: float = 0,
    desired: int = 0,
) -> Decision:
    return Decision(
        scale_up=scale_up,
        scale_down_ids=scale_down_ids or [],
        reason=reason,
        instance_type=instance_type,
        work_seconds=work,
        current_slots=slots,
        remaining_work_seconds=remaining,
        desired_instances=desired,
    )


def decide(snapshot: Any, cfg: Any, now: datetime) -> Decision:
    """Return one scale action using W/S/R/n in slot-seconds.

    ``W = pending * avg_job_seconds``
    ``S = healthy fixed slots + slots on all launched ephemeral instances``
    ``R = max(0, W - S * boot_seconds)``
    ``n = ceil(R / (new_worker_slots * target_work_seconds))``

    AUTOSCALE_STEP is a per-cycle ceiling and the safety maximum is two, even
    if an operator configures a larger value or sets the setting to zero.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    pending = max(0, int(_get(snapshot, "pendientes", 0) or 0))
    fixed_free = max(0, int(_get(snapshot, "capacidad_libre_fija", 0) or 0))
    fixed_slots = max(
        fixed_free,
        int(_get(snapshot, "capacidad_total_fija", fixed_free) or 0),
    )
    avg_job = max(
        0.001,
        float(_get(snapshot, "avg_job_seconds", _get(cfg, "autoscale_avg_job_seconds", 75)) or 75),
    )
    boot_seconds = max(
        0.0,
        float(_get(snapshot, "boot_seconds", _get(cfg, "autoscale_boot_estimate_seconds", 150)) or 0),
    )
    target = max(
        1.0,
        float(_get(cfg, "autoscale_target_work_seconds", 300) or 300),
    )
    base_type = str(_get(snapshot, "instance_type", _get(cfg, "autoscale_instance_type", "")) or "")
    base_slots = max(
        1,
        int(
            _get(
                snapshot,
                "capacidad_nueva",
                _get(cfg, "autoscale_capacity_per_worker", 5) or 5,
            )
            or 5
        ),
    )
    work = pending * avg_job
    ephemerals = list(_get(snapshot, "efimeros", []) or [])
    ephemeral_slots = sum(_slots(item, base_slots) for item in ephemerals)
    slots = fixed_slots + ephemeral_slots
    remaining = max(0.0, work - slots * boot_seconds)

    threshold = max(0, int(_get(cfg, "autoscale_queue_threshold", 10) or 0))
    up_after = max(0.0, float(_get(cfg, "autoscale_up_after_seconds", 20) or 0))
    max_instances = max(0, int(_get(cfg, "autoscale_max_instances", 4) or 0))
    configured_step = max(0, int(_get(cfg, "autoscale_step", 2) or 0))
    step = min(configured_step or 2, 2)
    boot_grace = max(0.0, float(_get(cfg, "autoscale_boot_grace_seconds", 240) or 0))
    idle_seconds = max(0.0, float(_get(cfg, "autoscale_idle_seconds", 45) or 0))
    max_lifetime = max(0.0, float(_get(cfg, "autoscale_max_lifetime_seconds", 86400) or 0))
    cooldown = max(0.0, float(_get(cfg, "autoscale_cooldown_seconds", 60) or 0))
    last_action_age = _seconds(_get(snapshot, "last_action_at"), now)
    on_cooldown = last_action_age is not None and last_action_age < cooldown
    pressure_seconds = _seconds(_get(snapshot, "queue_pressure_since"), now)
    if pressure_seconds is None:
        pressure_seconds = float(_get(snapshot, "queue_pressure_seconds", 0) or 0)

    active_instances = len(ephemerals)
    down_candidates: list[tuple[int, float, str, int]] = []
    idle_candidates: list[tuple[float, str]] = []
    for item in ephemerals:
        instance_id, launched_at, jobs, last_busy_at, healthy, state, never_registered = _ephemeral_parts(item)
        age = _seconds(launched_at, now) or 0.0
        idle_age = _seconds(last_busy_at or launched_at, now) or 0.0
        state = state.strip().lower()
        is_booting = state in {"pending", "booting", "launching", "starting"} or (
            not healthy and age < boot_grace
        )
        failed_boot = never_registered and age >= boot_grace * 2 and age > 0
        expired = max_lifetime > 0 and age >= max_lifetime
        item_slots = _slots(item, base_slots)
        if instance_id and (failed_boot or expired):
            down_candidates.append((0, -idle_age, instance_id, item_slots))
        elif instance_id and pending == 0 and jobs == 0 and idle_age >= idle_seconds and not is_booting:
            idle_candidates.append((-idle_age, instance_id))

    # Dead-man and lifetime protection supersede demand and cooldown.
    urgent = [candidate for candidate in down_candidates if candidate[0] == 0]
    if urgent:
        ids = [candidate[2] for candidate in sorted(urgent)]
        return _decision(
            reason="límite de vida o fallo de arranque (dead-man)",
            scale_down_ids=ids,
            work=work,
            slots=slots,
            remaining=remaining,
        )

    desired_default = 0
    chosen_type: str | None = None
    reason = "sin demanda adicional"
    if pending <= threshold:
        reason = "cola bajo umbral"
    elif remaining < avg_job * base_slots:
        reason = "la capacidad existente durante el boot absorbe la cola útil"
    elif pressure_seconds < up_after:
        reason = "presión de cola aún no sostenida"
    else:
        desired_default = math.ceil(remaining / (base_slots * target))
        large_type = str(
            _get(snapshot, "large_instance_type", _get(cfg, "autoscale_instance_type_large", "")) or ""
        )
        large_slots = max(1, int(_get(snapshot, "capacidad_tipo_grande", 0) or 0))
        base_rate = float(_get(snapshot, "rate_default", 0) or 0)
        large_rate = float(_get(snapshot, "rate_large", 0) or 0)
        desired = desired_default
        if (
            large_type
            and large_slots > base_slots
            and remaining >= avg_job * large_slots
            and base_rate > 0
            and large_rate > 0
            and large_rate / large_slots <= base_rate / base_slots
        ):
            desired_large = math.ceil(remaining / (large_slots * target))
            if desired_large < desired_default:
                desired = desired_large
                chosen_type = large_type
        available = max(0, max_instances - active_instances)
        desired = min(desired, available)
        desired = min(desired, step)
        last_scale_pending = _get(snapshot, "last_scale_up_pending")
        complementary = (
            last_scale_pending is not None
            and pending > int(last_scale_pending)
        )
        if desired <= 0:
            reason = "máximo de instancias alcanzado"
        elif on_cooldown and not complementary:
            reason = "cooldown activo; no hay aumento complementario de cola"
            desired = 0
        else:
            reason = (
                f"cola sostenida ({pending} pendientes): W={work:.0f}, S={slots}, "
                f"R={remaining:.0f}, n={desired}"
            )
            return _decision(
                reason=reason,
                scale_up=desired,
                instance_type=chosen_type,
                work=work,
                slots=slots,
                remaining=remaining,
                desired=desired,
            )

    # With queued work, remove only excess ephemeral capacity. Lowest-load
    # instances are drained first, and the controller still waits for jobs.
    if pending > 0 and ephemerals:
        absorption_window = max(1.0, boot_seconds, avg_job)
        required_slots = math.ceil(work / absorption_window)
        keep_ephemeral_slots = max(0, required_slots - fixed_slots)
        current_ephemeral_slots = ephemeral_slots
        candidates = []
        for item in ephemerals:
            instance_id, _launched, jobs, _busy, _healthy, _state, _never = _ephemeral_parts(item)
            if instance_id:
                candidates.append((jobs, instance_id, _slots(item, base_slots)))
        candidates.sort()
        remove: list[str] = []
        for jobs, instance_id, item_slots in candidates:
            if current_ephemeral_slots - item_slots < keep_ephemeral_slots:
                break
            current_ephemeral_slots -= item_slots
            remove.append(instance_id)
        if remove:
            return _decision(
                reason="capacidad efímera sobrante respecto del trabajo proyectado",
                scale_down_ids=remove,
                work=work,
                slots=slots,
                remaining=remaining,
            )

    if idle_candidates and not on_cooldown:
        idle_candidates.sort()
        return _decision(
            reason=f"sin cola: retirar efímeros ociosos tras {idle_seconds:.0f}s",
            scale_down_ids=[instance_id for _idle_age, instance_id in idle_candidates],
            work=work,
            slots=slots,
            remaining=remaining,
        )
    if on_cooldown:
        reason = "cooldown activo"
    return _decision(
        reason=reason,
        work=work,
        slots=slots,
        remaining=remaining,
        desired=desired_default,
    )
