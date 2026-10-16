"""API administrativa del autoscaler EC2 efímero."""

from __future__ import annotations

import asyncio
from datetime import datetime
from itertools import count
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, create_model, model_validator

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.autoscaler import controller
from central_api.settings import get_settings
from central_api.store import EPHEMERAL_WORKERS, utcnow

router = APIRouter()


class ScaleUpBody(BaseModel):
    count: int = Field(
        ge=1,
        le=100,
        description="Cantidad de instancias efímeras que se solicita iniciar.",
        examples=[2],
    )
    motivo: str = Field(
        description="Justificación de auditoría para aumentar la capacidad.",
        examples=["Aumento temporal por crecimiento de la cola"],
    )
    manual: bool = Field(
        default=True,
        description=(
            "Si es true (por defecto) las instancias son manuales: el autoscaler "
            "automático no las retira, solo se eliminan desde el panel."
        ),
    )


class PolicyBody(BaseModel):
    values: dict[str, int] = Field(
        default_factory=dict,
        description="Parámetros de política a fijar (ver GET /admin/autoscale/policy).",
        examples=[{"queue_threshold": 10, "jobs_per_worker": 8, "max_instances": 6}],
    )
    reset: list[str] = Field(
        default_factory=list,
        description="Parámetros que vuelven al valor del entorno (AUTOSCALE_*).",
    )
    motivo: str = Field(
        description="Justificación de auditoría para cambiar la política.",
        examples=["Ajuste de política por horario pico"],
    )


class TerminateBody(BaseModel):
    instance_ids: list[str] = Field(
        default_factory=list,
        max_length=100,
        description="Identificadores de instancias efímeras registradas que se terminarán.",
        examples=[["i-0123456789abcdef0"]],
    )
    all: bool = Field(
        default=False,
        description="Si es true, selecciona todas las instancias efímeras registradas.",
        examples=[False],
    )
    motivo: str = Field(
        description="Justificación de auditoría para terminar las instancias.",
        examples=["Retiro de capacidad temporal sobrante"],
    )
    mode: str = Field(
        default="soft",
        pattern="^(soft|hard)$",
        description=(
            "soft: deja de asignar jobs y termina cuando llegan a 0. "
            "hard: libera de inmediato los jobs de la instancia y la termina ya."
        ),
        examples=["soft"],
    )

    @model_validator(mode="after")
    def _select_instances(self):
        if self.all and self.instance_ids:
            raise ValueError("use instance_ids o all, no ambos")
        if not self.all and not self.instance_ids:
            raise ValueError("indique instance_ids o all=true")
        return self


class MotivoBody(BaseModel):
    motivo: str = Field(
        description="Justificación de auditoría para cambiar el autoscaler.",
        examples=["Pausa solicitada durante mantenimiento"],
    )


_SECUENCIA_MODELOS_OPENAPI = count(1)
_DESCRIPCIONES_CAMPOS_RESPUESTA = {
    "success": "Indica si la operación se completó correctamente.",
    "config": "Configuración efectiva y límites del escalado automático.",
    "snapshot": "Mediciones actuales de demanda y capacidad.",
    "efimeros": "Instancias efímeras conocidas y su estado operativo.",
    "costo_estimado": "Costo estimado de las instancias, si pudo calcularse.",
    "costo_moneda": "Moneda en la que se expresa el costo estimado.",
    "aws_error": "Tipo de error al consultar el servicio de instancias, o null si no hubo error.",
    "eventos": "Eventos operativos recientes del escalador.",
    "requested": "Cantidad de instancias solicitadas.",
    "launched": "Datos de las instancias iniciadas y registradas.",
    "terminated": "Instancias cuya terminación ya fue confirmada.",
    "draining": "Instancias que aún esperan finalizar sus trabajos activos.",
    "pending": "Instancias cuya terminación está solicitada pero pendiente de confirmación.",
    "ignored": "Instancias que no requirieron una nueva solicitud de terminación.",
    "enabled": "Indica si el escalado automático está habilitado.",
    "audit_orphans": "Informe de instancias activas sin correspondencia en el inventario.",
    "error": "Tipo de error de auditoría, o null si la revisión terminó correctamente.",
}


