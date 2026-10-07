"""PostgreSQL source of truth for account entitlements and request billing.

Callers pass their current ``AsyncSession`` when billing must be atomic with a
job transition. Account-row locks serialize admissions across API processes;
period, usage, and credit-ledger writes share that transaction.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from central_api.billing.reservation import QuotaExhausted, UsageEntry
from central_api.models.base import new_uuid7
from central_api.models.billing import (
    BillingAccount,
    BillingAccountPeriod,
    BillingUsage,
    CreditLedger,
    Plan,
)


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def _lock_account(session: AsyncSession, user_id: uuid.UUID) -> BillingAccount:
    await session.execute(
        insert(BillingAccount)
        .values(user_id=user_id, account_type="CREDITOS")
        .on_conflict_do_nothing(index_elements=[BillingAccount.user_id])
    )
    account = (
        await session.execute(
            select(BillingAccount)
            .where(BillingAccount.user_id == user_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if account is None:
        raise RuntimeError("billing account missing after idempotent insert")
    return account


async def _credit_balance(session: AsyncSession, user_id: uuid.UUID) -> int:
    value = await session.scalar(
        select(func.coalesce(func.sum(CreditLedger.units), 0)).where(
            CreditLedger.user_id == user_id
        )
    )
    return int(value or 0)


async def _append_credit(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    units: int,
    entry_type: str,
    idempotency_key: str,
    job_id: uuid.UUID | None = None,
    reason: str | None = None,
) -> CreditLedger:
    row = CreditLedger(
        id=new_uuid7(),
        user_id=user_id,
        job_id=job_id,
        entry_type=entry_type,
        units=units,
        idempotency_key=idempotency_key,
        reason=(reason or None),
    )
    session.add(row)
    await session.flush()
    return row


async def reserve_in_session(
    session: AsyncSession,
    *,
    user_id: str | uuid.UUID,
    job_id: str | uuid.UUID,
    bot: str,
    operation: str,
) -> UsageEntry:
    """Reserve one request exactly once while the job insert is uncommitted."""
    uid, jid = _uuid(user_id), _uuid(job_id)
    account = await _lock_account(session, uid)
    prior = (
        await session.execute(
            select(BillingUsage).where(BillingUsage.job_id == jid).with_for_update()
        )
    ).scalar_one_or_none()
    if prior is not None:
        return _as_usage(prior)

    now = datetime.now(timezone.utc)
    if (
        account.account_type == "GRATUITA"
        and account.expires_at is not None
        and account.expires_at <= now
    ):
        account.account_type = "CREDITOS"
        account.plan_code = None
        account.max_requests = None
        account.expires_at = None
        account.updated_at = now
        await session.execute(
            select(BillingAccountPeriod)
            .where(
                BillingAccountPeriod.user_id == uid,
                BillingAccountPeriod.status == "ABIERTO",
            )
            .with_for_update()
        )
        await session.execute(
            BillingAccountPeriod.__table__.update()
            .where(
                BillingAccountPeriod.user_id == uid,
                BillingAccountPeriod.status == "ABIERTO",
            )
            .values(status="CERRADO", close_reason="plazo gratuito vencido")
        )

    mode = account.account_type
    period: BillingAccountPeriod | None = None
    if mode in {"TIER", "GRATUITA"}:
        plan_code = account.plan_code or ("gratuita" if mode == "GRATUITA" else None)
        if not plan_code:
            raise QuotaExhausted("plan de cuenta sin configurar")
        period = (
            await session.execute(
                select(BillingAccountPeriod)
                .where(
                    BillingAccountPeriod.user_id == uid,
                    BillingAccountPeriod.status == "ABIERTO",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if period is not None and not (period.starts_at <= now < period.ends_at):
            period.status = "CERRADO"
            period.close_reason = "periodo vencido"
            period = None
        if mode == "GRATUITA" and (
            account.expires_at is None or account.expires_at <= now
        ):
            account.account_type = "CREDITOS"
            account.plan_code = None
            account.max_requests = None
            account.expires_at = None
            account.updated_at = now
            mode = "CREDITOS"
            period = None
        if period is None and mode in {"TIER", "GRATUITA"}:
            plan = (
                await session.execute(select(Plan).where(Plan.code == plan_code))
            ).scalar_one_or_none()
            if plan is None or not plan.active:
                raise QuotaExhausted("plan de cuenta inactivo")
            end = (
                account.expires_at
                if mode == "GRATUITA"
                else now + timedelta(days=int(plan.period_days))
            )
            if end is None or end <= now:
                if mode == "GRATUITA":
                    account.account_type = "CREDITOS"
                    account.plan_code = None
                    account.max_requests = None
                    account.expires_at = None
                    account.updated_at = now
                    mode = "CREDITOS"
                else:
                    end = now + timedelta(days=int(plan.period_days))
            if mode in {"TIER", "GRATUITA"}:
                period = BillingAccountPeriod(
                    id=new_uuid7(),
                    user_id=uid,
                    plan_code=plan_code,
                    account_type=mode,
                    starts_at=now,
                    ends_at=end,
                    included_requests=(
                        int(account.max_requests or 100)
                        if mode == "GRATUITA"
                        else int(plan.included_units)
                    ),
                )
                session.add(period)
                await session.flush()

        if period is not None:
            available = (
                period.included_requests
                - period.requests_reserved
                - period.requests_consumed
            )
            if available > 0:
                period.requests_reserved += 1
                if mode == "GRATUITA":
                    account.requests_reserved += 1
                account.updated_at = now
                usage = BillingUsage(
                    id=new_uuid7(),
                    user_id=uid,
                    job_id=jid,
                    account_period_id=period.id,
                    account_type=mode,
                    generation_id=account.generation_id,
                    billing_source="CUOTA",
                    units=1,
                )
                session.add(usage)
                await session.flush()
                return _as_usage(usage)
            if mode == "GRATUITA":
                # Exhausted grants fall back to paid credits without resetting
                # the durable free-period counters.
                period.status = "CERRADO"
                period.close_reason = "cuota gratuita agotada"
                account.account_type = "CREDITOS"
                account.plan_code = None
                account.max_requests = None
                account.expires_at = None
                account.updated_at = now
                mode = "CREDITOS"
            elif mode == "TIER":
                mode = "TIER"  # the tier's documented overflow is credits

    if mode == "LIBRE":
        used = account.requests_reserved + account.requests_consumed
        if account.max_requests is None or used >= account.max_requests:
            raise QuotaExhausted("límite LIBRE agotado")
        account.requests_reserved += 1
        account.updated_at = now
        usage = BillingUsage(
            id=new_uuid7(),
            user_id=uid,
            job_id=jid,
            account_type="LIBRE",
            generation_id=account.generation_id,
            billing_source="CUOTA",
            units=1,
        )
        session.add(usage)
        await session.flush()
        return _as_usage(usage)

    balance = await _credit_balance(session, uid)
    if balance < 1:
        if mode == "TIER":
            raise QuotaExhausted(f"sin cuota ni créditos para {bot}/{operation}")
        raise QuotaExhausted(f"sin créditos para {bot}/{operation}")
    await _append_credit(
        session,
        user_id=uid,
        units=-1,
        entry_type="DEBITO",
        idempotency_key=f"job:{jid}:reserva",
        job_id=jid,
        reason="reserva de request",
    )
    usage = BillingUsage(
        id=new_uuid7(),
        user_id=uid,
        job_id=jid,
        account_type=mode,
        generation_id=account.generation_id,
        billing_source="CREDITOS",
        units=1,
    )
    session.add(usage)
    await session.flush()
    return _as_usage(usage)


async def settle_in_session(
    session: AsyncSession,
    job_id: str | uuid.UUID,
    *,
    status: str,
    reason: str | None = None,
) -> UsageEntry | None:
    """Confirm or release one reservation idempotently inside a DB transaction."""
    if status not in {"CONFIRMADO", "LIBERADO", "REEMBOLSADO"}:
        raise ValueError("estado de liquidación inválido")
    jid = _uuid(job_id)
    snapshot = (
        await session.execute(select(BillingUsage).where(BillingUsage.job_id == jid))
    ).scalar_one_or_none()
    if snapshot is None:
        return None
    account = await _lock_account(session, snapshot.user_id)
    usage = (
        await session.execute(
            select(BillingUsage).where(BillingUsage.job_id == jid).with_for_update()
        )
    ).scalar_one_or_none()
    if usage is None:
        return None
    if usage.status != "RESERVADO":
        return _as_usage(usage)

    now = datetime.now(timezone.utc)
    if usage.billing_source == "CUOTA":
        if usage.account_period_id is not None:
            period = (
                await session.execute(
                    select(BillingAccountPeriod)
                    .where(BillingAccountPeriod.id == usage.account_period_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if period is not None:
                period.requests_reserved = max(0, period.requests_reserved - usage.units)
                if status == "CONFIRMADO":
                    period.requests_consumed += usage.units
                if (
                    usage.account_type == "GRATUITA"
                    and usage.generation_id == account.generation_id
                ):
                    account.requests_reserved = max(
                        0, account.requests_reserved - usage.units
                    )
                    if status == "CONFIRMADO":
                        account.requests_consumed += usage.units
                    if (
                        status == "CONFIRMADO"
                        and account.account_type == "GRATUITA"
                        and period.requests_consumed + period.requests_reserved
                        >= period.included_requests
                    ):
                        period.status = "CERRADO"
                        period.close_reason = "cuota gratuita consumida"
                        account.account_type = "CREDITOS"
                        account.plan_code = None
                        account.max_requests = None
                        account.expires_at = None
        elif (
            usage.account_type == "LIBRE"
            and account.account_type == "LIBRE"
            and usage.generation_id == account.generation_id
        ):
            account.requests_reserved = max(0, account.requests_reserved - usage.units)
            if status == "CONFIRMADO":
                account.requests_consumed += usage.units
        account.updated_at = now
    elif status in {"LIBERADO", "REEMBOLSADO"}:
        await _append_credit(
            session,
            user_id=usage.user_id,
            units=usage.units,
            entry_type="REEMBOLSO",
            idempotency_key=f"job:{jid}:release",
            job_id=jid,
            reason=(reason or "reserva liberada")[:160],
        )

    usage.status = status
    usage.reason = (reason or None)
    usage.updated_at = now
    await session.flush()
    return _as_usage(usage)


async def adjust_credits_in_session(
    session: AsyncSession,
    *,
    user_id: str | uuid.UUID,
    amount: int,
    reason: str,
    idempotency_key: str,
) -> CreditLedger:
    if amount == 0:
        raise ValueError("el ajuste debe ser distinto de cero")
    uid = _uuid(user_id)
    idempotency_key = idempotency_key[:160]
    await _lock_account(session, uid)
    prior = (
        await session.execute(
            select(CreditLedger).where(CreditLedger.idempotency_key == idempotency_key)
        )
    ).scalar_one_or_none()
    if prior is not None:
        if prior.user_id != uid or prior.units != amount:
            raise ValueError("clave de idempotencia reutilizada con otro ajuste")
        return prior
    return await _append_credit(
        session,
        user_id=uid,
        units=amount,
        entry_type="AJUSTE",
        idempotency_key=idempotency_key[:160],
        reason=reason[:160],
    )


async def credit_purchase_in_session(
    session: AsyncSession,
    *,
    user_id: str | uuid.UUID,
    amount: int,
    payment_id: str,
    idempotency_key: str,
) -> CreditLedger:
    """Append a verified purchase once while serializing the user's balance."""
    if amount <= 0:
        raise ValueError("la compra debe acreditar una cantidad positiva")
    uid = _uuid(user_id)
    key = idempotency_key[:160]
    await _lock_account(session, uid)
    prior = (
        await session.execute(
            select(CreditLedger).where(CreditLedger.idempotency_key == key)
        )
    ).scalar_one_or_none()
    if prior is not None:
        if prior.user_id != uid or prior.units != amount:
            raise ValueError("clave de idempotencia reutilizada con otra compra")
        return prior
    return await _append_credit(
        session,
        user_id=uid,
        units=amount,
        entry_type="COMPRA",
        idempotency_key=key,
        reason=f"compra verificada {payment_id}"[:160],
    )


