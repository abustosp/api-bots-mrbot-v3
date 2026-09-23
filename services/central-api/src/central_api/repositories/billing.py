"""Repositorio de facturacion: reservas y saldos como sumas (plan 01 §§11.3-11.4).

La admision reserva en la misma transaccion que inserta el job: toma
``FOR UPDATE`` sobre el periodo, suma el ledger y solo inserta ``RESERVA``
si cabe el ``unit_cost``. El saldo de creditos es la suma del ledger; los
webhooks duplicados se absorben por unicidad antes de cualquier efecto.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from central_api.models.base import new_uuid7
from central_api.models.billing import CreditLedger, PaymentEvent, UsageLedger
from central_api.repositories.base import RepositoryError

QUOTA_SQL = text(
    """
SELECT sp.included_units AS included,
       COALESCE(SUM(ul.units), 0) AS consumed
FROM subscription_periods sp
LEFT JOIN usage_ledger ul ON ul.period_id = sp.id
WHERE sp.id = :period_id
GROUP BY sp.id, sp.included_units
FOR UPDATE OF sp
"""
)

BALANCE_SQL = text(
    """
SELECT COALESCE(SUM(cl.units), 0) AS balance
FROM credit_ledger cl
WHERE cl.user_id = :user_id
"""
)


class BillingRepository:
    """Hechos contables append-only con idempotencia por restriccion unica."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def reserve_quota(
        self, *, period_id: UUID, job_id: UUID, units: int
    ) -> UsageLedger:
        """Reserva cuota si cabe; en otro caso falla sin insertar nada."""
        row = (
            await self._session.execute(QUOTA_SQL, {"period_id": str(period_id)})
        ).mappings().first()
        if row is None:
            raise RepositoryError("periodo de suscripcion inexistente")
        if int(row["consumed"]) + units > int(row["included"]):
            raise RepositoryError("cuota del periodo insuficiente")
        entry = UsageLedger(
            id=new_uuid7(),
            period_id=period_id,
            job_id=job_id,
            event_type="RESERVA",
            units=units,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def confirm_or_refund(
        self, *, period_id: UUID, job_id: UUID, refund_units: int = 0,
        reason: str | None = None,
    ) -> UsageLedger:
        """Fija ``CONFIRMACION`` (0 uds.) o compensa con ``REEMBOLSO``."""
        if refund_units:
            entry = UsageLedger(
                id=new_uuid7(),
                period_id=period_id,
                job_id=job_id,
                event_type="REEMBOLSO",
                units=-abs(refund_units),
                reason=reason,
            )
        else:
            entry = UsageLedger(
                id=new_uuid7(),
                period_id=period_id,
                job_id=job_id,
                event_type="CONFIRMACION",
                units=0,
            )
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def credit_balance(self, user_id: UUID) -> int:
        """Saldo como suma del ledger (proyeccion, nunca fuente editable)."""
        row = (
            await self._session.execute(BALANCE_SQL, {"user_id": str(user_id)})
        ).first()
        return int(row[0]) if row else 0

    async def debit_credits(
        self,
        *,
        user_id: UUID,
        job_id: UUID,
        units: int,
        idempotency_key: str,
    ) -> CreditLedger:
        """Debita creditos con clave idempotente (sin doble cargo)."""
        entry = CreditLedger(
            id=new_uuid7(),
            user_id=user_id,
            job_id=job_id,
            entry_type="DEBITO",
            units=-abs(units),
            idempotency_key=idempotency_key,
        )
        self._session.add(entry)
        try:
            await self._session.flush()
        except Exception as exc:
            raise RepositoryError(
                f"debito rechazado (duplicado o saldo): {type(exc).__name__}"
            ) from exc
        return entry

    async def record_payment_event(
        self,
        *,
        provider: str,
        provider_event_id: str,
        event_type: str,
        payload: dict,
        payment_id: UUID | None = None,
    ) -> PaymentEvent:
        """Registra el webhook antes de cualquier efecto economico."""
        row = PaymentEvent(
            id=new_uuid7(),
            payment_id=payment_id,
            provider=provider,
            provider_event_id=provider_event_id,
            event_type=event_type,
            payload=dict(payload),
        )
        self._session.add(row)
        await self._session.flush()
        return row


__all__ = ["BALANCE_SQL", "QUOTA_SQL", "BillingRepository"]