def _tipo_campo_respuesta(valor: object) -> Any:
    """Infiere el tipo JSON de primer nivel de un ejemplo de salida."""
    if isinstance(valor, bool):
        return bool
    if isinstance(valor, (str, int, float)):
        return type(valor)
    if isinstance(valor, list):
        return list[Any]
    if isinstance(valor, dict):
        return dict[str, Any]
    return Any


def _respuesta_json(descripcion: str, ejemplo: dict) -> dict:
    """Prepara la descripción de cada campo sin filtrar la respuesta real."""
    campos = {
        nombre: (
            _tipo_campo_respuesta(valor),
            Field(
                description=_DESCRIPCIONES_CAMPOS_RESPUESTA.get(
                    nombre, f"Campo «{nombre}» incluido en la respuesta de la operación."
                ),
                examples=[valor],
            ),
        )
        for nombre, valor in ejemplo.items()
    }
    modelo = create_model(
        f"AutoscaleOpenApiResponse{next(_SECUENCIA_MODELOS_OPENAPI)}", **campos
    )
    return {
        "description": descripcion,
        "model": modelo,
        "content": {"application/json": {"example": ejemplo}},
    }


def _errores_admin() -> dict:
    """Respuestas de autenticación comunes a las rutas administrativas."""
    return {
        401: {
            "description": (
                "No se recibió una cabecera Bearer válida. Envíe "
                "Authorization: Bearer <ADMIN_TOKEN>."
            )
        },
        403: {
            "description": (
                "El token administrativo no está configurado o no coincide. "
                "Configure ADMIN_TOKEN en el servicio o use el valor vigente."
            )
        },
    }


_ERROR_VALIDACION = {
    422: {
        "description": (
            "El cuerpo no cumple el formato, los límites o la selección requerida. "
            "Revise los campos y vuelva a enviar una solicitud coherente."
        )
    }
}


_ERROR_MOTIVO = {
    400: {
        "description": (
            "El motivo está vacío o no tiene entre 10 y 500 caracteres. Proporcione "
            "una justificación clara dentro de ese rango y repita la solicitud."
        )
    }
}


def _require_reason(motivo: str) -> str:
    try:
        return validar_motivo(motivo)
    except HTTPException:
        raise


def _safe_config(
    *, fleet: Any | None = None, runtime: dict[str, Any] | None = None
) -> dict:
    settings = controller._settings()
    runtime = dict(runtime or {})
    if fleet is not None and not runtime:
        try:
            runtime = fleet.instance_runtime()
        except Exception:  # noqa: BLE001 - fallback to static offline type table
            runtime = {}
    if not runtime:
        try:
            from central_api.autoscaler.ec2 import Ec2Settings, INSTANCE_TYPE_RAM_GIB

            ec2_settings = Ec2Settings.from_env()
            runtime = {
                "instance_type": ec2_settings.instance_type,
                "ram_gib": INSTANCE_TYPE_RAM_GIB.get(ec2_settings.instance_type, 8.0),
                "worker_concurrency": ec2_settings.effective_worker_concurrency,
            }
        except Exception:  # noqa: BLE001 - retain admin page availability
            runtime = {"worker_concurrency": settings.autoscale_capacity_per_worker or 5}
    concurrency = int(runtime.get("worker_concurrency", 5) or 5)
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
        "capacity_per_worker": concurrency,
        "worker_concurrency": concurrency,
        "effective_worker_concurrency": concurrency,
        "avg_job_seconds": settings.autoscale_avg_job_seconds,
        "boot_estimate_seconds": settings.autoscale_boot_estimate_seconds,
        "target_work_seconds": settings.autoscale_target_work_seconds,
        "jobs_per_worker": settings.autoscale_jobs_per_worker,
        "max_total_workers": settings.autoscale_max_total_workers,
        "instance_type_large": settings.autoscale_instance_type_large,
    }


def _serialize_ephemeral(instance_id: str, item: dict) -> dict:
    result = dict(item)
    result.setdefault("instance_id", instance_id)
    for key in ("launched_at", "last_busy_at", "last_heartbeat_at", "registered_at"):
        value = result.get(key)
        if isinstance(value, datetime):
            result[key] = value.isoformat()
    return result


