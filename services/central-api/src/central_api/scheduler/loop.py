"""Bucle push del scheduler: claim, selección y despacho (plan 02 §6).

Cada réplica corre esta tarea supervisada sin estado de autoridad en
memoria: reclama con exclusión (en PG ``FOR UPDATE SKIP LOCKED`` según
``claim.py``), reserva el slot en la misma unidad lógica, y recién después
despacha por HTTP fuera de ella. Un claim vacío por contención NO es cola
vacía: se distingue con ``has_schedulable_work`` y se cede el turno tras
``scheduler_max_empty_rounds`` rondas (§6.6). El resultado del dispatch se
interpreta según la tabla §6.8 (202/409/timeout/5xx).
"""

from __future__ import annotations

import asyncio
import secrets
import uuid
import logging

from mrbot_contracts.version import PROTOCOL_VERSION

from central_api.db import db_configurado, nueva_sesion
from central_api.scheduler import circuit_breaker, dispatcher
from central_api.scheduler import reaper as reaper_mod
from central_api.scheduler.claim import leases_vencidas_db
from central_api.scheduler.selector import select_worker
from central_api.settings import get_settings
from central_api.store import ADMIN_NODES, JOBS, WORKERS, Job, utcnow

log = logging.getLogger(__name__)
from central_api.worker_nodes import merge_nodes


async def reap_pg_vencidas() -> list[str]:
    """Recupera leases vencidas de PostgreSQL hacia el reaper en memoria.

    Con base configurada los vencimientos canónicos viven en PG
    (``JobRepository.expired_leases``); los IDs presentes en memoria se
    procesan con la misma semántica W-2/W-4. Best-effort: nunca rompe la
    vuelta del scheduler.
    """
    if not db_configurado():
        return []
    try:
        async with nueva_sesion() as sesion:
            ids = await leases_vencidas_db(sesion)
    except Exception:  # noqa: BLE001 - sin base, solo memoria
        return []
    ahora = utcnow()
    for jid in ids:
        texto = str(jid)
        lease = reaper_mod.LEASES.get(texto)
        if lease is not None:
            lease["expires_at"] = min(lease["expires_at"], ahora)
    return [str(jid) for jid in ids]

def has_schedulable_work() -> bool:
    """``EXISTS`` barato: hay PENDIENTE sin cancelación pedida (nunca count)."""
    return any(
        job.status == "PENDIENTE" for job in JOBS.values()
    )


def claim_next_job() -> Job | None:
    """Reclama el PENDIENTE más antiguo elegible (análogo a ``SKIP LOCKED``)."""
    candidates = [
        job for job in JOBS.values() if job.status == "PENDIENTE"
    ]
    candidates.sort(key=lambda j: (j.created_at, j.id))
    return candidates[0] if candidates else None


async def claim_and_reserve(job: Job):
    """Elige worker least-loaded y reserva el slot (transacción lógica).

    Devuelve ``(worker, token, lease_id, lease_expires_at)`` o
    ``(None, "", "", None)`` si no hay candidato sano con slot: el job
    queda PENDIENTE para la próxima vuelta. La ``lease_id`` es UUIDv7: el
    worker la exige en la admisión y la devuelve en cada callback del
    intento.
    """
    from central_api.api.dependencies import JOB_META
    from central_api.store import new_job_id

    settings = get_settings()
    allowed = merge_nodes(settings.worker_node_list, ADMIN_NODES)
    workers = [
        w for w in WORKERS.values()
        if (not allowed or w.node in allowed)
    ]
    worker = select_worker(
        job, workers,
        allowed_nodes=allowed or None,
        degraded_after_seconds=settings.worker_degraded_after_seconds,
    )
    if worker is None or not circuit_breaker.probe_allowed(worker.node):
        return None, "", "", None
    token = f"tok-{uuid.uuid4().hex}"
    lease_id = new_job_id()
    if db_configurado():
        # Antes del HTTP dispatch, la misma asignación debe ser durable para
        # que callbacks posteriores autentiquen worker_id/attempt tras restart.
        try:
            import uuid as uuid_mod

            from central_api.repositories.jobs import JobRepository

            worker_uuid = uuid_mod.UUID(str(worker.worker_id))
            async with nueva_sesion() as session:
                repo = JobRepository(session)  # type: ignore[arg-type]
                attempt = await repo.assign_for_dispatch(
                    job_id=uuid_mod.UUID(str(job.id)),
                    worker_id=worker_uuid,
                    lease_seconds=settings.worker_ack_lease_seconds,
                )
        except Exception:  # noqa: BLE001 - no despachar sin claim durable
            log.warning("claim PostgreSQL fallido; job queda sin dispatch")
            return None, "", "", None
        if attempt is None:
            return None, "", "", None
        job.assignment_attempt = attempt
    else:
        job.assignment_attempt += 1
    job.status = "ASIGNADO"
    job.worker_node = worker.node
    expira = reaper_mod.grant_lease(
        job.id, worker.node, token, job.assignment_attempt
    )
    reaper_mod.LEASES[job.id]["lease_id"] = lease_id
    return worker, token, lease_id, expira


