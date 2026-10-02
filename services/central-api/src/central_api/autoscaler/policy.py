"""Decisión pura de escalado para workers EC2 efímeros.

No importa servicios, estado global ni SDKs. ``snapshot`` y ``cfg`` pueden ser
mapping o objetos con atributos, por lo que las reglas se prueban sin IO.
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

    def __post_init__(self) -> None:
        if self.scale_down_ids is None:
            object.__setattr__(self, "scale_down_ids", [])


def _get(source: Any, key: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        if key in source:
            return source[key]
        upper = key.upper()
        if upper in source:
            return source[upper]
        return default
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
    """Normaliza tupla pública (id,node,launched,jobs,last_busy,sano) o dict."""
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


def decide(snapshot: Any, cfg: Any, now: datetime) -> Decision:
    """Devuelve una acción de escalado sin modificar snapshot/configuración.

    Se aceptan además ``queue_pressure_since`` y ``last_action_at`` en el
    snapshot. El controller mantiene esos dos instantes entre iteraciones.
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    pending = max(0, int(_get(snapshot, "pendientes", 0) or 0))
    fixed_free = max(0, int(_get(snapshot, "capacidad_libre_fija", 0) or 0))
    fixed_total = max(0, int(_get(snapshot, "capacidad_total_fija", 0) or 0))
    pressure_since = _get(snapshot, "queue_pressure_since")
    pressure_seconds = _seconds(pressure_since, now)
    if pressure_seconds is None:
        pressure_seconds = float(_get(snapshot, "queue_pressure_seconds", 0) or 0)
    last_action_age = _seconds(_get(snapshot, "last_action_at"), now)
    cooldown = max(0, int(_get(cfg, "autoscale_cooldown_seconds", 60) or 0))
    on_cooldown = last_action_age is not None and last_action_age < cooldown

    threshold = max(0, int(_get(cfg, "autoscale_queue_threshold", 10) or 0))
    up_after = max(0, int(_get(cfg, "autoscale_up_after_seconds", 20) or 0))
    per_worker = max(1, int(_get(cfg, "autoscale_capacity_per_worker", 5) or 5))
    max_instances = max(0, int(_get(cfg, "autoscale_max_instances", 4) or 0))
    step = max(0, int(_get(cfg, "autoscale_step", 2) or 0))
    boot_grace = max(0, int(_get(cfg, "autoscale_boot_grace_seconds", 240) or 0))
    idle_seconds = max(0, int(_get(cfg, "autoscale_idle_seconds", 120) or 0))
    max_lifetime = max(0, int(_get(cfg, "autoscale_max_lifetime_seconds", 86400) or 0))

    ephemerals = list(_get(snapshot, "efimeros", []) or [])
    active_instances = len(ephemerals)
    pending_boots = 0
    down_candidates: list[tuple[int, float, str]] = []
    no_capacity_demand = pending <= fixed_free

    for item in ephemerals:
        instance_id, launched_at, jobs, last_busy_at, healthy, state, never_registered = _ephemeral_parts(item)
        age = _seconds(launched_at, now) or 0.0
        idle_age = _seconds(last_busy_at or launched_at, now) or 0.0
        normalized_state = state.strip().lower()
        if normalized_state in {"pending", "booting", "launching", "starting"} or (
            not healthy and age < boot_grace
        ):
            pending_boots += 1

        failed_boot = never_registered and age >= boot_grace * 2 and age > 0
        expired = max_lifetime > 0 and age >= max_lifetime
        idle = (
            jobs == 0
            and no_capacity_demand
            and idle_age >= idle_seconds
            and (healthy or age >= boot_grace)
        )
        if instance_id and (failed_boot or expired or idle):
            # Dead-man and max-life take priority; then longest-idle instances.
            urgency = 0 if (failed_boot or expired) else 1
            down_candidates.append((urgency, -idle_age, instance_id))

    # Dead-man/lifetime protection supersedes scale-up pressure. The controller
    # still drains these nodes and waits for zero active work before terminating.
    if not on_cooldown:
        deadman_ids = [
            instance_id for urgency, _idle_age, instance_id in down_candidates
            if urgency == 0
        ]
        if deadman_ids:
            return Decision(
                scale_down_ids=deadman_ids,
                reason="límite de vida o fallo de arranque (dead-man)",
            )

    # Capacity need is based on the fixed fleet. In-flight launches already
    # satisfy part of that need, preventing duplicate launch requests.
    need = max(0, math.ceil(max(0, pending - fixed_free) / per_worker))
    up_reason = ""
    if pending <= threshold:
        up_reason = "cola bajo umbral"
    elif pending <= fixed_free:
        up_reason = "capacidad fija suficiente"
    elif pressure_seconds < up_after:
        up_reason = "presión de cola aún no sostenida"
    elif on_cooldown:
        up_reason = "cooldown activo"
    else:
        up_count = min(need - pending_boots, max_instances - active_instances, step)
        if up_count > 0:
            return Decision(
                scale_up=up_count,
                reason=(f"cola sostenida ({pending} pendientes), libres fijos="
                        f"{fixed_free}, capacidad fija total={fixed_total}"),
            )
        if active_instances >= max_instances:
            up_reason = "máximo de instancias alcanzado"
        elif need <= pending_boots:
            up_reason = "instancias pendientes/arrancando cubren la demanda"
        else:
            up_reason = "límite por ciclo alcanzado"

    if not on_cooldown and down_candidates:
        down_candidates.sort()
        down_ids = [instance_id for _urgency, _idle_age, instance_id in down_candidates]
        return Decision(scale_down_ids=down_ids, reason="retirar efímeros ociosos/dead-man")
    if on_cooldown:
        return Decision(reason="cooldown activo")
    return Decision(reason=up_reason or (f"sin demanda adicional ({pending} pendientes, {fixed_free} libres fijos)"))
