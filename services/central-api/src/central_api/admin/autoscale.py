"""API administrativa del autoscaler EC2 efímero."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, model_validator

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.autoscaler import controller
from central_api.settings import get_settings
from central_api.store import EPHEMERAL_WORKERS, utcnow

router = APIRouter()


class ScaleUpBody(BaseModel):
    count: int = Field(ge=1, le=100)
    motivo: str


class TerminateBody(BaseModel):
    instance_ids: list[str] = Field(default_factory=list, max_length=100)
    all: bool = False
    motivo: str

    @model_validator(mode="after")
    def _select_instances(self):
        if self.all and self.instance_ids:
            raise ValueError("use instance_ids o all, no ambos")
        if not self.all and not self.instance_ids:
            raise ValueError("indique instance_ids o all=true")
        return self


class MotivoBody(BaseModel):
    motivo: str


def _require_reason(motivo: str) -> str:
    try:
        return validar_motivo(motivo)
    except HTTPException:
        raise


def _safe_config(*, fleet: Any | None = None) -> dict:
    settings = get_settings()
    return {
        "enabled": controller.effective_enabled(),
        "dry_run": settings.autoscale_dry_run,
        "queue_threshold": settings.autoscale_queue_threshold,
        "up_after_seconds": settings.autoscale_up_after_seconds,
        "max_instances": (
            controller._effective_max(settings, fleet)
            if fleet is not None else settings.autoscale_max_instances
        ),
        "step": settings.autoscale_step,
        "boot_grace_seconds": settings.autoscale_boot_grace_seconds,
        "idle_seconds": settings.autoscale_idle_seconds,
        "interval_seconds": settings.autoscale_interval_seconds,
        "cooldown_seconds": settings.autoscale_cooldown_seconds,
        "max_lifetime_seconds": settings.autoscale_max_lifetime_seconds,
        "capacity_per_worker": settings.autoscale_capacity_per_worker,
    }


def _serialize_ephemeral(instance_id: str, item: dict) -> dict:
    result = dict(item)
    result.setdefault("instance_id", instance_id)
    for key in ("launched_at", "last_busy_at", "last_heartbeat_at", "registered_at"):
        value = result.get(key)
        if isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


@router.get("/autoscale")
async def get_autoscale(authorization: str | None = Header(default=None)) -> dict:
    require_admin(authorization)
    snapshot = await controller.build_snapshot()
    workers = []
    estimated_cost: float | None = None
    aws_error: str | None = None
    try:
        fleet = controller._make_fleet()
        workers = await asyncio.to_thread(fleet.list_managed)
        estimated_cost = await asyncio.to_thread(fleet.estimate_cost, workers, utcnow())
    except Exception as exc:  # noqa: BLE001 - status view remains available without AWS
        aws_error = type(exc).__name__
    return {
        "success": True,
        "config": {
            **_safe_config(fleet=fleet if aws_error is None else None),
            "ec2": ({
                "region": fleet.settings.region,
                "instance_type": fleet.settings.instance_type,
                "worker_port": fleet.settings.worker_port,
                "worker_concurrency": fleet.settings.worker_concurrency,
                "max_lifetime_minutes": fleet.settings.max_lifetime_minutes,
                "project_tag": fleet.settings.project_tag,
            } if aws_error is None else None),
        },
        "snapshot": snapshot,
        "efimeros": [
            _serialize_ephemeral(instance_id, item)
            for instance_id, item in EPHEMERAL_WORKERS.items()
        ],
        "costo_estimado": estimated_cost,
        "costo_moneda": "USD",
        "aws_error": aws_error,
        "eventos": controller.event_list(),
    }


@router.post("/autoscale/scale-up")
async def autoscale_scale_up(
    body: ScaleUpBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    actor = require_admin(authorization)
    motivo = _require_reason(body.motivo)
    try:
        await controller.reconcile_managed()
        launched = await controller.launch_workers(body.count, reason=motivo, dry_run=False)
    except Exception as exc:  # noqa: BLE001 - don't expose AWS error details
        log_event(
            "autoscaler.scale_up", actor_id=actor, target_type="ec2-fleet", target_id="all",
            request_id=request_id or "", result="error", reason=motivo,
            metadata={"count": body.count, "error": type(exc).__name__},
        )
        raise HTTPException(status_code=503, detail=f"AWS no disponible: {type(exc).__name__}") from exc
    log_event(
        "autoscaler.scale_up", actor_id=actor, target_type="ec2-fleet", target_id="all",
        request_id=request_id or "", reason=motivo,
        metadata={"requested": body.count, "launched": len(launched)},
    )
    return {"success": True, "requested": body.count, "launched": launched}


@router.post("/autoscale/terminate")
async def autoscale_terminate(
    body: TerminateBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    actor = require_admin(authorization)
    motivo = _require_reason(body.motivo)
    targets = list(EPHEMERAL_WORKERS) if body.all else body.instance_ids
    unknown = [instance_id for instance_id in targets if instance_id not in EPHEMERAL_WORKERS]
    if unknown:
        raise HTTPException(status_code=400, detail={
            "message": "solo se pueden terminar instancias efímeras registradas",
            "unknown_instance_ids": unknown,
        })
    try:
        result = await controller.terminate_workers(targets, reason=motivo)
    except Exception as exc:  # noqa: BLE001
        log_event(
            "autoscaler.terminate", actor_id=actor, target_type="ec2-fleet", target_id="all",
            request_id=request_id or "", result="error", reason=motivo,
            metadata={"instance_ids": targets, "error": type(exc).__name__},
        )
        raise HTTPException(status_code=503, detail=f"AWS no disponible: {type(exc).__name__}") from exc
    log_event(
        "autoscaler.terminate", actor_id=actor, target_type="ec2-instance", target_id=",".join(targets),
        request_id=request_id or "", reason=motivo, metadata=result,
    )
    return {"success": True, **result}


async def _set_enabled(enabled: bool, motivo: str, actor: str, request_id: str) -> dict:
    reason = _require_reason(motivo)
    controller.set_enabled(enabled)
    if enabled:
        controller.start_loop_task()
    else:
        await controller.stop_loop_task()
    action = "enable" if enabled else "disable"
    log_event(
        f"autoscaler.{action}", actor_id=actor, target_type="autoscaler", target_id="singleton",
        request_id=request_id, reason=reason, metadata={"enabled": enabled},
    )
    return {"success": True, "enabled": enabled}


@router.post("/autoscale/enable")
async def autoscale_enable(
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    actor = require_admin(authorization)
    return await _set_enabled(True, body.motivo, actor, request_id or "")


@router.post("/autoscale/disable")
async def autoscale_disable(
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    actor = require_admin(authorization)
    return await _set_enabled(False, body.motivo, actor, request_id or "")


@router.get("/autoscale/audit")
async def autoscale_audit(authorization: str | None = Header(default=None)) -> dict:
    actor = require_admin(authorization)
    try:
        fleet = controller._make_fleet()
        orphan_report = await asyncio.to_thread(fleet.audit_orphans)
        error = None
    except Exception as exc:  # noqa: BLE001 - audit endpoint reports adapter failures
        orphan_report = None
        error = type(exc).__name__
    log_event(
        "autoscaler.audit", actor_id=actor, target_type="ec2-fleet", target_id="all",
        result="success" if error is None else "error",
        metadata={"error": error, "report_available": orphan_report is not None},
    )
    return {
        "success": error is None,
        "audit_orphans": orphan_report,
        "error": error,
        "eventos": controller.event_list(),
    }
