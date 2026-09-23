"""Reserva, confirmación y reversión de consumo (plan 04 §5-§6, B-1/B-2/B-4).

Reglas: la reserva se hace al crear el job, antes de asignar (B-2); primero
cuota del período, luego créditos como overflow; una sola fuente por job;
cada movimiento deja fila inmutable (B-1); el débito de créditos se decide
bajo bloqueo de saldo del usuario (B-4, aquí con candado por usuario).

En PostgreSQL esto corre en una transacción con ``SELECT ... FOR UPDATE``;
el esqueleto usa un candado en memoria con idéntica semántica de serie.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True)
class UsageEntry:
    """Fila de ``usage_ledger``: un job, una reserva, una fuente congelada."""

    id: str
    user_id: str
    job_id: str
    fuente_cobro: str  # CUOTA | CREDITOS
    estado: str  # RESERVADO | CONFIRMADO | LIBERADO | REEMBOLSADO
    costo_snapshot: int = 0
    period_id: str | None = None
    motivo: str | None = None


@dataclass(frozen=True)
class CreditMove:
    """Fila append-only de ``credit_ledger`` (nunca update/delete)."""

    id: str
    user_id: str
    amount: int
    movimiento: str  # COMPRA|RESERVA|LIBERACION_RESERVA|CONSUMO_CONFIRMADO|...
    job_id: str | None = None
    payment_id: str | None = None
    idempotency_key: str = ""
    creado_en: datetime = field(default_factory=_utcnow)


class QuotaExhausted(Exception):
    """Sin cuota utilizable ni créditos suficientes (429 público)."""


USAGE: dict[str, UsageEntry] = {}  # por job_id, único (UNIQUE job_id)
CREDIT_MOVES: list[CreditMove] = []
BALANCES: dict[str, int] = {}
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(user_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(user_id, threading.Lock())


def get_balance(user_id: str) -> int:
    """Saldo derivado proyectado (en PG se reconcilia con ``SUM(ledger)``)."""
    return BALANCES.get(user_id, 0)


def get_usage(job_id: str) -> UsageEntry | None:
    """Reserva de uso asociada a un job, o ``None``."""
    return USAGE.get(job_id)


def _append_credit(
    user_id: str, amount: int, movimiento: str, *,
    job_id: str | None = None, payment_id: str | None = None,
    idempotency_key: str = "",
) -> CreditMove:
    move = CreditMove(
        id=_new_id(), user_id=user_id, amount=amount, movimiento=movimiento,
        job_id=job_id, payment_id=payment_id, idempotency_key=idempotency_key,
    )
    CREDIT_MOVES.append(move)
    BALANCES[user_id] = BALANCES.get(user_id, 0) + amount
    return move


def reserve_for_job(
    user_id: str, job_id: str, bot: str, operation: str,
    plan_code: str = "free",
) -> UsageEntry:
    """Reserva cuota o créditos para un job (idempotente por ``job_id``).

    Precedencia: cuota del período vigente, luego créditos. Lanza
    ``QuotaExhausted`` sin reserva parcial si no hay fondos.
    """
    from central_api.billing.entitlements import (
        BOT_CREDIT_COST, PERIODS, current_period, open_period,
    )

    existing = USAGE.get(job_id)
    if existing is not None:
        return existing
    with _lock_for(user_id):
        if job_id in USAGE:  # doble chequeo bajo candado (carrera)
            return USAGE[job_id]
        period = current_period(user_id)
        if period is None:
            period = open_period(user_id, plan_code)
        free = period.cuota_asignada - period.cuota_reservada - period.cuota_consumida
        if free > 0:
            period.cuota_reservada += 1
            entry = UsageEntry(
                id=_new_id(), user_id=user_id, job_id=job_id,
                fuente_cobro="CUOTA", estado="RESERVADO",
                costo_snapshot=0, period_id=period.id,
            )
            USAGE[job_id] = entry
            return entry
        cost = BOT_CREDIT_COST.get((bot, operation), 1)
        if BALANCES.get(user_id, 0) >= cost and cost >= 0:
            _append_credit(user_id, -cost, "RESERVA", job_id=job_id,
                           idempotency_key=f"job:{job_id}:reserva")
            entry = UsageEntry(
                id=_new_id(), user_id=user_id, job_id=job_id,
                fuente_cobro="CREDITOS", estado="RESERVADO",
                costo_snapshot=cost,
            )
            USAGE[job_id] = entry
            return entry
        raise QuotaExhausted(f"sin cuota ni créditos para {bot}/{operation}")


def confirm_usage(job_id: str, motivo: str | None = None) -> UsageEntry | None:
    """Convierte la reserva en consumo (terminal cobrado, sin segundo débito)."""
    from central_api.billing.entitlements import PERIODS

    entry = USAGE.get(job_id)
    if entry is None or entry.estado != "RESERVADO":
        return entry
    if entry.fuente_cobro == "CUOTA" and entry.period_id:
        for period in PERIODS.values():
            if period.id == entry.period_id and period.cuota_reservada > 0:
                period.cuota_reservada -= 1
                period.cuota_consumida += 1
                break
    else:
        _append_credit(entry.user_id, 0, "CONSUMO_CONFIRMADO", job_id=job_id,
                       idempotency_key=f"job:{job_id}:confirmado")
    done = UsageEntry(
        id=entry.id, user_id=entry.user_id, job_id=job_id,
        fuente_cobro=entry.fuente_cobro, estado="CONFIRMADO",
        costo_snapshot=entry.costo_snapshot, period_id=entry.period_id,
        motivo=motivo,
    )
    USAGE[job_id] = done
    return done


def release_usage(job_id: str, motivo: str | None = None) -> UsageEntry | None:
    """Libera la reserva (cancelado en PENDIENTE: siempre, exactamente 1 vez)."""
    from central_api.billing.entitlements import PERIODS

    entry = USAGE.get(job_id)
    if entry is None or entry.estado != "RESERVADO":
        return entry
    if entry.fuente_cobro == "CUOTA" and entry.period_id:
        for period in PERIODS.values():
            if period.id == entry.period_id and period.cuota_reservada > 0:
                period.cuota_reservada -= 1
                break
    else:
        _append_credit(entry.user_id, entry.costo_snapshot, "LIBERACION_RESERVA",
                       job_id=job_id, idempotency_key=f"job:{job_id}:liberado")
    done = UsageEntry(
        id=entry.id, user_id=entry.user_id, job_id=job_id,
        fuente_cobro=entry.fuente_cobro, estado="LIBERADO",
        costo_snapshot=entry.costo_snapshot, period_id=entry.period_id,
        motivo=motivo,
    )
    USAGE[job_id] = done
    return done


def refund_usage(job_id: str, motivo: str) -> UsageEntry | None:
    """Reembolsa tras fallo de infraestructura con reintentos agotados."""
    entry = release_usage(job_id, motivo=motivo)
    if entry is None:
        return None
    refunded = UsageEntry(
        id=entry.id, user_id=entry.user_id, job_id=job_id,
        fuente_cobro=entry.fuente_cobro, estado="REEMBOLSADO",
        costo_snapshot=entry.costo_snapshot, period_id=entry.period_id,
        motivo=motivo,
    )
    USAGE[job_id] = refunded
    return refunded


def adjust_credits(
    user_id: str, amount: int, motivo: str, *,
    actor: str = "", referencia: str = "",
    idempotency_key: str = "",
) -> CreditMove:
    """Ajuste manual append-only (``AJUSTE``) con motivo obligatorio.

    Nunca edita saldos: agrega un movimiento firmado ligado al motivo y al
    actor. Idempotente por llave explícita o derivada de la referencia.
    """
    if not (motivo or "").strip():
        raise ValueError("motivo obligatorio para el ajuste")
    llave = (
        idempotency_key
        or f"ajuste:{user_id}:{referencia}:{amount}:{motivo.strip()}"
    )
    for move in CREDIT_MOVES:
        if move.user_id == user_id and move.idempotency_key == llave:
            return move
    with _lock_for(user_id):
        for move in CREDIT_MOVES:
            if move.user_id == user_id and move.idempotency_key == llave:
                return move
        _ = actor
        return _append_credit(user_id, amount, "AJUSTE", idempotency_key=llave)


def credit_purchase(
    user_id: str, creditos: int, payment_id: str, idempotency_key: str
) -> CreditMove:
    """Acredita una compra confirmada (idempotente por llave, B-3)."""
    for move in CREDIT_MOVES:
        if move.user_id == user_id and move.idempotency_key == idempotency_key:
            return move
    with _lock_for(user_id):
        for move in CREDIT_MOVES:
            if move.user_id == user_id and move.idempotency_key == idempotency_key:
                return move
        return _append_credit(user_id, creditos, "COMPRA", payment_id=payment_id,
                              idempotency_key=idempotency_key)


__all__ = [
    "UsageEntry",
    "CreditMove",
    "QuotaExhausted",
    "USAGE",
    "CREDIT_MOVES",
    "BALANCES",
    "get_balance",
    "get_usage",
    "reserve_for_job",
    "confirm_usage",
    "release_usage",
    "refund_usage",
    "adjust_credits",
    "credit_purchase",
]
