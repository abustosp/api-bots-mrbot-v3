"""PostgreSQL como fuente de verdad de cuentas y consumo de requests.

Quien llama pasa su ``AsyncSession`` cuando la reserva debe ser atómica con la
creación del job. El bloqueo de fila de ``billing_accounts`` serializa las
admisiones entre procesos de la API; períodos y uso comparten esa transacción.

Tipos de cuenta (ver ``billing.entitlements``):

* ``PRUEBA``: 100 requests totales, contadores en ``billing_accounts``.
* ``TESTING``: sin tope, contadores en ``billing_accounts``.
* ``NORMAL``: ``max_requests`` por mes, contadores en
  ``billing_account_periods`` (un período abierto por usuario).
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from central_api.billing.entitlements import (
    ACCOUNT_NORMAL,
    ACCOUNT_PRUEBA,
    ACCOUNT_TESTING,
    PRUEBA_MAX_REQUESTS,
    month_bounds,
)
from central_api.billing.reservation import QuotaExhausted, UsageEntry
from central_api.models.base import new_uuid7
from central_api.models.billing import (
    BillingAccount,
    BillingAccountPeriod,
    BillingUsage,
)


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


async def _lock_account(session: AsyncSession, user_id: uuid.UUID) -> BillingAccount:
    """Bloquea la cuenta; si no existe se crea como PRUEBA (valor seguro)."""
    await session.execute(
        insert(BillingAccount)
        .values(
            user_id=user_id,
            account_type=ACCOUNT_PRUEBA,
            max_requests=PRUEBA_MAX_REQUESTS,
        )
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


async def _open_locked_period(
    session: AsyncSession, uid: uuid.UUID, account: BillingAccount, now: datetime
) -> BillingAccountPeriod:
    """Devuelve el período del mes en curso, cerrando uno vencido si hace falta."""
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
    if period is not None and period.starts_at <= now < period.ends_at:
        return period
    if period is not None:
        period.status = "CERRADO"
        period.close_reason = "período vencido"
        await session.flush()
    start, end = month_bounds(now)
    period = BillingAccountPeriod(
        id=new_uuid7(),
        user_id=uid,
        plan_code=None,
        account_type=ACCOUNT_NORMAL,
        starts_at=start,
        ends_at=end,
        included_requests=int(account.max_requests or 0),
    )
    session.add(period)
    await session.flush()
    return period


async def reserve_in_session(
    session: AsyncSession,
    *,
    user_id: str | uuid.UUID,
    job_id: str | uuid.UUID,
    bot: str = "",
    operation: str = "",
) -> UsageEntry:
    """Reserva un request exactamente una vez mientras el job no está commiteado."""
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
    mode = account.account_type
    period_id = None
    if mode == ACCOUNT_NORMAL:
        period = await _open_locked_period(session, uid, account, now)
        if period.requests_reserved + period.requests_consumed >= period.included_requests:
            raise QuotaExhausted(f"cupo mensual agotado para {bot}/{operation}")
        period.requests_reserved += 1
        period_id = period.id
    elif mode == ACCOUNT_TESTING:
        account.requests_reserved += 1
    else:
        if account.requests_reserved + account.requests_consumed >= PRUEBA_MAX_REQUESTS:
            raise QuotaExhausted(f"cuenta de prueba agotada para {bot}/{operation}")
        account.requests_reserved += 1
    account.updated_at = now
    usage = BillingUsage(
        id=new_uuid7(),
        user_id=uid,
        job_id=jid,
        account_period_id=period_id,
        account_type=mode,
        generation_id=account.generation_id,
        billing_source="CUOTA",
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
    """Confirma o libera una reserva de forma idempotente dentro de la transacción."""
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
    consume = status == "CONFIRMADO"
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
            if consume:
                period.requests_consumed += usage.units
    elif usage.generation_id == account.generation_id:
        # Si el admin reasignó la cuenta mientras el job corría, los contadores
        # ya se reiniciaron y esta reserva no debe tocarlos.
        account.requests_reserved = max(0, account.requests_reserved - usage.units)
        if consume:
            account.requests_consumed += usage.units
    account.updated_at = now
    usage.status = status
    usage.reason = (reason or None)
    usage.updated_at = now
    if (
        consume
        and usage.account_period_id is None
        and usage.generation_id == account.generation_id
        and account.account_type == ACCOUNT_PRUEBA
        and account.requests_consumed >= PRUEBA_MAX_REQUESTS
    ):
        await _finish_prueba(session, account, now)
    await session.flush()
    return _as_usage(usage)


async def _finish_prueba(
    session: AsyncSession, account: BillingAccount, now: datetime
) -> None:
    """Agotada la prueba: la cuenta pasa a NORMAL y el usuario se deshabilita.

    Las API keys no se revocan: al habilitar de nuevo desde el panel el usuario
    recupera su acceso. El cupo mensual queda en el valor de la prueba hasta que
    el administrador lo ajuste.
    """
    from central_api.models.identity import User

    account.account_type = ACCOUNT_NORMAL
    account.max_requests = PRUEBA_MAX_REQUESTS
    account.starts_at = now
    account.requests_reserved = 0
    account.requests_consumed = 0
    account.generation_id = uuid.uuid4()
    user = await session.get(User, account.user_id)
    if user is not None:
        user.habilitado = False
    _audit_prueba_finished(str(account.user_id))


def _audit_prueba_finished(user_id: str) -> None:
    try:
        from central_api.admin.audit import log_event
        from central_api.admin.users import USERS

        user = USERS.get(user_id)
        if user is not None:
            user.estado = "deshabilitado"
            user.account_type = ACCOUNT_NORMAL
            user.max_requests = PRUEBA_MAX_REQUESTS
            user.plan = "normal"
        log_event(
            "billing.prueba.finished",
            actor_id="system:billing",
            actor_type="system",
            target_type="user",
            target_id=user_id,
            reason="prueba agotada: pasa a NORMAL y queda deshabilitado",
            metadata={"nuevo_tipo": ACCOUNT_NORMAL, "habilitado": False},
        )
    except ImportError:
        pass


async def assign_account_in_db(
    user_id: uuid.UUID, account_type: str, max_requests: int | None
) -> None:
    """Asigna el tipo de cuenta de forma durable.

    Reasignar el mismo tipo conserva el consumo (una cuenta de prueba no se
    renueva re-guardándola). Cambiar de tipo reinicia los contadores y cierra
    el período abierto. En NORMAL, cambiar el cupo ajusta el mes en curso.
    """
    from central_api.db import nueva_sesion
    from central_api.models.identity import User

    now = datetime.now(timezone.utc)
    try:
        async with nueva_sesion() as session:
            if await session.get(User, user_id) is None:
                raise RuntimeError("usuario no encontrado")
            account = await _lock_account(session, user_id)
            same_type = account.account_type == account_type
            if not same_type:
                account.account_type = account_type
                account.starts_at = now
                account.requests_reserved = 0
                account.requests_consumed = 0
                account.generation_id = uuid.uuid4()
            account.max_requests = max_requests
            account.plan_code = None
            account.expires_at = None
            account.updated_at = now
            open_periods = (
                await session.execute(
                    select(BillingAccountPeriod)
                    .where(
                        BillingAccountPeriod.user_id == user_id,
                        BillingAccountPeriod.status == "ABIERTO",
                    )
                    .with_for_update()
                )
            ).scalars().all()
            for period in open_periods:
                if account_type == ACCOUNT_NORMAL and same_type:
                    period.included_requests = int(max_requests or 0)
                else:
                    period.status = "CERRADO"
                    period.close_reason = "asignación de cuenta modificada"
            await session.flush()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("no se pudo asignar el tipo de cuenta") from exc


async def close_expired_periods_in_db(now: datetime | None = None) -> list[str]:
    """Cierra períodos vencidos bloqueando primero la cuenta."""
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
            await _lock_account(session, user_id)
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
            closed.append(str(period.id))
        await session.flush()
    return closed


async def usage_for_user(user_id: str) -> list[dict]:
    from central_api.db import nueva_sesion

    uid = _uuid(user_id)
    async with nueva_sesion() as session:
        rows = (
            await session.execute(
                select(BillingUsage)
                .where(BillingUsage.user_id == uid)
                .order_by(BillingUsage.created_at.desc())
                .limit(200)
            )
        ).scalars().all()
        return [
            {
                "job_id": str(row.job_id),
                "tipo_cuenta": row.account_type,
                "estado": row.status,
                "unidades": row.units,
                "creado_en": row.created_at.isoformat().replace("+00:00", "Z"),
            }
            for row in rows
        ]


def _as_usage(row: BillingUsage) -> UsageEntry:
    return UsageEntry(
        id=str(row.id),
        user_id=str(row.user_id),
        job_id=str(row.job_id),
        estado=row.status,
        period_id=str(row.account_period_id) if row.account_period_id else None,
        motivo=row.reason,
        unidades=row.units,
    )


__all__ = [
    "assign_account_in_db",
    "close_expired_periods_in_db",
    "reserve_in_session",
    "settle_in_session",
    "usage_for_user",
]
