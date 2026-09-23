"""Registro y latidos de workers (plan 02 §7.2-§7.5).

La central SOLO guarda la dirección ``ip:port`` de cada worker (más el estado
que el propio worker reporta por HTTP). El alta acepta la unión de
``WORKER_NODES`` y los nodos del panel admin (nunca solo uno); la capacidad
se topa en 5 (W-2); la salud viva se confirma por HTTP contra el worker
además de los latidos. ``protocol_version`` es entero (1).

Fase de identidad: con base configurada el registro devuelve el ``worker_id``
UUID pleno (sin IDs correlativos, I-1/I-2) y el token de servicio v2 va
ligado a ese UUID; el latido acepta UUID o ``ip:port``. Sin base, el modo
memoria responde con token v1 ligado al nodo.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from mrbot_contracts.version import PROTOCOL_VERSION as CONTRACT_PROTOCOL_VERSION

from central_api.db import db_configurado, nueva_sesion
from central_api.security.worker_auth import mint_service_token, mint_worker_token
from central_api.settings import get_settings
from central_api.store import ADMIN_NODES, WORKERS, WorkerEntry, utcnow
from central_api.worker_nodes import merge_nodes, node_from_url


async def _registrar_db(node: str, body: object) -> UUID | None:
    """Registra el worker en PostgreSQL; ``None`` si hay que usar memoria.

    Idempotente por ``endpoint``: un re-registro actualiza pubkey, capacidad
    y estado sin duplicar la identidad UUID del worker.
    """
    try:
        from central_api.repositories.workers import WorkerRepository
    except Exception:  # noqa: BLE001 - sin repos, fallback de desarrollo
        return None
    try:
        async with nueva_sesion() as sesion:
            repo = WorkerRepository(sesion)  # type: ignore[arg-type]
            fila = await repo.register(
                name=node,
                endpoint_node=node,
                app_version=str(getattr(body, "build_version", "") or ""),
                allowed=merge_nodes(get_settings().worker_node_list, ADMIN_NODES),
                capacity=min(max(1, int(getattr(body, "capacity", 5) or 5)), 5),
                sealed_pubkey_pem=str(getattr(body, "sealed_pubkey_pem", "") or "") or None,
                protocol_version=int(getattr(body, "protocol_version", 1) or 1),
            )
            return fila.id  # type: ignore[return-value]
    except Exception:  # noqa: BLE001 - el registro vive igual en memoria
        return None


async def _latido_db(
    worker_id: UUID | None, node: str, status: str, running: int, capacity: int
) -> bool:
    """Persiste el latido en PostgreSQL; ``False`` si hay que usar memoria."""
    if worker_id is None:
        return False
    try:
        from central_api.repositories.workers import WorkerRepository
    except Exception:  # noqa: BLE001 - sin repos, fallback de desarrollo
        return False
    try:
        async with nueva_sesion() as sesion:
            repo = WorkerRepository(sesion)  # type: ignore[arg-type]
            await repo.heartbeat(
                worker_id=worker_id,
                status=status,
                running_jobs=running,
                queued_jobs=0,
                capacity=capacity,
            )
    except Exception:  # noqa: BLE001 - el latido vive igual en memoria
        return False
    return True


def _uuid_de(entrada: WorkerEntry | None) -> UUID | None:
    """UUID pleno guardado en la entrada en memoria; ``None`` si se ignora."""
    if entrada is None or not entrada.worker_id:
        return None
    try:
        return UUID(str(entrada.worker_id))
    except (ValueError, AttributeError, TypeError):
        return None


async def _nodo_desde_ruta(worker_id: str) -> str:
    """Resuelve el parámetro de ruta (UUID pleno o ``ip:port``) al nodo.

    Con base configurada un UUID desconocido en memoria se busca en
    PostgreSQL por PK y se hidrata la caché; sin base solo memoria.
    """
    try:
        visto = UUID(str(worker_id))
    except (ValueError, AttributeError, TypeError):
        return str(worker_id)
    for nodo, entrada in WORKERS.items():
        if _uuid_de(entrada) == visto:
            return nodo
    if db_configurado():
        try:
            from central_api.models.fleet import Worker

            async with nueva_sesion() as sesion:
                fila = await sesion.get(Worker, visto)
                if fila is not None:
                    nodo = str(fila.endpoint)
                    entrada = WORKERS.get(nodo) or WorkerEntry(node=nodo)
                    entrada.worker_id = str(fila.id)
                    entrada.status = str(fila.status)
                    entrada.capacity = int(fila.capacity or 5)
                    entrada.running_jobs = int(fila.running_jobs or 0)
                    entrada.sealed_pubkey_pem = str(fila.sealed_pubkey_pem or "")
                    entrada.last_heartbeat_at = fila.last_heartbeat_at
                    WORKERS[nodo] = entrada
                    return nodo
        except Exception:  # noqa: BLE001 - cae al 404 de abajo
            pass
    raise HTTPException(status_code=404, detail="worker no registrado")


async def _reincorporar_por_latido(node: str) -> WorkerEntry | None:
    """Reincorpora un worker cuyo latido llega sin entrada en memoria.

    La central pierde la caché al reiniciarse mientras los workers siguen
    latiendo: en vez de 404, se rehidrata desde PostgreSQL (canónico con
    base) o se recrea si el nodo está inventariado (``WORKER_NODES`` +
    panel admin). Un nodo desconocido en ambos lados sigue siendo 404.
    """
    if db_configurado():
        try:
            from sqlalchemy import select

            from central_api.models.fleet import Worker

            async with nueva_sesion() as sesion:
                fila = (
                    await sesion.execute(
                        select(Worker).where(Worker.endpoint == node)
                    )
                ).scalar_one_or_none()
                if fila is not None:
                    entrada = WORKERS.get(node) or WorkerEntry(node=node)
                    entrada.worker_id = str(fila.id)
                    entrada.status = str(fila.status)
                    entrada.capacity = int(fila.capacity or 5)
                    entrada.running_jobs = int(fila.running_jobs or 0)
                    entrada.sealed_pubkey_pem = str(fila.sealed_pubkey_pem or "")
                    entrada.last_heartbeat_at = fila.last_heartbeat_at
                    WORKERS[node] = entrada
                    return entrada
        except Exception:  # noqa: BLE001 - cae al inventario en memoria
            pass
    permitido = merge_nodes(get_settings().worker_node_list, ADMIN_NODES)
    if permitido and node not in permitido:
        return None
    entrada = WorkerEntry(node=node)
    entrada.status = "REGISTRANDO"
    WORKERS[node] = entrada
    return entrada


router = APIRouter()

# Alias explícito para conservar la comprobación estática de compatibilidad
# mientras la fuente normativa permanece en mrbot-contracts.
PROTOCOL_VERSION: int = 1
if PROTOCOL_VERSION != CONTRACT_PROTOCOL_VERSION:  # pragma: no cover - guardia de build
    raise RuntimeError("central-api: versión de protocolo desalineada")

MAX_WORKER_CAPACITY = 5  # W-2: tope duro inicial de 5 ejecuciones


class RegisterBody(BaseModel):
    advertised_url: str = Field(description="URL base del worker, p.ej. http://10.0.0.11:8080")
    capacity: int = 5
    capabilities: list[str] = Field(default_factory=list)
    build_version: str = ""
    protocol_version: int = PROTOCOL_VERSION
    instance_nonce: str = Field(
        default="",
        description="UUID que cambia en cada arranque; distingue sesiones del worker",
    )
    sealed_pubkey_pem: str = Field(
        default="",
        description="RSA pública efímera del worker (PEM) para el sobre sellado",
    )


class HeartbeatBody(BaseModel):
    status: str = "SANO"
    capacity: int = 5
    en_ejecucion: int = 0
    en_cola_local: int = 0


@router.post("/workers/register")
async def register_worker(body: RegisterBody) -> dict:
    try:
        node = node_from_url(body.advertised_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Unión de inventarios: env + panel admin (ninguno reemplaza al otro).
    allowed = merge_nodes(get_settings().worker_node_list, ADMIN_NODES)
    if allowed and node not in allowed:
        raise HTTPException(
            status_code=403,
            detail="worker no inventariado (WORKER_NODES ni panel admin)",
        )
    if body.protocol_version != PROTOCOL_VERSION:
        entry = WORKERS.get(node) or WorkerEntry(node=node)
        entry.status = "DRENANDO"
        WORKERS[node] = entry
        return {
            "accepted": False,
            "node": node,
            "worker_id": entry.worker_id,
            "protocol_version": PROTOCOL_VERSION,
            "motivo": "protocolo incompatible",
        }
    entry = WORKERS.get(node) or WorkerEntry(node=node)
    entry.status = "REGISTRANDO"
    entry.capacity = min(max(1, body.capacity), MAX_WORKER_CAPACITY)
    entry.capabilities = body.capabilities
    if body.sealed_pubkey_pem:
        entry.sealed_pubkey_pem = body.sealed_pubkey_pem
    WORKERS[node] = entry
    # Con base configurada el registro canónico vive en PostgreSQL
    # (repositories+models, UUID pleno sin correlativos); la entrada en
    # memoria queda como caché viva con el mismo UUID.
    worker_uuid = _uuid_de(entry)
    if db_configurado():
        registrado = await _registrar_db(node, body)
        if registrado is not None:
            worker_uuid = registrado
            entry.worker_id = str(registrado)
    settings = get_settings()
    signing_key = settings.internal_jwt_signing_key or ""
    if worker_uuid is not None and signing_key:
        token = mint_worker_token(signing_key, worker_uuid)
    elif signing_key:
        token = mint_service_token(signing_key, node)
    else:
        token = ""
    # Clave pública de asignaciones: el worker la fija en memoria y con ella
    # verifica que cada ejecución la haya firmado esta central. En desarrollo
    # (sin ASSIGNMENT_SIGNING_KEY) es efímera por proceso.
    from central_api.security.assignments import process_signing_key, public_pem

    signing_private, _efimera = process_signing_key(settings.assignment_signing_key)
    return {
        "accepted": True,
        "node": node,
        "worker_id": str(worker_uuid) if worker_uuid is not None else entry.worker_id,
        "heartbeat_interval_seconds": settings.worker_heartbeat_interval_seconds,
        "protocol_version": PROTOCOL_VERSION,
        "service_token": token,
        "assignment_verify_key_pem": public_pem(signing_private),
    }


@router.post("/workers/{worker_id}/heartbeat")
async def worker_heartbeat(worker_id: str, body: HeartbeatBody) -> dict:
    # La ruta acepta el UUID pleno o el nodo "ip:port" (compatibilidad).
    node = await _nodo_desde_ruta(worker_id)
    entry = WORKERS.get(node)
    if entry is None:
        entry = await _reincorporar_por_latido(node)
    if entry is None:
        raise HTTPException(status_code=404, detail="worker no registrado")
    if entry.status == "DRENANDO":
        return {"ok": True, "node": entry.node, "status": entry.status}
    entry.status = body.status
    entry.capacity = min(max(1, body.capacity), MAX_WORKER_CAPACITY)
    entry.running_jobs = max(0, body.en_ejecucion)
    entry.last_heartbeat_at = utcnow()
    # Con base configurada el latido canónico vive en PostgreSQL.
    if db_configurado():
        await _latido_db(
            _uuid_de(entry), node, body.status, entry.running_jobs, entry.capacity
        )
    return {"ok": True, "node": entry.node, "status": entry.status}
