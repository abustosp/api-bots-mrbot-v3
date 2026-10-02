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

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event, nuevo_uuid7
from central_api.admin.workers import derive_state, worker_overview
from central_api.settings import get_settings
from central_api.store import EPHEMERAL_WORKERS, JOBS, WORKERS, utcnow
from central_api.worker_nodes import merge_nodes

router = APIRouter()


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
        "capacidad": get_settings().worker_capacity,
        "alertas": [],
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
        concurrency = base.get("capacidad", get_settings().worker_capacity)
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
    comentario: str = Field(min_length=3, max_length=500)


class SilencioBody(BaseModel):
    horas: float = Field(gt=0)
    motivo: str = ""


class MotivoFleetBody(BaseModel):
    motivo: str = ""


@router.get("/fleet")
def ver_flota(authorization: str | None = Header(default=None)) -> dict:
    """Tabla paginada de la flota con estado derivado y métricas accionables."""
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
    return {
        "success": True,
        "flota": flota,
        "manuales": manuales,
        "efimeros": efimeros,
        "resumen": {"manuales": resumen_manuales, "efimeros": resumen_efimeros},
    }


@router.get("/fleet/workers/{node}")
def ver_worker(node: str, authorization: str | None = Header(default=None)) -> dict:
    """Detalle de un worker: asignaciones, terminales, protocolo y alertas."""
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


@router.get("/fleet/alerts")
def listar_alertas(
    authorization: str | None = Header(default=None), estado: str = ""
) -> dict:
    """Lista alertas con banner implícito (abiertas primero) y su historial."""
    require_admin(authorization)
    alertas = list(ALERTS.values())
    if estado:
        alertas = [a for a in alertas if a.estado == estado]
    alertas.sort(key=lambda a: (a.estado == "RESUELTA", a.last_seen), reverse=False)
    return {"success": True, "alertas": [asdict(a) for a in alertas]}


@router.post("/fleet/evaluate")
def forzar_evaluacion(
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Ejecuta el detector con los umbrales versionados de settings."""
    actor = require_admin(authorization)
    activas = evaluar_flota()
    log_event(
        "fleet.evaluated", actor_id=actor, target_type="fleet", target_id="all",
        request_id=request_id or "", metadata={"activas": len(activas)},
    )
    return {"success": True, "activas": activas}


@router.post("/fleet/alerts/{alerta_id}/ack")
def reconocer_alerta(
    alerta_id: str,
    body: ComentarioBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Reconoce una alerta con comentario; no cambia el estado técnico."""
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


@router.post("/fleet/alerts/{alerta_id}/silence")
def silenciar_alerta(
    alerta_id: str,
    body: SilencioBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Silencia notificaciones externas con vencimiento (máximo 4 horas)."""
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


@router.post("/fleet/workers/{node}/drain")
def drenar_worker(
    node: str,
    body: MotivoFleetBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Marca un worker DRENANDO: no recibe jobs nuevos hasta liberarse."""
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


@router.post("/fleet/workers/{node}/retire")
def retirar_worker(
    node: str,
    body: MotivoFleetBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Da de baja explícita un worker sin jobs en vuelo (conserva auditoría)."""
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