async def force_period_for_user(
    user_id: str,
    plan_code: str,
    reason: str,
) -> dict:
    """Close the current period and persist a fresh administrator grant."""
    from central_api.db import nueva_sesion
    from central_api.models.identity import User

    if not (reason or "").strip():
        raise ValueError("motivo obligatorio para forzar el período")
    aliases = {"free": "gratuita", "basico": "tier-100", "pro": "tier-1000", "empresa": "tier-10000"}
    code = aliases.get(plan_code, plan_code)
    uid = _uuid(user_id)
    now = datetime.now(timezone.utc)
    async with nueva_sesion() as session:
        if await session.get(User, uid) is None:
            raise ValueError("usuario no encontrado")
        account = await _lock_account(session, uid)
        plan = (
            await session.execute(select(Plan).where(Plan.code == code, Plan.active.is_(True)))
        ).scalar_one_or_none()
        if plan is None or (code != "gratuita" and not code.startswith("tier-")):
            raise ValueError("plan fuera de catálogo")
        open_periods = (
            await session.execute(
                select(BillingAccountPeriod)
                .where(
                    BillingAccountPeriod.user_id == uid,
                    BillingAccountPeriod.status == "ABIERTO",
                )
                .with_for_update()
            )
        ).scalars().all()
        for old in open_periods:
            old.status = "CERRADO"
            old.close_reason = ("forzado: " + reason.strip())[:160]

        mode = "GRATUITA" if code == "gratuita" else "TIER"
        days = int(plan.period_days)
        ends_at = now + timedelta(days=days)
        account.account_type = mode
        account.plan_code = code
        account.max_requests = 100 if mode == "GRATUITA" else None
        account.starts_at = now
        account.expires_at = ends_at if mode == "GRATUITA" else None
        account.requests_reserved = 0
        account.requests_consumed = 0
        account.generation_id = uuid.uuid4()
        account.updated_at = now
        period = BillingAccountPeriod(
            id=new_uuid7(),
            user_id=uid,
            plan_code=code,
            account_type=mode,
            starts_at=now,
            ends_at=ends_at,
            included_requests=(100 if mode == "GRATUITA" else int(plan.included_units)),
        )
        session.add(period)
        await session.flush()
        return {
            "id": str(period.id),
            "user_id": str(period.user_id),
            "plan_code": period.plan_code,
            "starts_at": period.starts_at,
            "ends_at": period.ends_at,
            "included_requests": period.included_requests,
        }


