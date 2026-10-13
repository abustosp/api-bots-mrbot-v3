"""Flota del panel (R11): dashboard, detector de alertas y acciones de drenaje.

Reutiliza ``derive_state`` y ``worker_overview`` de
``central_api.admin.workers``: el estado visible de cada nodo (SANO,
SATURADO, DEGRADADO, CAIDO, DRENANDO, REGISTRANDO) sale de esa única
máquina de estados. Este módulo agrega:

- ``GET /admin/fleet``: tabla accionable con capacidad, jobs, heartbeat,
  protocolo, bots y tasas de error de la ventana disponible.
- Detector de alertas con umbrales versionados desde settings, deduplificación
  por ``(tipo, worker)``, resumen máximo cada 30 minutos (anti-flapping) y
  recuperación tras dos heartbeats sanos consecutivos.
- Reconocimiento con comentario obligatorio, silencio con vencimiento máximo
  de 4 horas, drenaje y retiro de workers.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from itertools import count
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, create_model

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event, nuevo_uuid7
from central_api.admin.workers import derive_state, worker_overview
from central_api.settings import get_settings
from central_api.store import EPHEMERAL_WORKERS, JOBS, WORKERS, WorkerEntry, utcnow
from central_api.worker_nodes import merge_nodes

router = APIRouter()

# Los registros efímeros legacy no guardaban el semaphore; el default histórico
# de EC2 era 5 y no debe seguir un worker_capacity global que ahora puede ser 10.
DEFAULT_EPHEMERAL_CONCURRENCY = 5
_SEMVER = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$"
)


def _version_sort_key(version: str, digest: str = "") -> tuple:
    """Ordena SemVer numericamente; etiquetas opacas usan orden textual."""
    texto = version.strip()
    match = _SEMVER.fullmatch(texto)
    if match is None:
        return (0, texto.casefold(), digest.casefold())
    prerelease = match.group(4)
    prerelease_key = tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in (prerelease.split(".") if prerelease else [])
    )
    return (
        1,
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        1 if prerelease is None else 0,
        prerelease_key,
        digest.casefold(),
        texto.casefold(),
    )


def _latest_version() -> tuple[str | None, str | None, str | None]:
    """Retorna (latest, digest, versión de app) desde settings o flota activa."""
    expected = get_settings().worker_latest_version.strip()
    if expected:
        if expected.startswith("sha256:"):
            return expected, expected, None
        if "@" in expected:
            version, digest = expected.split("@", 1)
            return version or digest, digest or None, version or None
        return expected, None, expected

    reported = [
        (worker.app_version.strip(), worker.image_digest.strip())
        for worker in WORKERS.values()
        if worker.app_version.strip() or worker.image_digest.strip()
    ]
    if not reported:
        return None, None, None
    app_version, digest = max(
        reported,
        key=lambda item: _version_sort_key(item[0] or item[1], item[1]),
    )
    return app_version or digest, digest or None, app_version or None


def _version_fields(entrada: WorkerEntry | None) -> dict:
    app_version = str(getattr(entrada, "app_version", "") or "").strip()
    digest = str(getattr(entrada, "image_digest", "") or "").strip()
    latest, latest_digest, latest_app = _latest_version()
    if latest is None:
        is_latest = None
    elif latest_app is None:
        is_latest = digest == latest_digest
    else:
        is_latest = app_version == latest_app and (
            latest_digest is None or digest == latest_digest
        )
    return {
        "version": app_version or digest or None,
        "app_version": app_version or None,
        "image_digest": digest or None,
        "latest_version": latest,
        "latest_digest": latest_digest,
        "is_latest": is_latest,
        "outdated": None if is_latest is None else not is_latest,
    }


def _version_summary(filas: list[dict]) -> dict:
    latest, latest_digest, _ = _latest_version()
    return {
        "latest_version": latest,
        "latest_digest": latest_digest,
        "total": len(filas),
        "outdated": sum(1 for fila in filas if fila.get("outdated") is True),
        "unknown": sum(1 for fila in filas if not fila.get("version")),
    }


@dataclass
class Alert:
    """Alerta de flota con ciclo de vida Abierta → Notificada → Resuelta."""

    id: str
    tipo: str
    worker: str
    severidad: str
    estado: str = "ABIERTA"
    contador: int = 1
    first_seen: str = ""
    last_seen: str = ""
    last_notified: str = ""
    sanos_consecutivos: int = 0
    reconocida_por: str = ""
    comentario: str = ""
    silenciada_hasta: str = ""
    historial: list[dict] = field(default_factory=list)


ALERTS: dict[str, Alert] = {}


def _clave_alerta(tipo: str, worker: str) -> str:
    return f"{tipo}:{worker}"


def _abrir_o_resumir(tipo: str, worker: str, severidad: str) -> Alert:
    """Deduplifica por (tipo, worker): repite contador, no crea alerta nueva."""
    settings = get_settings()
    ahora = utcnow()
    clave = _clave_alerta(tipo, worker)
    alerta = ALERTS.get(clave)
    if alerta is not None and alerta.estado != "RESUELTA":
        alerta.contador += 1
        alerta.last_seen = ahora.isoformat()
        alerta.sanos_consecutivos = 0
        ventana = timedelta(minutes=settings.fleet_alert_resumen_minutes)
        ultimo = alerta.last_notified
        if not ultimo or (ahora - _parse_fecha(ultimo)) >= ventana:
            alerta.last_notified = ahora.isoformat()
            alerta.estado = "NOTIFICADA"
            alerta.historial.append({"evento": "renotificada", "en": ahora.isoformat()})
        return alerta
    alerta = Alert(
        id=nuevo_uuid7(), tipo=tipo, worker=worker, severidad=severidad,
        estado="NOTIFICADA", first_seen=ahora.isoformat(),
        last_seen=ahora.isoformat(), last_notified=ahora.isoformat(),
        historial=[{"evento": "abierta", "en": ahora.isoformat()}],
    )
    ALERTS[clave] = alerta
    return alerta


def _parse_fecha(valor: str):
    from datetime import datetime

    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        return utcnow() - timedelta(days=365)


def evaluar_flota() -> list[dict]:
    """Corre el detector sobre el inventario unido y devuelve alertas activas."""
    settings = get_settings()
    nodos = merge_nodes(settings.worker_node_list, _admin_nodes())
    activas: list[dict] = []
    for nodo in nodos:
        entrada = WORKERS.get(nodo)
        estado = derive_state(entrada)
        if estado == "CAIDO":
            activas.append(asdict(_abrir_o_resumir("worker_caido", nodo, "critica")))
        elif estado == "SATURADO":
            activas.append(asdict(_abrir_o_resumir("worker_saturado", nodo, "advertencia")))
        elif estado == "DEGRADADO":
            activas.append(asdict(_abrir_o_resumir("worker_degradado", nodo, "advertencia")))
        else:
            _marcar_sano(nodo)
    _ = settings
    return activas


def _marcar_sano(nodo: str) -> None:
    """Suma un heartbeat sano; cierra la alerta tras dos consecutivos."""
    ahora = utcnow().isoformat()
    for clave, alerta in ALERTS.items():
        if alerta.worker != nodo or alerta.estado == "RESUELTA":
            continue
        alerta.sanos_consecutivos += 1
        if alerta.sanos_consecutivos >= 2:
            alerta.estado = "RESUELTA"
            alerta.historial.append({"evento": "resuelta", "en": ahora})


def _admin_nodes() -> set[str]:
    from central_api.store import ADMIN_NODES

    return ADMIN_NODES


def _vista_fila(nodo: str) -> dict:
    base = worker_overview(nodo)
    entrada = WORKERS.get(nodo)
    settings = get_settings()
    activos = [j for j in JOBS.values() if j.worker_node == nodo and j.status in ("ASIGNADO", "CORRIENDO")]
    terminales = [j for j in JOBS.values() if j.worker_node == nodo and j.status in ("COMPLETO", "FALLIDO")]
    fallidos = sum(1 for j in terminales if j.status == "FALLIDO")
    tasa_error = (fallidos / len(terminales)) if terminales else 0.0
    from central_api.store import EPHEMERAL_WORKERS

    ephemeral = next(
        (item for item in EPHEMERAL_WORKERS.values() if item.get("node") == nodo),
        None,
    )
    return {
        **base,
        **_version_fields(entrada),
        "origen": "efimero" if ephemeral is not None else base.get("origen"),
        "instance_id": ephemeral.get("instance_id") if ephemeral else None,
        "protocolo": settings.worker_protocol_version,
        "protocolo_estado": "compatible",
        "bots": list(entrada.capabilities) if entrada else [],
        "jobs_activos": len(activos),
        "tasa_error": round(tasa_error, 4),
        "muestra_error": {"total": len(terminales), "fallidos": fallidos},
        "alertas": [asdict(a) for a in ALERTS.values() if a.worker == nodo and a.estado != "RESUELTA"],
    }


def _parse_instante(valor: object) -> datetime | None:
    """Normaliza timestamps del store sin asumir que aún sean serializados."""
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=timezone.utc) if valor.tzinfo is None else valor
    if isinstance(valor, str) and valor:
        try:
            instante = datetime.fromisoformat(valor.replace("Z", "+00:00"))
            return instante.replace(tzinfo=timezone.utc) if instante.tzinfo is None else instante
        except ValueError:
            return None
    return None


def _vista_efimero(instance_id: str, item: dict) -> dict:
    """Fila autoscaler incluso mientras la instancia todavía no tiene nodo."""
    node = str(item.get("node") or "")
    base = _vista_fila(node) if node else {
        "node": "",
        "estado": "REGISTRANDO",
        "en_ejecucion": 0,
        "jobs_activos": 0,
        "capacidad": DEFAULT_EPHEMERAL_CONCURRENCY,
        "alertas": [],
        **_version_fields(None),
    }
    entrada = WORKERS.get(node) if node else None
    ec2_estado = str(item.get("state") or "").strip().lower()
    if entrada is None:
        if ec2_estado == "stopping":
            estado = "DRENANDO"
        elif ec2_estado in {"stopped", "terminated", "shutting-down"}:
            estado = ec2_estado.upper()
        else:
            estado = "REGISTRANDO"
    else:
        estado = base["estado"]

    lanzado = _parse_instante(item.get("launched_at"))
    edad = None
    if lanzado is not None:
        edad = max(0, int((utcnow() - lanzado).total_seconds()))
    concurrency = item.get("concurrency")
    if concurrency is None:
        concurrency = item.get("worker_concurrency")
    if concurrency is None:
        concurrency = base.get("capacidad", DEFAULT_EPHEMERAL_CONCURRENCY)
    return {
        **base,
        "node": node,
        "origen": "efimero",
        "instance_id": item.get("instance_id") or instance_id,
        "instance_type": item.get("instance_type"),
        "ram_gib": item.get("ram_gib"),
        "concurrency": concurrency,
        "launched_at": lanzado.isoformat() if lanzado else None,
        "edad": edad,
        "estado": estado,
        "state_ec2": ec2_estado or None,
        "jobs_activos": base.get("jobs_activos", 0),
        "en_ejecucion": base.get("en_ejecucion", 0),
        "capacidad": concurrency,
    }


def _costo_estimado_efimeros() -> float:
    """Estimación local por tiempo de vida, sin llamadas de red a AWS."""
    from central_api.autoscaler.ec2 import Ec2Fleet

    tarifas = Ec2Fleet._ON_DEMAND_USD_HOUR_SA_EAST_1
    tarifa_fallback = tarifas.get("c6i.xlarge", 0.0)
    ahora = utcnow()
    total = 0.0
    for item in EPHEMERAL_WORKERS.values():
        if str(item.get("state") or "running").lower() not in {"pending", "running", "stopping"}:
            continue
        lanzado = _parse_instante(item.get("launched_at"))
        if lanzado is None:
            continue
        tipo = item.get("instance_type")
        tarifa = tarifas.get(str(tipo), tarifa_fallback)
        segundos = max(60.0, (ahora - lanzado).total_seconds())
        total += tarifa * segundos / 3600.0
    return round(total, 6)


def _resumen_filas(filas: list[dict]) -> dict:
    return {
        "total": len(filas),
        "sanos": sum(1 for fila in filas if fila.get("estado") == "SANO"),
        "capacidad": sum(int(fila.get("capacidad") or 0) for fila in filas),
        "en_ejecucion": sum(
            int(fila.get("jobs_activos", fila.get("en_ejecucion", 0)) or 0)
            for fila in filas
        ),
    }


class ComentarioBody(BaseModel):
    comentario: str = Field(
        min_length=3,
        max_length=500,
        description="Comentario que deja constancia del reconocimiento de la alerta.",
        examples=["Revisando el nodo y coordinando su recuperación."],
    )


class SilencioBody(BaseModel):
    horas: float = Field(
        gt=0,
        description="Duración del silencio en horas; no puede superar el máximo configurado.",
        examples=[1.5],
    )
    motivo: str = Field(
        default="",
        description="Motivo de auditoría para silenciar las notificaciones.",
        examples=["Ventana de mantenimiento programada"],
    )


class MotivoFleetBody(BaseModel):
    motivo: str = Field(
        default="",
        description="Motivo de auditoría de la operación sobre el worker.",
        examples=["Mantenimiento programado del nodo"],
    )


_SECUENCIA_MODELOS_OPENAPI = count(1)
_DESCRIPCIONES_CAMPOS_RESPUESTA = {
    "success": "Indica que la consulta o acción terminó correctamente.",
    "latest_version": "Versión de worker más reciente conocida por la flota.",
    "latest_digest": "Identificador de imagen más reciente conocido por la flota.",
    "flota": "Inventario consolidado de workers manuales y efímeros.",
    "manuales": "Workers administrados fuera del grupo efímero.",
    "efimeros": "Workers efímeros y datos de su instancia.",
    "resumen": "Totales de capacidad, estado y versiones de la flota.",
    "worker": "Estado, capacidad y métricas actuales del worker.",
    "asignaciones_activas": "Identificadores de trabajos activos en el worker.",
    "ultimos_terminales": "Identificadores de los trabajos finalizados más recientes.",
    "compatibilidad": "Versión de protocolo y resultado de compatibilidad.",
    "alertas": "Alertas encontradas, con su estado e historial.",
    "activas": "Alertas activas detectadas durante la evaluación.",
    "alerta": "Alerta actualizada con su historial de cambios.",
    "restantes": "Cantidad de trabajos que aún permanecen en el worker drenado.",
    "estado": "Nuevo estado operativo asignado al worker.",
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
        f"FleetOpenApiResponse{next(_SECUENCIA_MODELOS_OPENAPI)}", **campos
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
            "El cuerpo no cumple el formato o los límites indicados. Revise los "
            "campos obligatorios, longitudes y valores numéricos y vuelva a enviar la solicitud."
        )
    }
}


@router.get(
    "/fleet",
    summary="Consultar el estado de la flota",
    description=(
        "Devuelve los workers manuales y efímeros, su estado operativo, capacidad, "
        "trabajos, alertas, versiones reportadas y resúmenes de flota y costo estimado. "
        "Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        200: _respuesta_json(
            "Inventario y resúmenes de la flota.",
            {
                "success": True,
                "latest_version": "2.4.1",
                "latest_digest": "sha256:abc123",
                "flota": [{"node": "10.0.0.13:8080", "estado": "SANO", "capacidad": 5}],
                "manuales": [{"node": "10.0.0.13:8080", "estado": "SANO"}],
                "efimeros": [],
                "resumen": {
                    "manuales": {"total": 1, "sanos": 1, "capacidad": 5, "en_ejecucion": 1},
                    "efimeros": {"total": 0, "sanos": 0, "capacidad": 0, "en_ejecucion": 0, "costo_estimado_usd": 0.0},
                    "versiones": {"latest_version": "2.4.1", "latest_digest": "sha256:abc123", "total": 1, "outdated": 0, "unknown": 0},
                },
            },
        ),
    },
)
def ver_flota(authorization: str | None = Header(default=None)) -> dict:
    """Entrega el inventario consolidado, el estado y las métricas de cada worker."""
    require_admin(authorization)
    settings = get_settings()
    nodos = merge_nodes(settings.worker_node_list, _admin_nodes())
    # ``flota`` mantiene el contrato previo, incluyendo nodos efímeros que el
    # controller también conserva en ADMIN_NODES para compatibilidad histórica.
    flota = [_vista_fila(n) for n in nodos]
    nodos_efimeros = {
        str(item.get("node") or "")
        for item in EPHEMERAL_WORKERS.values()
        if item.get("node")
    }
    manuales = [
        fila for fila in flota
        if fila["node"] not in nodos_efimeros
    ]
    efimeros = [
        _vista_efimero(instance_id, item)
        for instance_id, item in EPHEMERAL_WORKERS.items()
    ]
    resumen_manuales = _resumen_filas(manuales)
    resumen_efimeros = _resumen_filas(efimeros)
    resumen_efimeros["costo_estimado_usd"] = _costo_estimado_efimeros()
    resumen_versiones = _version_summary(manuales + efimeros)
    return {
        "success": True,
        "latest_version": resumen_versiones["latest_version"],
        "latest_digest": resumen_versiones["latest_digest"],
        "flota": flota,
        "manuales": manuales,
        "efimeros": efimeros,
        "resumen": {
            "manuales": resumen_manuales,
            "efimeros": resumen_efimeros,
            "versiones": resumen_versiones,
        },
    }


@router.get(
    "/fleet/workers/{node}",
    summary="Consultar el detalle de un worker",
    description=(
        "Muestra el estado y las métricas del worker registrado, sus trabajos "
        "activos, los últimos trabajos finalizados y la compatibilidad de protocolo. "
        "Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        200: _respuesta_json(
            "Detalle del worker solicitado.",
            {
                "success": True,
                "worker": {"node": "10.0.0.13:8080", "estado": "SANO", "capacidad": 5, "jobs_activos": 1},
                "asignaciones_activas": ["job-123"],
                "ultimos_terminales": ["job-122"],
                "compatibilidad": {"protocolo": "1", "estado": "compatible"},
            },
        ),
        404: {
            "description": (
                "No existe un worker registrado con ese identificador. Verifique el "
                "valor de node y consulte primero el inventario de la flota."
            )
        },
    },
)
def ver_worker(node: str, authorization: str | None = Header(default=None)) -> dict:
    """Entrega las métricas, asignaciones e historial reciente del worker."""
    require_admin(authorization)
    entrada = WORKERS.get(node)
    if entrada is None:
        raise HTTPException(status_code=404, detail="worker no registrado")
    activos = [j.id for j in JOBS.values() if j.worker_node == node and j.status in ("ASIGNADO", "CORRIENDO")]
    terminales = [j.id for j in JOBS.values() if j.worker_node == node and j.status in ("COMPLETO", "FALLIDO", "CANCELADO")][-10:]
    return {
        "success": True,
        "worker": _vista_fila(node),
        "asignaciones_activas": activos,
        "ultimos_terminales": terminales,
        "compatibilidad": {"protocolo": get_settings().worker_protocol_version, "estado": "compatible"},
    }


@router.get(
    "/fleet/alerts",
    summary="Listar alertas de la flota",
    description=(
        "Devuelve las alertas conocidas y su historial, ordenadas con las alertas "
        "no resueltas antes que las resueltas. El parámetro estado filtra por el "
        "estado exacto. Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        200: _respuesta_json(
            "Alertas que coinciden con el filtro solicitado.",
            {"success": True, "alertas": [{"id": "019abc", "tipo": "worker_degradado", "worker": "10.0.0.13:8080", "severidad": "advertencia", "estado": "NOTIFICADA", "contador": 1, "historial": []}]},
        ),
    },
)
def listar_alertas(
    authorization: str | None = Header(default=None), estado: str = ""
) -> dict:
    """Lista las alertas de la flota, opcionalmente filtradas por estado."""
    require_admin(authorization)
    alertas = list(ALERTS.values())
    if estado:
        alertas = [a for a in alertas if a.estado == estado]
    alertas.sort(key=lambda a: (a.estado == "RESUELTA", a.last_seen), reverse=False)
    return {"success": True, "alertas": [asdict(a) for a in alertas]}


@router.post(
    "/fleet/evaluate",
    summary="Evaluar el estado de la flota",
    description=(
        "Ejecuta una evaluación inmediata de los nodos conocidos con los umbrales "
        "configurados y devuelve las alertas activas detectadas. La operación queda "
        "registrada en la auditoría y requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        200: _respuesta_json(
            "Resultado de la evaluación inmediata.",
            {"success": True, "activas": [{"id": "019abc", "tipo": "worker_caido", "worker": "10.0.0.13:8080", "severidad": "critica", "estado": "NOTIFICADA", "contador": 1}]},
        ),
    },
)
def forzar_evaluacion(
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Evalúa la flota ahora y devuelve las alertas activas resultantes."""
    actor = require_admin(authorization)
    activas = evaluar_flota()
    log_event(
        "fleet.evaluated", actor_id=actor, target_type="fleet", target_id="all",
        request_id=request_id or "", metadata={"activas": len(activas)},
    )
    return {"success": True, "activas": activas}


