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
    instance_type: str = Field(
        default="c5.xlarge",
        max_length=40,
        pattern=r"^[a-z0-9]+\.[a-z0-9]+$",
        description="Tipo EC2 (ver GET /admin/autoscale/instance-types). Por defecto c5.xlarge.",
        examples=["c5.xlarge"],
    )
    concurrency: int | None = Field(
        default=None,
        ge=1,
        le=10,
        description=(
            "Semaphore: jobs simultáneos que acepta cada worker (1 a 10). "
            "Sin valor se deduce de la RAM del tipo. Se rechaza si supera el "
            "máximo del tipo en esa zona."
        ),
        examples=[5],
    )
    zone: str | None = Field(
        default=None,
        max_length=20,
        description=(
            "Zona de lanzamiento (ver GET /admin/autoscale/zones). Sin valor se "
            "usa la zona activa del panel."
        ),
        examples=["bue"],
    )


class ZoneBody(BaseModel):
    zone: str = Field(
        min_length=1,
        max_length=20,
        description="Clave de la zona que pasa a estar activa (ver GET /admin/autoscale/zones).",
        examples=["sp"],
    )
    motivo: str = Field(
        description="Justificación de auditoría para cambiar la zona de lanzamiento.",
        examples=["Menor latencia hacia los clientes de São Paulo"],
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
    "error": "Tipo de error de la operación, o null si terminó correctamente.",
    "zone": (
        "Zona de lanzamiento: objeto con su clave, etiqueta, región, tipo de "
        "volumen y zonas de disponibilidad; en cada efímera, la zona a la que "
        "pertenece."
    ),
    "zones": "Zonas de lanzamiento disponibles con su catálogo de tipos.",
    "active": "Clave de la zona de lanzamiento activa.",
    "types": "Catálogo de tipos lanzables con vCPU, RAM y sizing por RAM.",
    "instances": "Cantidad de instancias efímeras registradas en la zona.",
    "label": "Nombre legible de la zona.",
    "region": "Región AWS de la zona.",
    "volume_type": "Tipo de volumen EBS de la zona (gp2 en Local Zones).",
    "availability_zones": "Zonas de disponibilidad donde se lanzan los workers.",
    "max_concurrency": "Semaphore máximo permitido (tope duro del worker: 10).",
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


def _zone_meta(zone_key: str) -> dict:
    """Metadatos de la zona (``ZONES``) sin tocar AWS ni sus settings."""
    from central_api.autoscaler.ec2 import ZONES

    meta = ZONES.get(zone_key) or {}
    return {
        "key": zone_key,
        "label": str(meta.get("label") or zone_key),
        "region": str(meta.get("region") or ""),
        "volume_type": str(meta.get("volume_type") or ""),
        "availability_zones": list(meta.get("availability_zones") or ()),
    }


def _resolver_zona(zone: str | None) -> str:
    """Valida la zona pedida y devuelve su clave (400 si no existe)."""
    try:
        return controller.resolve_zone(zone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _gb(value: Any) -> str:
    """Formatea GB con coma decimal para el mensaje de error."""
    try:
        return f"{float(value):g}".replace(".", ",")
    except (TypeError, ValueError):
        return str(value)


def _type_sizing(row: dict) -> dict:
    """Sizing del tipo: lo que informe el catálogo, completado con ``worker_sizing``.

    El catálogo ya trae ``recommended_min/max`` y ``max_concurrency``; si una
    flota de prueba no los informa, se derivan de su RAM.
    """
    from central_api.autoscaler.ec2 import worker_sizing

    try:
        ram = float(row.get("ram_gib") or 0)
    except (TypeError, ValueError):
        ram = 0.0
    sizing: dict[str, Any] = worker_sizing(ram) if ram > 0 else {}
    for key in (
        "recommended_min",
        "recommended_max",
        "max_concurrency",
        "gb_per_worker_min",
        "gb_per_worker_max",
    ):
        if row.get(key) is not None:
            sizing[key] = row[key]
    return sizing


def _validar_semaphore(concurrency: int | None, row: dict, instance_type: str, zone: str) -> None:
    """Rechaza (400) un semaphore por encima del máximo del tipo en esa zona.

    El máximo sale de ``worker_sizing``: cada worker consume entre 1,5 y 2 GB, y
    el worker nunca acepta más de 10 jobs simultáneos (invariante W-2).
    """
    from central_api.autoscaler.ec2 import MAX_WORKER_SEMAPHORE

    if concurrency is None:
        return
    sizing = _type_sizing(row)
    maximo = int(sizing.get("max_concurrency") or MAX_WORKER_SEMAPHORE)
    permitido = min(maximo, MAX_WORKER_SEMAPHORE)
    if int(concurrency) <= permitido:
        return
    ram = row.get("ram_gib")
    recomendado = sizing.get("recommended_max")
    detalle = (
        f"semaphore {concurrency} inválido para {instance_type} en la zona {zone}: "
        f"máximo {permitido} con {_gb(ram)} GiB de RAM"
    )
    if recomendado:
        detalle += f" (recomendado {sizing.get('recommended_min')}-{recomendado})"
    detalle += (
        f"; cada worker consume entre {_gb(sizing.get('gb_per_worker_min', 1.5))} y "
        f"{_gb(sizing.get('gb_per_worker_max', 2.0))} GB y nunca más de "
        f"{MAX_WORKER_SEMAPHORE} jobs simultáneos"
    )
    raise HTTPException(status_code=400, detail=detalle)


async def _tipos_de_zona(zone: str) -> tuple[list[dict], str | None]:
    """Catálogo de una zona (cacheado), tolerante a fallos de AWS (error por zona)."""
    try:
        return list(await controller.catalog_for(zone)), None
    except Exception as exc:  # noqa: BLE001 - el listado debe seguir disponible
        return [], type(exc).__name__


def _serialize_ephemeral(instance_id: str, item: dict) -> dict:
    result = dict(item)
    result.setdefault("instance_id", instance_id)
    # Una efímera adoptada antes de existir zonas no trae el dato: se informa la
    # activa, que es la usada para operarla.
    result.setdefault("zone", controller.record_zone(item))
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
                "zone": {"key": "bue", "label": "N. Virginia - Buenos Aires", "region": "us-east-1", "volume_type": "gp2", "availability_zones": ["us-east-1-bue-1a"], "instances": 1},
                "config": {"enabled": True, "dry_run": False, "queue_threshold": 10, "max_instances": 8, "capacity_per_worker": 5, "ec2": {"zone": "bue", "region": "us-east-1", "instance_type": "c6i.xlarge", "worker_concurrency": 5}},
                "snapshot": {"queued": 3, "running": 2},
                "efimeros": [{"instance_id": "i-0123456789abcdef0", "zone": "bue", "state": "running", "node": "10.0.0.13:8080"}],
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
    zone_key = controller.resolve_zone()
    workers = []
    estimated_cost: float | None = None
    aws_error: str | None = None
    runtime: dict[str, Any] = {}
    try:
        fleet = controller._make_fleet(zone_key)
        workers = await asyncio.to_thread(fleet.list_managed)
        estimated_cost = await asyncio.to_thread(fleet.estimate_cost, workers, utcnow())
        runtime = await asyncio.to_thread(fleet.instance_runtime)
    except Exception as exc:  # noqa: BLE001 - status view remains available without AWS
        aws_error = type(exc).__name__
    return {
        "success": True,
        "zone": {
            **_zone_meta(zone_key),
            "instances": controller.instances_in_zone(zone_key),
        },
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
                "zone": zone_key,
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
        "Solicita iniciar la cantidad indicada de instancias efímeras en una zona "
        "(la activa si no se indica). La cantidad debe estar entre 1 y 100 y la "
        "operación requiere un motivo de al menos 10 caracteres. El tipo debe "
        "existir en el catálogo de esa zona y el semaphore no puede superar su "
        "máximo. Requiere autorización administrativa."
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
                "zone": "bue",
                "launched": [
                    {"instance_id": "i-0123456789abcdef0", "zone": "bue", "node": "10.0.0.13:8080", "state": "running", "instance_type": "c6i.xlarge", "concurrency": 5},
                    {"instance_id": "i-abcdef0123456789", "zone": "bue", "node": "10.0.0.14:8080", "state": "running", "instance_type": "c6i.xlarge", "concurrency": 5},
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
    zone_key = _resolver_zona(body.zone)
    try:
        await controller.reconcile_managed()
        fleet = controller._make_fleet(zone_key)
        catalog = await asyncio.to_thread(fleet.list_instance_types)
        row = next(
            (item for item in catalog if item.get("instance_type") == body.instance_type),
            None,
        )
        if row is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"tipo {body.instance_type} no disponible en la zona {zone_key}; "
                    f"consulte /admin/autoscale/instance-types?zone={zone_key}"
                ),
            )
        # El semaphore se valida contra el catálogo de esa zona: por encima del
        # máximo del tipo la instancia no puede atender tantos jobs.
        _validar_semaphore(body.concurrency, row, body.instance_type, zone_key)
        launched = await controller.launch_workers(
            body.count, reason=motivo, dry_run=False, manual=body.manual,
            instance_type=body.instance_type, concurrency=body.concurrency,
            zone=zone_key,
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 - don't expose AWS error details
        log_event(
            "autoscaler.scale_up", actor_id=actor, target_type="ec2-fleet", target_id="all",
            request_id=request_id or "", result="error", reason=motivo,
            metadata={"count": body.count, "zone": zone_key, "error": type(exc).__name__},
        )
        raise HTTPException(status_code=503, detail=f"AWS no disponible: {type(exc).__name__}") from exc
    log_event(
        "autoscaler.scale_up", actor_id=actor, target_type="ec2-fleet", target_id="all",
        request_id=request_id or "", reason=motivo,
        metadata={"requested": body.count, "launched": len(launched), "zone": zone_key},
    )
    return {
        "success": True,
        "requested": body.count,
        "launched": launched,
        "zone": zone_key,
    }


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


@router.get(
    "/autoscale/instance-types",
    summary="Listar tipos de instancia disponibles",
    description=(
        "Tipos EC2 lanzables en una zona, con vCPU, RAM, el semaphore sugerido "
        "(jobs simultáneos por worker) y su sizing (mínimo/máximo recomendado y "
        "máximo permitido). Sin ?zone se usa la zona activa; el rango se explica "
        "por el consumo de 1,5 a 2 GB por worker. El tipo por defecto para altas "
        "manuales es c5.xlarge. Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        400: {
            "description": (
                "La zona indicada no existe. Consulte GET /admin/autoscale/zones "
                "para ver las zonas válidas."
            )
        },
    },
)
async def get_instance_types(
    zone: str | None = None, authorization: str | None = Header(default=None)
) -> dict:
    require_admin(authorization)
    zone_key = _resolver_zona(zone)
    rows, error = await _tipos_de_zona(zone_key)
    return {
        "success": error is None,
        "default": "c5.xlarge",
        "zone": zone_key,
        "active": controller.resolve_zone(),
        "types": rows,
        "max_concurrency": 10,
        "error": error,
    }


@router.get(
    "/autoscale/zones",
    summary="Listar zonas de lanzamiento",
    description=(
        "Devuelve la zona activa y, por cada zona, su región, tipo de volumen, "
        "zonas de disponibilidad, cuántas instancias efímeras tiene registradas y "
        "el catálogo de tipos con su sizing. Responde rápido: el catálogo solo se "
        "incluye con ?with_types=true (usar GET /admin/autoscale/instance-types). "
        "Si el catálogo de una zona no se puede consultar, esa zona informa su "
        "error y las demás siguen disponibles. Requiere autorización "
        "administrativa."
    ),
    responses={**_errores_admin()},
)
async def get_zones(
    with_types: bool = False, authorization: str | None = Header(default=None)
) -> dict:
    require_admin(authorization)
    zones = []
    for key in controller.known_zones():
        if with_types:
            rows, error = await _tipos_de_zona(key)
        else:
            rows, error = [], None
        zones.append({
            **_zone_meta(key),
            "instances": controller.instances_in_zone(key),
            "types": rows,
            "error": error,
        })
    return {
        "success": True,
        "active": controller.resolve_zone(),
        "zones": zones,
        "max_concurrency": 10,
        "with_types": with_types,
    }


@router.put(
    "/autoscale/zone",
    summary="Definir la zona de lanzamiento activa",
    description=(
        "Cambia la zona donde el autoscaler y las altas manuales lanzan "
        "instancias nuevas; se persiste y queda auditado. Las instancias ya "
        "creadas siguen en su zona. Requiere motivo de al menos 10 caracteres."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_MOTIVO,
        400: {
            "description": (
                "La zona indicada no existe. Consulte GET /admin/autoscale/zones "
                "para ver las zonas válidas."
            )
        },
    },
)
async def put_autoscale_zone(
    body: ZoneBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    from central_api.autoscaler import state as zone_state

    actor = require_admin(authorization)
    motivo = _require_reason(body.motivo)
    previous = controller.resolve_zone()
    try:
        active = await zone_state.set_zone(body.zone)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log_event(
        "autoscaler.zone", actor_id=actor, target_type="autoscaler", target_id="zone",
        request_id=request_id or "", reason=motivo,
        metadata={"zone": active, "previous": previous},
    )
    return {
        "success": True,
        "active": active,
        "zone": {
            **_zone_meta(active),
            "instances": controller.instances_in_zone(active),
        },
    }
