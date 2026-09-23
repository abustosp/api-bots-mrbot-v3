"""Autenticación de borde interno (plan 02 §4.8 y §7).

Las rutas ``/internal/v1`` no aceptan API keys de clientes: el worker se
identifica por su nodo ``ip:port`` registrado y, cuando hay clave de firma
configurada, por token de servicio de corta vida (v1 ligado al nodo, v2
ligado al UUID pleno del worker). Un worker nunca reporta eventos o
resultados para otro worker.

Espejo PostgreSQL: con ``DATABASE_URL`` configurada la fuente canónica es la
tabla ``workers`` (registro con pubkey y estado); la entrada en memoria queda
como caché viva. Sin base, solo memoria (modo desarrollo, misma firma).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Header, HTTPException

from central_api.db import db_configurado, nueva_sesion
from central_api.security.secret_redaction import public_error
from central_api.security.worker_auth import check_any_token
from central_api.settings import get_settings
from central_api.store import WORKERS, WorkerEntry


def _portador_valido(
    signing_key: str, authorization: str | None, node: str, worker_id: UUID | None
) -> bool:
    """Valida el Bearer contra el token v1 (nodo) o v2 (UUID) del worker."""
    if not signing_key:
        return True
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return check_any_token(signing_key, token, node, worker_id)


async def _espejo_pg(node: str) -> WorkerEntry | None:
    """Hidrata la caché en memoria desde la fila canónica de PostgreSQL."""
    try:
        from sqlalchemy import select

        from central_api.models.fleet import Worker
    except Exception:  # noqa: BLE001 - sin modelos, solo memoria
        return None
    try:
        async with nueva_sesion() as sesion:
            fila = (
                await sesion.execute(
                    select(Worker).where(Worker.endpoint == node)
                )
            ).scalar_one_or_none()
            if fila is None:
                return None
            entrada = WORKERS.get(node) or WorkerEntry(node=node)
            entrada.worker_id = str(fila.id)
            entrada.status = str(fila.status)
            entrada.capacity = int(fila.capacity or 5)
            entrada.running_jobs = int(fila.running_jobs or 0)
            entrada.sealed_pubkey_pem = str(fila.sealed_pubkey_pem or "")
            entrada.last_heartbeat_at = fila.last_heartbeat_at
            WORKERS[node] = entrada
            return entrada
    except Exception:  # noqa: BLE001 - sin base, solo memoria
        return None


def _uuid_entrada(entrada: WorkerEntry | None) -> UUID | None:
    """UUID pleno del worker si la entrada ya lo conoce; ``None`` si no."""
    if entrada is None or not entrada.worker_id:
        return None
    try:
        return UUID(str(entrada.worker_id))
    except (ValueError, AttributeError, TypeError):
        return None


async def require_worker(
    x_worker_node: str | None = Header(default=None, alias="X-Worker-Node"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> str:
    """Devuelve el nodo autenticado o lanza 401/403 público (sin detalle).

    Memoria primero (caché viva); si el nodo no está en memoria y hay base
    configurada, hidrata desde ``workers`` (con su pubkey y estado) y valida
    el portador v1/v2. Sin base, solo memoria.
    """
    settings = get_settings()
    signing_key = settings.internal_jwt_signing_key or ""
    entrada = WORKERS.get(x_worker_node) if x_worker_node else None
    if entrada is None and x_worker_node and db_configurado():
        entrada = await _espejo_pg(x_worker_node)
    if entrada is None or entrada.status == "RETIRADO":
        raise HTTPException(
            status_code=401, detail=public_error("authentication")["detail"]
        )
    if not _portador_valido(
        signing_key, authorization, x_worker_node or "", _uuid_entrada(entrada)
    ):
        raise HTTPException(
            status_code=403, detail=public_error("forbidden")["detail"]
        )
    return x_worker_node or ""


def resolve_worker_uuid(node: str) -> UUID | None:
    """UUID pleno del worker desde la caché en memoria; ``None`` si se ignora."""
    return _uuid_entrada(WORKERS.get(node))


__all__ = ["require_worker", "resolve_worker_uuid"]
