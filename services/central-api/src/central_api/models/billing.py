"""Facturacion: planes, suscripciones, periodos y ledgers (plan 01 §5.5).

Los ledgers son append-only: rectificar es agregar un hecho compensatorio
ligado al original. Autenticar nunca abre ni resetea periodos; la cuota es
una suma de ``usage_ledger`` y el saldo una suma de ``credit_ledger``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from central_api.models.base import Base, new_uuid7


class Plan(Base):
    """Version comercial contratable (cuota, duracion y precio congelados)."""

    __tablename__ = "plans"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    code: Mapped[str] = mapped_column(sa.String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    included_units: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    period_days: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("30")
    )
    price_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        sa.CHAR(3), nullable=False, server_default=sa.text("'ARS'")
    )
    active: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true()
    )
    # ``metadata`` es nombre reservado del API declarativa: el atributo se
    # llama ``meta`` pero la columna sigue siendo ``metadata`` (DDL intacto).
    meta: Mapped[object] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )

    __table_args__ = (
        sa.CheckConstraint("included_units >= 0", name="plans_units"),
        sa.CheckConstraint("period_days BETWEEN 1 AND 366", name="plans_period"),
        sa.CheckConstraint("price_cents >= 0", name="plans_price"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="plans_currency"),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'", name="plans_metadata"
        ),
        sa.Index("ix_plans_active", "code", postgresql_where=sa.text("active")),
    )


class Subscription(Base):
    """Contrato del titular; una UQ parcial limita activa/pendiente/pausada."""

    __tablename__ = "subscriptions"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    user_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    plan_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    provider: Mapped[str | None] = mapped_column(sa.String(32))
    provider_subscription_id: Mapped[str | None] = mapped_column(sa.String(160))
    current_period_start: Mapped[object | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    current_period_end: Mapped[object | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    ended_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('PENDIENTE','ACTIVA','PAUSADA','CANCELADA','VENCIDA')",
            name="subscriptions_status",
        ),
        sa.CheckConstraint(
            "provider IS NULL OR provider IN ('MERCADOPAGO')",
            name="subscriptions_provider",
        ),
        sa.CheckConstraint(
            "current_period_end IS NULL OR current_period_start IS NULL"
            " OR current_period_end > current_period_start",
            name="subscriptions_period",
        ),
        sa.Index(
            "uq_subscriptions_active_user",
            "user_id",
            unique=True,
            postgresql_where=sa.text("status IN ('PENDIENTE','ACTIVA','PAUSADA')"),
        ),
        sa.Index(
            "uq_subscriptions_provider_external",
            "provider",
            "provider_subscription_id",
            unique=True,
            postgresql_where=sa.text("provider_subscription_id IS NOT NULL"),
        ),
        sa.Index("ix_subscriptions_ended", "ended_at"),
    )


class SubscriptionPeriod(Base):
    """Periodo facturable con limites congelados; solo ``ABIERTO`` admite uso."""

    __tablename__ = "subscription_periods"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    subscription_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("subscriptions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    starts_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    ends_at: Mapped[object] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'ABIERTO'")
    )
    included_units: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    credit_unit_price_cents: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    opened_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    closed_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.UniqueConstraint(
            "subscription_id", "starts_at", name="uq_subscription_periods_start"
        ),
        sa.CheckConstraint("ends_at > starts_at", name="subscription_periods_range"),
        sa.CheckConstraint(
            "status IN ('ABIERTO','CERRADO','ANULADO')",
            name="subscription_periods_status",
        ),
        sa.CheckConstraint("included_units >= 0", name="subscription_periods_units"),
        sa.CheckConstraint(
            "credit_unit_price_cents >= 0", name="subscription_periods_price"
        ),
        sa.Index(
            "ix_subscription_periods_open",
            "starts_at",
            "ends_at",
            postgresql_where=sa.text("status = 'ABIERTO'"),
        ),
    )


class Payment(Base):
    """Orden de pago interna derivada de eventos de proveedor verificados."""

    __tablename__ = "payments"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    user_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    subscription_id: Mapped[object | None] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("subscriptions.id", ondelete="RESTRICT"),
    )
    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    provider_payment_id: Mapped[str | None] = mapped_column(sa.String(160))
    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'PENDIENTE'")
    )
    amount_cents: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    currency: Mapped[str] = mapped_column(
        sa.CHAR(3), nullable=False, server_default=sa.text("'ARS'")
    )
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    approved_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(
            "provider IN ('MERCADOPAGO')", name="payments_provider"
        ),
        sa.CheckConstraint(
            "kind IN ('SUSCRIPCION','CREDITOS','REEMBOLSO')", name="payments_kind"
        ),
        sa.CheckConstraint(
            "status IN ('PENDIENTE','APROBADO','RECHAZADO','CANCELADO','REEMBOLSADO')",
            name="payments_status",
        ),
        sa.CheckConstraint("amount_cents >= 0", name="payments_amount"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="payments_currency"),
        sa.Index(
            "uq_payments_provider_payment",
            "provider",
            "provider_payment_id",
            unique=True,
            postgresql_where=sa.text("provider_payment_id IS NOT NULL"),
        ),
        sa.Index("ix_payments_user_created", "user_id", "created_at"),
    )


class UsageLedger(Base):
    """Hecho inmutable de cuota ligado a un job y a un periodo."""

    __tablename__ = "usage_ledger"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    period_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("subscription_periods.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    units: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(sa.String(160))
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.UniqueConstraint("job_id", "event_type", name="uq_usage_ledger_job_event"),
        sa.CheckConstraint(
            "event_type IN ('RESERVA','CONFIRMACION','REEMBOLSO')",
            name="usage_ledger_type",
        ),
        sa.CheckConstraint(
            "(event_type = 'RESERVA' AND units > 0)"
            " OR (event_type = 'CONFIRMACION' AND units = 0)"
            " OR (event_type = 'REEMBOLSO' AND units < 0)",
            name="usage_ledger_units",
        ),
        sa.Index("ix_usage_ledger_period_created", "period_id", "created_at"),
    )


class CreditLedger(Base):
    """Movimiento inmutable de creditos; el saldo es la suma por usuario."""

    __tablename__ = "credit_ledger"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    user_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    payment_id: Mapped[object | None] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("payments.id", ondelete="RESTRICT"),
    )
    job_id: Mapped[object | None] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
    )
    entry_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    units: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    reason: Mapped[str | None] = mapped_column(sa.String(160))
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "idempotency_key", name="uq_credit_ledger_idempotency"
        ),
        sa.CheckConstraint(
            "entry_type IN ('COMPRA','DEBITO','REEMBOLSO','AJUSTE')",
            name="credit_ledger_type",
        ),
        sa.CheckConstraint("units <> 0", name="credit_ledger_units"),
        sa.Index("ix_credit_ledger_user_created", "user_id", "created_at"),
    )


class PaymentEvent(Base):
    """Webhook minimizado y firmado; idempotente por proveedor y evento."""

    __tablename__ = "payment_events"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    payment_id: Mapped[object | None] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("payments.id", ondelete="RESTRICT"),
    )
    provider: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    provider_event_id: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    payload: Mapped[object] = mapped_column(JSONB, nullable=False)
    received_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "provider", "provider_event_id", name="uq_payment_events_provider_event"
        ),
        sa.CheckConstraint(
            "provider IN ('MERCADOPAGO')", name="payment_events_provider"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="payment_events_payload"
        ),
        sa.Index("ix_payment_events_received", "received_at"),
    )


__all__ = [
    "Plan",
    "Subscription",
    "SubscriptionPeriod",
    "Payment",
    "UsageLedger",
    "CreditLedger",
    "PaymentEvent",
]
