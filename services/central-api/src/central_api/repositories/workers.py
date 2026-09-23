"""Repositorio de flota: registro, latidos y seleccion (plan 01 §§5.4 y 11.2).

El registro acepta la union ``WORKER_NODES + ADMIN_NODES``: el inventario
inicial llega por entorno y el panel suma nodos en caliente. La central solo
guarda ``ip:port`` + estado reportado por HTTP y contacta por HTTP.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from central_api.models.base import PROTOCOL_VERSION, new_uuid7
from central_api.models.fleet import Worker, WorkerHeartbeat
from central_api.repositories.base import NotFoundError, RepositoryError, scope_in
from central_api.worker_nodes import merge_nodes

#: Ventana de latido sano y vista de salud efectiva (30 s, plan 01 §11.2).
HEARTBEAT_FRESH_SECONDS = 30

FLEET_HEALTH_SQL = text(
    """
SELECT w.id, w.name, w.status, w.protocol_version, w.app_version,
       w.capacity, w.running_jobs, w.queued_jobs, w.last_heartbeat_at,
       CURRENT_TIMESTAMP - w.last_heartbeat_at AS heartbeat_age,
       CASE
           WHEN w.status = 'DRENANDO' THEN 'DRENANDO'
           WHEN w.last_heartbeat_at IS NULL THEN 'CAIDO'
           WHEN w.last_heartbeat_at < CURRENT_TIMESTAMP - make_interval(secs => :fresh)
               THEN 'CAIDO'
           WHEN w.running_jobs >= w.capacity THEN 'SATURADO'
           WHEN w.status = 'SANO' THEN 'SANO'
           ELSE 'DEGRADADO'
       END AS effective_health
FROM workers w
ORDER BY effective_health, w.last_heartbeat_at DESC NULLS FIRST, w.name
"""
)


def allowed_nodes(
    worker_nodes_env: str | Collection[str],
    admin_nodes: Collection[str] | None = None,
) -> list[str]:
    """Union ordenada sin duplicados de ``WORKER_NODES + ADMIN_NODES``."""
    env_list = (
        list(worker_nodes_env)
        if isinstance(worker_nodes_env, (list, tuple, set))
        else [worker_nodes_env]
    )
    return merge_nodes(*env_list, *(list(admin_nodes or []),))


def _revisar_pubkey(sealed_pubkey_pem: str | None) -> str | None:
    """Valida la pubkey efimera del sobre sellado; falla cerrado si es privada."""
    if not sealed_pubkey_pem:
        return None
    texto = sealed_pubkey_pem.strip()
    if not texto:
        return None
    if "PRIVATE" in texto.upper():
        raise RepositoryError(
            "la central solo persiste la clave publica del worker"
        )
    if "BEGIN PUBLIC KEY" not in texto.upper() and "BEGIN RSA PUBLIC KEY" not in texto.upper():
        raise RepositoryError("clave pública del worker inválida")
    return texto


class WorkerRepository:
    """Registro y telemetria de workers con tope 5 impuesto en la base."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_endpoint(self, endpoint_node: str) -> Worker | None:
        """Localiza el worker por su ``ip:port`` (identidad de red)."""
        return (
            await self._session.execute(
                select(Worker).where(Worker.endpoint == endpoint_node)
            )
        ).scalar_one_or_none()

    async def register(
        self,
        *,
        name: str,
        endpoint_node: str,
        app_version: str,
        allowed: Collection[str],
        capacity: int = 5,
        sealed_pubkey_pem: str | None = None,
        protocol_version: int = PROTOCOL_VERSION,
    ) -> Worker:
        """Registra un worker solo si su ``ip:port`` esta en el inventario.

        Idempotente por ``endpoint``: un re-registro (nuevo arranque con otro
        ``instance_nonce``) actualiza la fila existente —pubkey, capacidad y
        estado ``REGISTRANDO``— en vez de duplicarla; la identidad UUID
        estable del worker se conserva.
        """
        if not scope_in(allowed, endpoint_node):
            raise RepositoryError(f"nodo {endpoint_node!r} fuera del inventario")
        if protocol_version != PROTOCOL_VERSION:
            raise RepositoryError("protocolo incompatible")
        if not 1 <= capacity <= 5:
            raise RepositoryError("capacidad fuera del tope 1..5")
        pubkey = _revisar_pubkey(sealed_pubkey_pem)
        existente = await self.get_by_endpoint(endpoint_node)
        if existente is not None:
            existente.name = name
            existente.status = "REGISTRANDO"
            existente.protocol_version = PROTOCOL_VERSION
            existente.app_version = app_version
            existente.capacity = capacity
            existente.running_jobs = 0
            existente.queued_jobs = 0
            if pubkey is not None:
                existente.sealed_pubkey_pem = pubkey
            try:
                await self._session.flush()
            except Exception as exc:
                raise RepositoryError(
                    f"no se pudo re-registrar el worker: {type(exc).__name__}"
                ) from exc
            return existente
        worker = Worker(
            id=new_uuid7(),
            name=name,
            status="REGISTRANDO",
            endpoint=endpoint_node,
            protocol_version=PROTOCOL_VERSION,
            app_version=app_version,
            capacity=capacity,
            running_jobs=0,
            queued_jobs=0,
            sealed_pubkey_pem=pubkey,
        )
        self._session.add(worker)
        try:
            await self._session.flush()
        except Exception as exc:
            raise RepositoryError(
                f"no se pudo registrar el worker: {type(exc).__name__}"
            ) from exc
        return worker

    async def heartbeat(
        self,
        *,
        worker_id: UUID,
        status: str,
        running_jobs: int,
        queued_jobs: int,
        capacity: int,
        metrics: dict | None = None,
    ) -> WorkerHeartbeat:
        """Persiste el latido y refresca el snapshot del worker."""
        if not 1 <= capacity <= 5 or not 0 <= running_jobs <= 5:
            raise RepositoryError("capacidad o carga fuera del tope 1..5/0..5")
        worker = await self._session.get(Worker, worker_id)
        if worker is None:
            raise NotFoundError("worker no registrado")
        beat = WorkerHeartbeat(
            id=new_uuid7(),
            worker_id=worker_id,
            status=status,
            running_jobs=running_jobs,
            queued_jobs=queued_jobs,
            capacity=capacity,
            metrics=dict(metrics or {}),
        )
        worker.status = status
        worker.running_jobs = running_jobs
        worker.queued_jobs = queued_jobs
        worker.capacity = capacity
        worker.last_heartbeat_at = datetime.now(timezone.utc)
        self._session.add(beat)
        await self._session.flush()
        return beat

    async def fleet_health(self) -> list[dict]:
        """Retorna la vista de salud efectiva sin mutar ``workers.status``."""
        rows = (
            await self._session.execute(
                FLEET_HEALTH_SQL, {"fresh": HEARTBEAT_FRESH_SECONDS}
            )
        ).mappings()
        return [dict(r) for r in rows.all()]


__all__ = [
    "FLEET_HEALTH_SQL",
    "HEARTBEAT_FRESH_SECONDS",
    "WorkerRepository",
    "allowed_nodes",
]