async def handle_dispatch_result(job: Job, worker_node: str, ok: bool) -> str:
    """Aplica la tabla §6.8 ante la respuesta del worker."""
    if ok:
        circuit_breaker.record_success(worker_node)
        return "ASIGNADO"
    circuit_breaker.record_failure(worker_node)
    if db_configurado():
        try:
            from uuid import UUID

            from central_api.internal.dependencies import resolve_worker_uuid
            from central_api.repositories.jobs import JobRepository

            worker_id = resolve_worker_uuid(worker_node)
            if worker_id is None:
                return "ASIGNADO"
            async with nueva_sesion() as session:
                await JobRepository(session).release_assignment(
                    job_id=UUID(str(job.id)), worker_id=worker_id
                )
        except Exception:  # noqa: BLE001 - conservar lease hasta reaper
            log.warning("no se pudo liberar claim PostgreSQL; queda bajo lease")
            return "ASIGNADO"
    # Sin entrega confirmada: liberar slot y reencolar con backoff.
    reaper_mod.clear_lease(job.id)
    job.status = "PENDIENTE"
    job.worker_node = None
    return "PENDIENTE"


async def dispatch_claimed(
    job: Job, worker, token: str, lease_id: str = "",
    lease_expires_at: object = None,
) -> str:
    """Despacha el sobre sellado fuera de la unidad de claim y lo interpreta."""
    expira_txt = None
    if lease_expires_at is not None:
        expira_txt = lease_expires_at.isoformat().replace("+00:00", "Z")
    try:
        ok = await dispatcher.dispatch_to_worker(
            job, worker, token, lease_id, expira_txt
        )
    except Exception:
        ok = False  # timeout/ambiguo: se resuelve por ack lease, no reencola ya
        return "ASIGNADO"
    return await handle_dispatch_result(job, worker.node, ok)


async def scheduler_round() -> dict:
    """Una vuelta de claims hasta ``scheduler_batch_size`` (con cesión)."""
    settings = get_settings()
    outcome = {"asignados": 0, "vacíos": 0, "sin_claim": 0}
    #: Rondas consecutivas sin progreso. Solo una entrega confirmada la
    #: reinicia: un claim exitoso no es progreso, porque puede morir en la
    #: reserva (sin worker con cupo o con PostgreSQL caído).
    sin_progreso = 0
    for _ in range(settings.scheduler_batch_size):
        job = claim_next_job()
        if job is None:
            if not has_schedulable_work():
                break  # cola realmente vacía: dormir
            sin_progreso += 1
            outcome["vacíos"] += 1
            if sin_progreso >= settings.scheduler_max_empty_rounds:
                break  # contención sostenida: ceder el turno
            continue
        worker, token, lease_id, expira = await claim_and_reserve(job)
        if worker is None:
            # Sin worker con cupo (o con el claim PostgreSQL caído) el job no se
            # puede entregar: devolverlo a PENDIENTE y ceder el turno. Si se
            # reintentara sin límite, cada iteración repetiría el mismo claim
            # fallido y una caída de base costaría ``scheduler_batch_size``
            # round-trips por vuelta; con el tope se duerme y se reintenta.
            job.status = "PENDIENTE"
            job.worker_node = None
            sin_progreso += 1
            outcome["sin_claim"] += 1
            if sin_progreso >= settings.scheduler_max_empty_rounds:
                break
            continue
        sin_progreso = 0
        await dispatch_claimed(job, worker, token, lease_id, expira)
        outcome["asignados"] += 1
    return outcome


async def scheduler_loop(replica_id: str = "") -> None:
    """Tarea supervisada por réplica: NOTIFY/best-effort + polling seguro."""
    _ = replica_id or secrets.token_hex(4)
    settings = get_settings()
    while settings.scheduler_enabled:
        await scheduler_round()
        if db_configurado():
            await reap_pg_vencidas()
        reaper_mod.reap_expired(utcnow())
        await asyncio.sleep(settings.scheduler_poll_interval_ms / 1000.0)


__all__ = [
    "PROTOCOL_VERSION",
    "has_schedulable_work",
    "claim_next_job",
    "claim_and_reserve",
    "handle_dispatch_result",
    "dispatch_claimed",
    "scheduler_round",
    "scheduler_loop",
    "reap_pg_vencidas",
]