@router.get(
    "/autoscale",
    summary="Consultar el estado del escalado automático",
    description=(
        "Devuelve la configuración efectiva, la instantánea de demanda, las "
        "instancias efímeras conocidas, el costo estimado y los eventos recientes. "
        "Si no se puede consultar el servicio de instancias, el estado sigue disponible: "
        "los campos relacionados indican el error y los datos no obtenidos quedan vacíos. "
        "Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        200: _respuesta_json(
            "Estado y configuración del escalado automático; aws_error puede indicar que faltan datos externos.",
            {
                "success": True,
                "config": {"enabled": True, "dry_run": False, "queue_threshold": 10, "max_instances": 8, "capacity_per_worker": 5, "ec2": {"region": "sa-east-1", "instance_type": "c6i.xlarge", "worker_concurrency": 5}},
                "snapshot": {"queued": 3, "running": 2},
                "efimeros": [{"instance_id": "i-0123456789abcdef0", "state": "running", "node": "10.0.0.13:8080"}],
                "costo_estimado": 0.12,
                "costo_moneda": "USD",
                "aws_error": None,
                "eventos": [],
            },
        ),
    },
)
async def get_autoscale(authorization: str | None = Header(default=None)) -> dict:
    """Consulta configuración, demanda, instancias y costo estimado."""
    require_admin(authorization)
    snapshot = await controller.build_snapshot()
    workers = []
    estimated_cost: float | None = None
    aws_error: str | None = None
    runtime: dict[str, Any] = {}
    try:
        fleet = controller._make_fleet()
        workers = await asyncio.to_thread(fleet.list_managed)
        estimated_cost = await asyncio.to_thread(fleet.estimate_cost, workers, utcnow())
        runtime = await asyncio.to_thread(fleet.instance_runtime)
    except Exception as exc:  # noqa: BLE001 - status view remains available without AWS
        aws_error = type(exc).__name__
    return {
        "success": True,
        "config": {
            **_safe_config(
                fleet=fleet if aws_error is None else None,
                runtime=runtime if aws_error is None else None,
            ),
            "avg_job_seconds": snapshot.get(
                "avg_job_seconds", controller._settings().autoscale_avg_job_seconds
            ),
            "boot_seconds": snapshot.get(
                "boot_seconds", controller._settings().autoscale_boot_estimate_seconds
            ),
            "ec2": ({
                "region": fleet.settings.region,
                "instance_type": fleet.settings.instance_type,
                "worker_port": fleet.settings.worker_port,
                "worker_concurrency": runtime.get("worker_concurrency")
                or getattr(fleet.settings, "effective_worker_concurrency", 5),
                "ram_gib": runtime.get("ram_gib"),
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


@router.post(
    "/autoscale/scale-up",
    summary="Aumentar la capacidad del autoscaler",
    description=(
        "Solicita iniciar la cantidad indicada de instancias efímeras. La cantidad "
        "debe estar entre 1 y 100 y la operación requiere un motivo de al menos 10 "
        "caracteres. Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_VALIDACION,
        **_ERROR_MOTIVO,
        200: _respuesta_json(
            "Solicitud procesada; launched contiene las instancias iniciadas.",
            {
                "success": True,
                "requested": 2,
                "launched": [
                    {"instance_id": "i-0123456789abcdef0", "node": "10.0.0.13:8080", "state": "running", "instance_type": "c6i.xlarge", "concurrency": 5},
                    {"instance_id": "i-abcdef0123456789", "node": "10.0.0.14:8080", "state": "running", "instance_type": "c6i.xlarge", "concurrency": 5},
                ],
            },
        ),
        503: {
            "description": (
                "No se pudo consultar o iniciar capacidad en el servicio de instancias. "
                "Revise su disponibilidad, permisos y configuración, y vuelva a intentar "
                "cuando el servicio esté operativo."
            )
        },
    },
)
async def autoscale_scale_up(
    body: ScaleUpBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Solicita nuevas instancias efímeras para ampliar la capacidad."""
    actor = require_admin(authorization)
    motivo = _require_reason(body.motivo)
    try:
        await controller.reconcile_managed()
        launched = await controller.launch_workers(
            body.count, reason=motivo, dry_run=False, manual=body.manual
        )
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


@router.post(
    "/autoscale/terminate",
    summary="Terminar instancias efímeras",
    description=(
        "Termina las instancias registradas que se indiquen en instance_ids o todas "
        "las registradas cuando all sea true. Debe usarse una sola modalidad de selección "
        "y aportar un motivo válido. Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_VALIDACION,
        200: _respuesta_json(
            "Resultado de la terminación de las instancias seleccionadas.",
            {
                "success": True,
                "terminated": ["i-0123456789abcdef0"],
                "draining": [],
                "pending": [],
                "ignored": [],
            },
        ),
        400: {
            "description": (
                "El motivo está vacío o no tiene entre 10 y 500 caracteres, o uno o más "
                "identificadores no corresponden a instancias efímeras registradas. "
                "Proporcione un motivo válido y consulte el inventario para enviar "
                "únicamente identificadores conocidos."
            )
        },
        503: {
            "description": (
                "No se pudo terminar las instancias en el servicio de capacidad. Revise "
                "su disponibilidad, permisos y configuración y vuelva a intentar."
            )
        },
    },
)
async def autoscale_terminate(
    body: TerminateBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Termina instancias efímeras registradas según la selección solicitada."""
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
        result = await controller.terminate_workers(targets, reason=motivo, mode=body.mode)
    except Exception as exc:  # noqa: BLE001
        log_event(
            "autoscaler.terminate", actor_id=actor, target_type="ec2-fleet", target_id="all",
            request_id=request_id or "", result="error", reason=motivo,
            metadata={"instance_ids": targets, "error": type(exc).__name__},
        )
        raise HTTPException(status_code=503, detail=f"AWS no disponible: {type(exc).__name__}") from exc
    log_event(
        "autoscaler.terminate", actor_id=actor, target_type="ec2-instance", target_id=",".join(targets),
        request_id=request_id or "", reason=motivo, metadata={**result, "mode": body.mode},
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


@router.post(
    "/autoscale/enable",
    summary="Habilitar el autoscaler",
    description=(
        "Activa el escalado automático y pone en marcha su ciclo de evaluación. "
        "El cuerpo debe incluir un motivo de al menos 10 caracteres. "
        "Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_VALIDACION,
        **_ERROR_MOTIVO,
        200: _respuesta_json("Autoscaler habilitado.", {"success": True, "enabled": True}),
    },
)
async def autoscale_enable(
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Habilita el ajuste automático de capacidad."""
    actor = require_admin(authorization)
    return await _set_enabled(True, body.motivo, actor, request_id or "")


@router.post(
    "/autoscale/disable",
    summary="Deshabilitar el autoscaler",
    description=(
        "Desactiva el escalado automático y detiene su ciclo de evaluación. "
        "El cuerpo debe incluir un motivo de al menos 10 caracteres. "
        "Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_VALIDACION,
        **_ERROR_MOTIVO,
        200: _respuesta_json("Autoscaler deshabilitado.", {"success": True, "enabled": False}),
    },
)
async def autoscale_disable(
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Deshabilita el ajuste automático de capacidad."""
    actor = require_admin(authorization)
    return await _set_enabled(False, body.motivo, actor, request_id or "")


@router.get(
    "/autoscale/audit",
    summary="Revisar instancias sin correspondencia",
    description=(
        "Compara las instancias administradas con las conocidas por el servicio y "
        "devuelve el informe de instancias huérfanas junto con eventos recientes. "
        "Si la consulta no está disponible, devuelve success=false y el tipo de error "
        "en lugar de interrumpir la respuesta. Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        200: _respuesta_json(
            "Informe de auditoría; audit_orphans y error informan si la revisión pudo completarse.",
            {"success": True, "audit_orphans": {"orphans": []}, "error": None, "eventos": []},
        ),
    },
)
async def autoscale_audit(authorization: str | None = Header(default=None)) -> dict:
    """Comprueba si existen instancias activas sin correspondencia registrada."""
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


def _policy_view(pending: int | None = None, snapshot: dict | None = None) -> dict:
    """Política efectiva, valores del entorno y estimación aproximada de instancias."""
    from central_api.autoscaler import state as policy_state

    env = get_settings()
    effective = controller._settings()
    values = {}
    for key, (attr, low, high) in policy_state.POLICY_FIELDS.items():
        values[key] = {
            "value": int(getattr(effective, attr)),
            "env": int(getattr(env, attr)),
            "overridden": key in policy_state.POLICY_OVERRIDES,
            "min": low,
            "max": high,
        }
    snapshot = snapshot or {}
    slots = int(snapshot.get("capacidad_nueva") or effective.autoscale_capacity_per_worker or 5)
    per_instance = int(effective.autoscale_jobs_per_worker) or slots
    avg_job = float(snapshot.get("avg_job_seconds") or effective.autoscale_avg_job_seconds)
    queue = int(pending if pending is not None else snapshot.get("pendientes", 0) or 0)
    fixed = int(snapshot.get("workers_fijos", 0) or 0)
    threshold = int(effective.autoscale_queue_threshold)
    cap = int(effective.autoscale_max_instances)
    if effective.autoscale_max_total_workers:
        cap = min(cap, max(0, int(effective.autoscale_max_total_workers) - fixed))
    needed = 0
    if queue > threshold:
        needed = min(cap, max(0, -(-queue // per_instance) - fixed))
    return {
        "values": values,
        "estimate": {
            "pending": queue,
            "trigger_when_pending_over": threshold,
            "requests_per_instance": per_instance,
            "slots_per_instance": slots,
            "avg_job_seconds": round(avg_job, 1),
            "estimated_instances": needed,
            "estimated_minutes_to_drain": round(
                (queue * avg_job) / max(1, (needed + fixed) * slots) / 60, 1
            ) if queue and (needed + fixed) else 0.0,
            "mode": "por conteo" if effective.autoscale_jobs_per_worker else "por tiempo (W/S/R/n)",
            "note": (
                "Estimación aproximada: instancias = ceil(cola / solicitudes por instancia) "
                "menos workers fijos, acotado por el máximo."
            ),
        },
    }


@router.get(
    "/autoscale/policy",
    summary="Consultar la política del autoscaler",
    description=(
        "Devuelve cada parámetro con su valor efectivo, el del entorno y si fue "
        "modificado desde el panel, más una estimación de instancias y de "
        "solicitudes por instancia. Con ?pending=N simula una cola distinta. "
        "Requiere autorización administrativa."
    ),
    responses={**_errores_admin()},
)
async def get_autoscale_policy(
    pending: int | None = None, authorization: str | None = Header(default=None)
) -> dict:
    require_admin(authorization)
    try:
        snapshot = await controller.build_snapshot()
    except Exception:  # noqa: BLE001 - la política debe poder verse sin métricas
        snapshot = {}
    return {"success": True, **_policy_view(pending, snapshot)}


@router.put(
    "/autoscale/policy",
    summary="Definir la política del autoscaler",
    description=(
        "Fija parámetros de política (umbral de cola, solicitudes por instancia, "
        "máximos, esperas) sin reiniciar el servicio. Se persisten en la base. "
        "'reset' devuelve parámetros al valor del entorno. Requiere motivo."
    ),
    responses={**_errores_admin(), **_ERROR_VALIDACION, **_ERROR_MOTIVO},
)
async def put_autoscale_policy(
    body: PolicyBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    from central_api.autoscaler import state as policy_state

    actor = require_admin(authorization)
    motivo = _require_reason(body.motivo)
    try:
        clean = policy_state.validate_policy(body.values)
        unknown = [key for key in body.reset if key not in policy_state.POLICY_FIELDS]
        if unknown:
            raise ValueError(f"parámetro de política desconocido: {unknown[0]}")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for key in body.reset:
        policy_state.POLICY_OVERRIDES.pop(key, None)
    policy_state.POLICY_OVERRIDES.update(clean)
    await policy_state.save_policy()
    log_event(
        "autoscaler.policy", actor_id=actor, target_type="autoscaler", target_id="policy",
        request_id=request_id or "", reason=motivo,
        metadata={"values": clean, "reset": body.reset},
    )
    try:
        snapshot = await controller.build_snapshot()
    except Exception:  # noqa: BLE001
        snapshot = {}
    return {"success": True, **_policy_view(None, snapshot)}