async def close_expired_periods_in_db(now: datetime | None = None) -> list[str]:
    """Close expired periods with account-first locking across API processes."""
    from central_api.db import nueva_sesion

    moment = now or datetime.now(timezone.utc)
    closed: list[str] = []
    async with nueva_sesion() as session:
        candidates = (
            await session.execute(
                select(BillingAccountPeriod.id, BillingAccountPeriod.user_id)
                .where(
                    BillingAccountPeriod.status == "ABIERTO",
                    BillingAccountPeriod.ends_at <= moment,
                )
                .order_by(BillingAccountPeriod.ends_at)
            )
        ).all()
        for period_id, user_id in candidates:
            account = await _lock_account(session, user_id)
            period = (
                await session.execute(
                    select(BillingAccountPeriod)
                    .where(
                        BillingAccountPeriod.id == period_id,
                        BillingAccountPeriod.status == "ABIERTO",
                        BillingAccountPeriod.ends_at <= moment,
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if period is None:
                continue
            period.status = "CERRADO"
            period.close_reason = "período vencido"
            if (
                period.account_type == "GRATUITA"
                and account.account_type == "GRATUITA"
                and account.expires_at is not None
                and account.expires_at <= moment
            ):
                account.account_type = "CREDITOS"
                account.plan_code = None
                account.max_requests = None
                account.expires_at = None
                account.updated_at = moment
            closed.append(str(period.id))
        await session.flush()
    return closed


async def balance_for_user(user_id: str) -> int:
    from central_api.db import nueva_sesion

    uid = _uuid(user_id)
    async with nueva_sesion() as session:
        return await _credit_balance(session, uid)


async def period_count_for_user(user_id: str) -> int:
    from central_api.db import nueva_sesion

    uid = _uuid(user_id)
    async with nueva_sesion() as session:
        count = await session.scalar(
            select(func.count())
            .select_from(BillingAccountPeriod)
            .where(BillingAccountPeriod.user_id == uid)
        )
        return int(count or 0)


async def usage_for_user(user_id: str) -> list[dict]:
    from central_api.db import nueva_sesion

    uid = _uuid(user_id)
    async with nueva_sesion() as session:
        rows = (
            await session.execute(
                select(BillingUsage)
                .where(BillingUsage.user_id == uid)
                .order_by(BillingUsage.created_at.desc())
            )
        ).scalars().all()
        return [
            {
                "job_id": str(row.job_id),
                "fuente_cobro": row.billing_source,
                "estado": row.status,
                "costo_snapshot": row.units if row.billing_source == "CREDITOS" else 0,
            }
            for row in rows
        ]


async def credit_moves_for_user(user_id: str) -> list[dict]:
    from central_api.db import nueva_sesion

    uid = _uuid(user_id)
    async with nueva_sesion() as session:
        rows = (
            await session.execute(
                select(CreditLedger)
                .where(CreditLedger.user_id == uid)
                .order_by(CreditLedger.created_at.desc())
                .limit(20)
            )
        ).scalars().all()
        return [
            {
                "movimiento": row.entry_type,
                "amount": row.units,
                "job_id": str(row.job_id) if row.job_id else None,
                "creado_en": row.created_at.isoformat().replace("+00:00", "Z"),
            }
            for row in rows
        ]


def _as_usage(row: BillingUsage) -> UsageEntry:
    return UsageEntry(
        id=str(row.id),
        user_id=str(row.user_id),
        job_id=str(row.job_id),
        fuente_cobro=row.billing_source,
        estado=row.status,
        costo_snapshot=row.units if row.billing_source == "CREDITOS" else 0,
        period_id=str(row.account_period_id) if row.account_period_id else None,
        motivo=row.reason,
    )


__all__ = [
    "adjust_credits_in_session",
    "balance_for_user",
    "close_expired_periods_in_db",
    "credit_moves_for_user",
    "credit_purchase_in_session",
    "force_period_for_user",
    "period_count_for_user",
    "reserve_in_session",
    "settle_in_session",
    "usage_for_user",
]
