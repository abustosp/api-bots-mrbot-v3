"""Panel de workers: alta de IPs en caliente + monitoreo de la flota.

- ``GET /admin/workers``: inventario (env + panel) con estado derivado por
  nodo: origen, registrado, estado, ejecución/capacidad, último latido.
- ``POST /admin/workers``: agrega una IP:puerto al inventario en caliente
  (sondea al worker best-effort e informa si responde). Mutación protegida
  con ``ADMIN_TOKEN`` (bearer); sin token configurado es 403.
- ``DELETE /admin/workers/{nodo}``: quita el nodo del inventario del panel
  y marca su entrada DRENANDO para que el scheduler deje de asignarle.

El estado deriva de los umbrales ya configurados
(``worker_degraded_after_seconds`` / ``worker_down_after_seconds``), igual
criterio que el selector: SANO+SATURADO exigen latido fresco.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta
from itertools import count
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field, create_model

from central_api.db import db_configurado, nueva_sesion
from central_api.scheduler import dispatcher
from central_api.settings import get_settings
from central_api.store import (
    ADMIN_NODES,
    WORKER_ALIAS_OVERRIDES,
    WORKERS,
    WorkerEntry,
    utcnow,
)
from central_api.worker_nodes import merge_nodes, node_from_url, resolve_node_ip

router = APIRouter()


async def _fila_worker_db(node: str) -> dict | None:
    """Fila del worker en PostgreSQL por endpoint; ``None`` sin base/fila.

    Lee ``models.Worker`` (fuente canónica con base configurada); nunca
    expone secretos, solo inventario y telemetría ya reportada.
    """
    if not db_configurado():
        return None
    try:
        from sqlalchemy import select

        from central_api.models.fleet import Worker
    except Exception:  # noqa: BLE001 - sin modelos, solo memoria
        return None
    try:
        async with nueva_sesion() as sesion:
            fila = (await sesion.execute(
                select(Worker).where(Worker.endpoint == node)
            )).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - sin base, solo memoria
        return None
    if fila is None:
        return None
    return {
        "registrado": True,
        "worker_id": str(fila.id),
        "estado": str(fila.status),
        "en_ejecucion": int(fila.running_jobs or 0),
        "capacidad": int(fila.capacity or 5),
        "ultimo_latido": (
            fila.last_heartbeat_at.isoformat() if fila.last_heartbeat_at else None
        ),
    }


async def vista_worker(node: str) -> dict:
    """Vista del nodo: memoria + fila PG cuando hay base configurada."""
    vista = worker_overview(node)
    fila = await _fila_worker_db(node)
    if fila is not None:
        vista = {**vista, **fila, "origen": vista.get("origen", "env"),
                 "fuente": "postgresql"}
    else:
        vista = {**vista, "fuente": "memoria"}
    return vista


class AliasBody(BaseModel):
    alias: str = Field(
        default="",
        max_length=64,
        description="Alias legible del worker. Vacío quita el alias editado y vuelve al de WORKER_ALIASES.",
        examples=["api2"],
    )


class AddWorkerBody(BaseModel):
    node: str = Field(
        description=(
            "Dirección del worker en formato ip:puerto o como URL. El nodo se agrega "
            "al inventario aunque no responda al sondeo inicial."
        ),
        examples=["10.0.0.13:8080"],
    )


_SECUENCIA_MODELOS_OPENAPI = count(1)
_DESCRIPCIONES_CAMPOS_RESPUESTA = {
    "success": "Indica que la consulta o acción terminó correctamente.",
    "workers": "Workers del inventario y su estado observado.",
    "node": "Dirección normalizada del worker.",
    "alcanzable": "Indica si el worker respondió al sondeo inicial.",
    "detalle": "Estado observado durante el sondeo, o tipo de fallo si no respondió.",
    "vista": "Estado, capacidad y métricas iniciales del worker agregado.",
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
        f"WorkersOpenApiResponse{next(_SECUENCIA_MODELOS_OPENAPI)}", **campos
    )
    return {
        "description": descripcion,
        "model": modelo,
        "content": {"application/json": {"example": ejemplo}},
    }


def _errores_admin() -> dict:
    """Respuestas de autenticación comunes a las rutas del inventario."""
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
            "Falta el campo node o no tiene el tipo esperado. Envíe un cuerpo JSON "
            "con node como texto, por ejemplo 10.0.0.13:8080."
        )
    }
}


_ERROR_NODO = {
    400: {
        "description": (
            "La dirección está vacía o no incluye host y puerto en un formato "
            "reconocible. Indique una dirección como 10.0.0.13:8080 o una URL del nodo."
        )
    }
}


def derive_state(entry: WorkerEntry | None) -> str:
    """Estado de flota derivado: SANO, SATURADO, DEGRADADO, CAIDO, REGISTRANDO o DESCONOCIDO."""
    settings = get_settings()
    now: datetime = utcnow()
    if entry is None:
        return "DESCONOCIDO"
    if entry.status == "DRENANDO":
        return "DRENANDO"
    if entry.last_heartbeat_at is None:
        return "REGISTRANDO"
    age = now - entry.last_heartbeat_at
    if age > timedelta(seconds=settings.worker_down_after_seconds):
        return "CAIDO"
    if age > timedelta(seconds=settings.worker_degraded_after_seconds):
        return "DEGRADADO"
    if entry.running_jobs + entry.reserved_slots >= entry.capacity:
        return "SATURADO"
    return "SANO"


def alias_de(node: str) -> str:
    """Alias vigente: el editado en el panel gana sobre ``WORKER_ALIASES``."""
    return WORKER_ALIAS_OVERRIDES.get(node) or get_settings().worker_alias_map.get(node, "")


async def cargar_alias_persistidos() -> int:
    """Carga ``worker_aliases`` en memoria (arranque). Sin base no hace nada."""
    if not db_configurado():
        return 0
    from sqlalchemy import text

    async with nueva_sesion() as sesion:
        filas = (await sesion.execute(text("SELECT node, alias FROM worker_aliases"))).all()
    WORKER_ALIAS_OVERRIDES.clear()
    WORKER_ALIAS_OVERRIDES.update({str(n): str(a) for n, a in filas})
    return len(filas)


async def _guardar_alias(node: str, alias: str) -> None:
    """Persiste (o borra, si ``alias`` es vacío) el alias de un nodo."""
    if not db_configurado():
        return
    from sqlalchemy import text

    async with nueva_sesion() as sesion:
        if alias:
            await sesion.execute(
                text(
                    "INSERT INTO worker_aliases (node, alias) VALUES (:n, :a) "
                    "ON CONFLICT (node) DO UPDATE SET alias = :a, updated_at = CURRENT_TIMESTAMP"
                ),
                {"n": node, "a": alias},
            )
        else:
            await sesion.execute(
                text("DELETE FROM worker_aliases WHERE node = :n"), {"n": node}
            )


def worker_overview(node: str) -> dict:
    settings = get_settings()
    entry = WORKERS.get(node)
    from central_api.store import EPHEMERAL_WORKERS

    origen = "efimero" if any(
        info.get("node") == node for info in EPHEMERAL_WORKERS.values()
    ) else "panel" if node in ADMIN_NODES else "env"
    return {
        "node": node,
        "alias": alias_de(node),
        "ip": resolve_node_ip(node),
        "worker_id": entry.worker_id if entry else "",
        "origen": origen,
        "registrado": entry is not None,
        "estado": derive_state(entry),
        "en_ejecucion": entry.running_jobs if entry else 0,
        "capacidad": entry.capacity if entry else settings.worker_capacity,
        "sellado": bool(entry and entry.sealed_pubkey_pem),
        "ultimo_latido": entry.last_heartbeat_at.isoformat() if entry and entry.last_heartbeat_at else None,
    }


def require_admin(authorization: str | None) -> None:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(status_code=403, detail="ADMIN_TOKEN no configurado")
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(status_code=401, detail="Falta bearer ADMIN_TOKEN")
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=403, detail="ADMIN_TOKEN inválido")


def _coerce_node(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="node vacío")
    if "://" in text:
        return node_from_url(text)
    if ":" not in text:
        raise HTTPException(status_code=400, detail="node debe ser ip:port")
    return text


@router.get(
    "/workers",
    summary="Listar workers registrados",
    description=(
        "Devuelve el inventario combinado de los workers configurados y los agregados "
        "desde el panel. Cada elemento incluye su origen, estado operativo, capacidad, "
        "trabajos en ejecución y último latido. Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        200: _respuesta_json(
            "Inventario actual de workers.",
            {"success": True, "workers": [{"node": "10.0.0.13:8080", "worker_id": "worker-01", "origen": "panel", "registrado": True, "estado": "SANO", "en_ejecucion": 1, "capacidad": 5, "sellado": False, "ultimo_latido": "2026-10-12T16:30:00+00:00", "fuente": "memoria"}]},
        ),
    },
)
async def list_workers(authorization: str | None = Header(default=None)) -> dict:
    """Devuelve el inventario y el estado observado de cada worker."""
    require_admin(authorization)
    settings = get_settings()
    nodes = merge_nodes(settings.worker_node_list, ADMIN_NODES)
    return {"success": True, "workers": [await vista_worker(n) for n in nodes]}


@router.post(
    "/workers",
    status_code=201,
    summary="Agregar un worker al inventario",
    description=(
        "Agrega una dirección de worker al inventario administrado y realiza un "
        "sondeo inicial. Si el nodo no responde, igualmente queda agregado y la "
        "respuesta informa alcanzable=false. Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_VALIDACION,
        **_ERROR_NODO,
        201: _respuesta_json(
            "Worker agregado al inventario; alcanzable indica el resultado del sondeo inicial.",
            {"success": True, "node": "10.0.0.13:8080", "alcanzable": True, "detalle": "SANO", "vista": {"node": "10.0.0.13:8080", "estado": "DESCONOCIDO", "registrado": False, "capacidad": 5}},
        ),
    },
)
async def add_worker(body: AddWorkerBody, authorization: str | None = Header(default=None)) -> dict:
    """Agrega el nodo al inventario e informa si responde al sondeo inicial."""
    require_admin(authorization)
    try:
        node = _coerce_node(body.node)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reachable: bool | None = None
    detail: str | None = None
    try:
        status = await dispatcher.probe_worker(node)
        reachable = True
        detail = str(status.get("state", status.get("status", "ok")))
    except Exception as exc:  # el nodo puede levantarse después; igual se registra
        reachable = False
        detail = type(exc).__name__
    ADMIN_NODES.add(node)
    return {
        "success": True,
        "node": node,
        "alcanzable": reachable,
        "detalle": detail,
        "vista": worker_overview(node),
    }


@router.delete(
    "/workers/{node}",
    summary="Quitar un worker del inventario administrado",
    description=(
        "Quita el nodo de la lista agregada desde el panel. Si existe una entrada "
        "activa del worker, la marca como DRENANDO para que no reciba nuevas "
        "asignaciones; las fuentes de inventario externas no se modifican. "
        "Requiere autorización administrativa."
    ),
    responses={
        **_errores_admin(),
        **_ERROR_NODO,
        200: _respuesta_json(
            "Nodo quitado del inventario administrado.",
            {"success": True, "node": "10.0.0.13:8080"},
        ),
    },
)
def remove_worker(node: str, authorization: str | None = Header(default=None)) -> dict:
    """Quita el nodo del inventario administrado y, si está activo, lo drena."""
    require_admin(authorization)
    try:
        norm = _coerce_node(node)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ADMIN_NODES.discard(norm)
    entry = WORKERS.get(norm)
    if entry is not None:
        entry.status = "DRENANDO"
    return {"success": True, "node": norm}


@router.put(
    "/workers/{node}/alias",
    summary="Editar el alias de un worker",
    description=(
        "Define el alias legible que el panel muestra para el nodo. Se guarda en "
        "la base y tiene prioridad sobre WORKER_ALIASES. Un alias vacío lo quita. "
        "Requiere autorización administrativa."
    ),
)
async def set_worker_alias(
    node: str,
    body: AliasBody,
    authorization: str | None = Header(default=None),
) -> dict:
    """Guarda el alias del nodo y lo aplica de inmediato."""
    require_admin(authorization)
    try:
        norm = _coerce_node(node)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    alias = " ".join((body.alias or "").split())
    if any(ord(c) < 32 for c in alias):
        raise HTTPException(status_code=400, detail="alias con caracteres inválidos")
    conocidos = merge_nodes(get_settings().worker_node_list, ADMIN_NODES)
    if norm not in conocidos and norm not in WORKERS:
        raise HTTPException(status_code=404, detail="worker no está en el inventario")
    await _guardar_alias(norm, alias)
    if alias:
        WORKER_ALIAS_OVERRIDES[norm] = alias
    else:
        WORKER_ALIAS_OVERRIDES.pop(norm, None)
    try:
        from central_api.admin.audit import log_event

        log_event(
            "fleet.worker.alias_set", target_type="worker", target_id=norm,
            metadata={"alias": alias},
        )
    except Exception:  # noqa: BLE001 - la auditoría no debe romper la edición
        pass
    return {"success": True, "node": norm, "alias": alias_de(norm)}
