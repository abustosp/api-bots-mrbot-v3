"""Reserva, confirmación y liberación de requests (respaldo en memoria).

Cada job reserva exactamente 1 request al crearse, antes de asignarse. Según
el tipo de cuenta:

* ``PRUEBA``: cuenta contra un tope total de 100, sin renovación.
* ``TESTING``: siempre admite; el uso se sigue registrando.
* ``NORMAL``: cuenta contra el cupo del mes en curso; el período siguiente se
  abre solo con la primera reserva del mes.

Una reserva se confirma al terminar el job, o se libera si se cancela o falla
por infraestructura (el cupo se devuelve). Con PostgreSQL esto lo hace
``billing.postgres`` dentro de la transacción del job; este módulo es el
respaldo para desarrollo y pruebas sin base de datos.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass


@dataclass(frozen=True)
class UsageEntry:
    """Reserva de un request ligada a un job (única por job)."""

    id: str
    user_id: str
    job_id: str
    estado: str  # RESERVADO | CONFIRMADO | LIBERADO | REEMBOLSADO
    period_id: str | None = None
    motivo: str | None = None
    unidades: int = 1


class QuotaExhausted(Exception):
    """El cupo de la cuenta está agotado (429 público)."""


USAGE: dict[str, UsageEntry] = {}  # por job_id, único
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(user_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(user_id, threading.Lock())


def _new_id() -> str:
    return str(uuid.uuid4())


def get_usage(job_id: str) -> UsageEntry | None:
    """Reserva de uso asociada a un job, o ``None``."""
    return USAGE.get(job_id)


def reserve_for_job(
    user_id: str, job_id: str, bot: str = "", operation: str = ""
) -> UsageEntry:
    """Reserva un request según el tipo de cuenta o levanta ``QuotaExhausted``."""
    from central_api.billing.entitlements import (
        ACCOUNT_NORMAL,
        ACCOUNT_TESTING,
        PRUEBA_MAX_REQUESTS,
        get_billing_account,
        open_period,
    )

    existing = USAGE.get(job_id)
    if existing is not None:
        return existing
    with _lock_for(user_id):
        if job_id in USAGE:  # doble chequeo bajo candado
            return USAGE[job_id]
        account = get_billing_account(user_id)
        period_id: str | None = None
        if account.account_type == ACCOUNT_NORMAL:
            period = open_period(user_id)
            used = period.cuota_reservada + period.cuota_consumida
            if used >= period.cuota_asignada:
                raise QuotaExhausted(f"cupo mensual agotado para {bot}/{operation}")
            period.cuota_reservada += 1
            period_id = period.id
        elif account.account_type == ACCOUNT_TESTING:
            account.requests_reserved += 1
        else:
            used = account.requests_reserved + account.requests_consumed
            if used >= PRUEBA_MAX_REQUESTS:
                raise QuotaExhausted(f"cuenta de prueba agotada para {bot}/{operation}")
            account.requests_reserved += 1
        entry = UsageEntry(
            id=_new_id(), user_id=user_id, job_id=job_id,
            estado="RESERVADO", period_id=period_id,
        )
        USAGE[job_id] = entry
        return entry


def _finish_prueba(account) -> None:
    """Prueba agotada: pasa a NORMAL y se deshabilita al usuario (sin revocar claves)."""
    from central_api.billing.entitlements import ACCOUNT_NORMAL, PRUEBA_MAX_REQUESTS
    from central_api.billing.postgres import _audit_prueba_finished

    account.account_type = ACCOUNT_NORMAL
    account.max_requests = PRUEBA_MAX_REQUESTS
    account.requests_reserved = 0
    account.requests_consumed = 0
    _audit_prueba_finished(account.user_id)


def _settle(job_id: str, estado: str, consume: bool, motivo: str | None):
    from central_api.billing.entitlements import (
        BILLING_ACCOUNTS,
        PERIODS,
        PRUEBA_MAX_REQUESTS,
    )

    entry = USAGE.get(job_id)
    if entry is None or entry.estado != "RESERVADO":
        return entry
    with _lock_for(entry.user_id):
        entry = USAGE.get(job_id)
        if entry is None or entry.estado != "RESERVADO":
            return entry
        if entry.period_id:
            period = next(
                (p for p in PERIODS.values() if p.id == entry.period_id), None
            )
            if period is not None and period.cuota_reservada > 0:
                period.cuota_reservada -= 1
                if consume:
                    period.cuota_consumida += 1
        else:
            account = BILLING_ACCOUNTS.get(entry.user_id)
            if account is not None and account.requests_reserved > 0:
                account.requests_reserved -= 1
                if consume:
                    account.requests_consumed += 1
                if (
                    consume
                    and account.account_type == "PRUEBA"
                    and account.requests_consumed >= PRUEBA_MAX_REQUESTS
                ):
                    _finish_prueba(account)
        done = UsageEntry(
            id=entry.id, user_id=entry.user_id, job_id=job_id, estado=estado,
            period_id=entry.period_id, motivo=motivo, unidades=entry.unidades,
        )
        USAGE[job_id] = done
        return done


def confirm_usage(job_id: str, motivo: str | None = None) -> UsageEntry | None:
    """Convierte la reserva en consumo (job terminado)."""
    return _settle(job_id, "CONFIRMADO", True, motivo)


def release_usage(job_id: str, motivo: str | None = None) -> UsageEntry | None:
    """Libera la reserva devolviendo el cupo (cancelado antes de ejecutar)."""
    return _settle(job_id, "LIBERADO", False, motivo)


def refund_usage(job_id: str, motivo: str) -> UsageEntry | None:
    """Reembolsa el cupo tras un fallo de infraestructura sin reintentos."""
    entry = _settle(job_id, "REEMBOLSADO", False, motivo)
    return entry


__all__ = [
    "QuotaExhausted",
    "USAGE",
    "UsageEntry",
    "confirm_usage",
    "get_usage",
    "refund_usage",
    "release_usage",
    "reserve_for_job",
]