@router.post(
    "/fleet/alerts/{alerta_id}/ack",
    summary="Reconocer una alerta",
    description=(
        "Asocia el administrador y el comentario proporcionado a una alerta. "
        "El reconocimiento deja constancia, pero no cambia el estado técnico "
        "de la alerta. Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_VALIDACION,
        200: _respuesta_json(
            "Alerta reconocida con el comentario aportado.",
            {"success": True, "alerta": {"id": "019abc", "tipo": "worker_degradado", "worker": "10.0.0.13:8080", "estado": "NOTIFICADA", "reconocida_por": "admin:token", "comentario": "Revisando el nodo y coordinando su recuperación."}},
        ),
        404: {
            "description": (
                "No se encontró una alerta con ese identificador. Confirme el ID "
                "consultando la lista actual de alertas."
            )
        },
    },
)
def reconocer_alerta(
    alerta_id: str,
    body: ComentarioBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Registra quién reconoce la alerta y el comentario asociado."""
    actor = require_admin(authorization)
    alerta = next((a for a in ALERTS.values() if a.id == alerta_id), None)
    if alerta is None:
        raise HTTPException(status_code=404, detail="alerta no encontrada")
    alerta.reconocida_por = actor
    alerta.comentario = body.comentario
    alerta.historial.append({"evento": "reconocida", "por": actor, "nota": body.comentario})
    log_event(
        "fleet.alert.acknowledged", actor_id=actor, target_type="alert",
        target_id=alerta.id, request_id=request_id or "",
        metadata={"tipo": alerta.tipo, "worker": alerta.worker},
    )
    return {"success": True, "alerta": asdict(alerta)}


@router.post(
    "/fleet/alerts/{alerta_id}/silence",
    summary="Silenciar temporalmente una alerta",
    description=(
        "Define por cuántas horas se silencian las notificaciones de una alerta, "
        "hasta el máximo permitido por la configuración, y registra el motivo. "
        "El silencio no resuelve la alerta. Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_VALIDACION,
        200: _respuesta_json(
            "Alerta silenciada hasta la fecha indicada.",
            {"success": True, "alerta": {"id": "019abc", "tipo": "worker_degradado", "worker": "10.0.0.13:8080", "estado": "NOTIFICADA", "silenciada_hasta": "2026-10-12T18:00:00+00:00"}},
        ),
        400: {
            "description": (
                "El motivo está vacío o fuera de longitud, o las horas solicitadas "
                "superan el máximo configurado. Proporcione un motivo válido y una "
                "duración positiva dentro del límite permitido."
            )
        },
        404: {
            "description": (
                "No se encontró una alerta con ese identificador. Confirme el ID "
                "consultando la lista actual de alertas."
            )
        },
    },
)
def silenciar_alerta(
    alerta_id: str,
    body: SilencioBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Silencia temporalmente las notificaciones de una alerta."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    maximo = get_settings().fleet_silence_max_hours
    if body.horas > maximo:
        raise HTTPException(status_code=400, detail=f"silencio máximo de {maximo} horas")
    alerta = next((a for a in ALERTS.values() if a.id == alerta_id), None)
    if alerta is None:
        raise HTTPException(status_code=404, detail="alerta no encontrada")
    alerta.silenciada_hasta = (utcnow() + timedelta(hours=body.horas)).isoformat()
    alerta.historial.append({"evento": "silenciada", "por": actor, "motivo": motivo})
    log_event(
        "fleet.alert.silenced", actor_id=actor, target_type="alert",
        target_id=alerta.id, request_id=request_id or "", reason=motivo,
        metadata={"horas": body.horas},
    )
    return {"success": True, "alerta": asdict(alerta)}


@router.post(
    "/fleet/workers/{node}/drain",
    summary="Drenar un worker",
    description=(
        "Marca el worker como DRENANDO para evitar nuevas asignaciones y devuelve "
        "cuántos trabajos asignados o en ejecución quedan por terminar. No cancela "
        "los trabajos existentes. Requiere autorización administrativa y motivo."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_VALIDACION,
        200: _respuesta_json(
            "Worker marcado para drenaje.",
            {"success": True, "worker": "10.0.0.13:8080", "estado": "DRENANDO", "restantes": 2},
        ),
        400: {
            "description": (
                "El motivo es obligatorio y debe tener entre 10 y 500 caracteres. "
                "Indique una justificación válida para la operación."
            )
        },
        404: {
            "description": (
                "El worker no está registrado. Revise el identificador del nodo y "
                "consulte el inventario antes de solicitar el drenaje."
            )
        },
    },
)
def drenar_worker(
    node: str,
    body: MotivoFleetBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Impide nuevas asignaciones al worker y conserva los trabajos en curso."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    entrada = WORKERS.get(node)
    if entrada is None:
        raise HTTPException(status_code=404, detail="worker no registrado")
    entrada.status = "DRENANDO"
    restantes = sum(
        1 for j in JOBS.values()
        if j.worker_node == node and j.status in ("ASIGNADO", "CORRIENDO")
    )
    log_event(
        "fleet.worker.drained", actor_id=actor, target_type="worker", target_id=node,
        request_id=request_id or "", reason=motivo,
        metadata={"restantes": restantes},
    )
    return {"success": True, "worker": node, "estado": "DRENANDO", "restantes": restantes}


@router.post(
    "/fleet/workers/{node}/retire",
    summary="Retirar un worker",
    description=(
        "Marca como RETIRADO un worker registrado que no tenga trabajos asignados "
        "ni en ejecución. Si hay trabajos en vuelo, drene primero el worker y espere "
        "a que termine. Requiere autorización administrativa y motivo."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_VALIDACION,
        200: _respuesta_json(
            "Worker retirado.",
            {"success": True, "worker": "10.0.0.13:8080", "estado": "RETIRADO"},
        ),
        400: {
            "description": (
                "El motivo es obligatorio y debe tener entre 10 y 500 caracteres. "
                "Indique una justificación válida para la operación."
            )
        },
        404: {
            "description": (
                "El worker no está registrado. Revise el identificador del nodo y "
                "consulte el inventario antes de solicitar el retiro."
            )
        },
        409: {
            "description": (
                "El worker todavía tiene trabajos asignados o en ejecución. Drene el "
                "worker, espere a que el contador de trabajos pendientes llegue a cero "
                "y repita la solicitud."
            )
        },
    },
)
def retirar_worker(
    node: str,
    body: MotivoFleetBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Retira un worker sin trabajos en vuelo y conserva la auditoría."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    entrada = WORKERS.get(node)
    if entrada is None:
        raise HTTPException(status_code=404, detail="worker no registrado")
    en_vuelo = sum(
        1 for j in JOBS.values()
        if j.worker_node == node and j.status in ("ASIGNADO", "CORRIENDO")
    )
    if en_vuelo:
        raise HTTPException(status_code=409, detail="worker con jobs en vuelo, drene primero")
    entrada.status = "RETIRADO"
    log_event(
        "fleet.worker.retired", actor_id=actor, target_type="worker", target_id=node,
        request_id=request_id or "", reason=motivo,
    )
    return {"success": True, "worker": node, "estado": "RETIRADO"}
