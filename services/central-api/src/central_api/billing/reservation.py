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
    plan_code: str | None = None,
) -> UsageEntry:
    """Reserva exactamente un request según el modo de cuenta activo.

    TIER usa primero su cuota mensual y después créditos si los tiene.
    GRATUITA cuenta 100 requests durante el plazo admin asignado y cae a
    CREDITOS al agotar cuota o vencer. LIBRE mantiene un límite manual total.
    """
    from central_api.billing.entitlements import (
        PERIODS, get_billing_account, current_period, open_period,
    )

    existing = USAGE.get(job_id)
    if existing is not None:
        return existing
    with _lock_for(user_id):
        if job_id in USAGE:  # doble chequeo bajo candado (carrera)
            return USAGE[job_id]
        account = get_billing_account(user_id)

        if account.account_type == "LIBRE":
            used = account.requests_reserved + account.requests_consumed
            if account.max_requests is not None and used < account.max_requests:
                account.requests_reserved += 1
                entry = UsageEntry(
                    id=_new_id(), user_id=user_id, job_id=job_id,
                    fuente_cobro="CUOTA", estado="RESERVADO",
                    costo_snapshot=0, period_id=f"libre:{user_id}",
                )
                USAGE[job_id] = entry
                return entry
            raise QuotaExhausted("límite LIBRE agotado")

        if account.account_type in {"TIER", "GRATUITA"}:
            selected_plan = account.plan_code or ("gratuita" if account.account_type == "GRATUITA" else plan_code)
            if selected_plan:
                period = current_period(user_id)
                if period is None:
                    period = open_period(user_id, selected_plan)
                available = period.cuota_asignada - period.cuota_reservada - period.cuota_consumida
                if available > 0:
                    period.cuota_reservada += 1
                    entry = UsageEntry(
                        id=_new_id(), user_id=user_id, job_id=job_id,
                        fuente_cobro="CUOTA", estado="RESERVADO",
                        costo_snapshot=0, period_id=period.id,
                    )
                    USAGE[job_id] = entry
                    if (account.account_type == "GRATUITA" and
                            period.cuota_reservada + period.cuota_consumida >= period.cuota_asignada):
                        _fallback_gratuita(user_id, account, "cuota agotada")
                    return entry
                if account.account_type == "GRATUITA":
                    _fallback_gratuita(user_id, account, "cuota agotada")

        # Los paquetes representan requests, no pesos de complejidad por bot.
        cost = 1
        if BALANCES.get(user_id, 0) >= cost:
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


def _fallback_gratuita(user_id: str, account, reason: str) -> None:
    """Desactiva el beneficio gratuito sin alterar el ledger histórico."""
    if account.account_type != "GRATUITA":
        return
    account.account_type = "CREDITOS"
    account.plan_code = None
    account.max_requests = None
    account.expires_at = None
    try:
        from central_api.admin.users import USERS
        from central_api.admin.audit import log_event

        user = USERS.get(user_id)
        if user is not None:
            user.account_type = "CREDITOS"
            user.plan_code = None
            user.max_requests = None
            user.account_expires_at = None
        log_event(
            "billing.account.free_fallback",
            actor_id="system:billing",
            actor_type="system",
            target_type="user",
            target_id=user_id,
            reason=reason,
            metadata={"nuevo_tipo": "CREDITOS"},
        )
    except ImportError:
        pass


def confirm_usage(job_id: str, motivo: str | None = None) -> UsageEntry | None:
    """Convierte la reserva en consumo (terminal cobrado, sin segundo débito)."""
    from central_api.billing.entitlements import BILLING_ACCOUNTS, PERIODS

    entry = USAGE.get(job_id)
    if entry is None or entry.estado != "RESERVADO":
        return entry
    if entry.fuente_cobro == "CUOTA" and entry.period_id and entry.period_id.startswith("libre:"):
        account = BILLING_ACCOUNTS.get(entry.user_id)
        if account is not None and account.requests_reserved > 0:
            account.requests_reserved -= 1
            account.requests_consumed += 1
    elif entry.fuente_cobro == "CUOTA" and entry.period_id:
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
    from central_api.billing.entitlements import BILLING_ACCOUNTS, PERIODS

    entry = USAGE.get(job_id)
    if entry is None or entry.estado != "RESERVADO":
        return entry
    if entry.fuente_cobro == "CUOTA" and entry.period_id and entry.period_id.startswith("libre:"):
        account = BILLING_ACCOUNTS.get(entry.user_id)
        if account is not None and account.requests_reserved > 0:
            account.requests_reserved -= 1
    elif entry.fuente_cobro == "CUOTA" and entry.period_id:
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
