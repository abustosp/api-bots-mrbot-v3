"""Facturación manual: tipos de cuenta (PRUEBA, TESTING, NORMAL), períodos y uso por job."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from central_api.models.base import Base, new_uuid7


class BillingAccount(Base):
    """Tipo de cuenta y contadores de un usuario (administrado manualmente).

    ``PRUEBA``: 100 requests totales, no renovables. ``TESTING``: sin tope.
    ``NORMAL``: ``max_requests`` por período mensual renovable; sus contadores
    viven en ``billing_account_periods``.
    """

    __tablename__ = "billing_accounts"

    user_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    account_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    max_requests: Mapped[int | None] = mapped_column(sa.Integer)
    starts_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True), nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    expires_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))
    requests_reserved: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    requests_consumed: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    generation_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, server_default=sa.text("gen_random_uuid()")
    )
    updated_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True), nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.CheckConstraint(
            "account_type IN ('PRUEBA','TESTING','NORMAL')",
            name="billing_accounts_type",
        ),
        sa.CheckConstraint(
            "max_requests IS NULL OR max_requests BETWEEN 1 AND 10000000",
            name="billing_accounts_max_requests",
        ),
        sa.CheckConstraint("requests_reserved >= 0", name="billing_accounts_reserved"),
        sa.CheckConstraint("requests_consumed >= 0", name="billing_accounts_consumed"),
        sa.CheckConstraint(
            "expires_at IS NULL AND ("
            "(account_type = 'PRUEBA' AND max_requests = 100)"
            " OR (account_type = 'TESTING' AND max_requests IS NULL)"
            " OR (account_type = 'NORMAL' AND max_requests IS NOT NULL))",
            name="billing_accounts_configuration",
        ),
        sa.Index("ix_billing_accounts_type", "account_type"),
    )


class BillingAccountPeriod(Base):
    """Periodo de cuota vigente o historico de un modo de cuenta manual."""

    __tablename__ = "billing_account_periods"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    user_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("billing_accounts.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    starts_at: Mapped[object] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    ends_at: Mapped[object] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'ABIERTO'")
    )
    included_requests: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    requests_reserved: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    requests_consumed: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    close_reason: Mapped[str | None] = mapped_column(sa.String(160))
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True), nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.CheckConstraint(
            "account_type IN ('NORMAL','PRUEBA')", name="billing_account_periods_type"
        ),
        sa.CheckConstraint(
            "status IN ('ABIERTO','CERRADO')", name="billing_account_periods_status"
        ),
        sa.CheckConstraint("ends_at > starts_at", name="billing_account_periods_range"),
        sa.CheckConstraint("included_requests >= 0", name="billing_account_periods_included"),
        sa.CheckConstraint("requests_reserved >= 0", name="billing_account_periods_reserved"),
        sa.CheckConstraint("requests_consumed >= 0", name="billing_account_periods_consumed"),
        sa.UniqueConstraint("user_id", "starts_at", name="uq_billing_account_periods_start"),
        sa.Index(
            "uq_billing_account_periods_open_user",
            "user_id",
            unique=True,
            postgresql_where=sa.text("status = 'ABIERTO'"),
        ),
    )


class BillingUsage(Base):
    """Estado persistido e idempotente de una reserva de facturacion por job."""

    __tablename__ = "billing_usage"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    user_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("billing_accounts.user_id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="RESTRICT"),
        nullable=False,
    )
    account_period_id: Mapped[object | None] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("billing_account_periods.id", ondelete="RESTRICT"),
    )
    account_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    generation_id: Mapped[object] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    billing_source: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'RESERVADO'")
    )
    units: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    reason: Mapped[str | None] = mapped_column(sa.String(160))
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True), nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    updated_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True), nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.CheckConstraint(
            "account_type IN ('PRUEBA','TESTING','NORMAL')",
            name="billing_usage_account_type",
        ),
        sa.CheckConstraint(
            "billing_source IN ('CUOTA','CREDITOS')", name="billing_usage_source"
        ),
        sa.CheckConstraint(
            "status IN ('RESERVADO','CONFIRMADO','LIBERADO','REEMBOLSADO')",
            name="billing_usage_status",
        ),
        sa.CheckConstraint("units > 0", name="billing_usage_units"),
        sa.CheckConstraint(
            "billing_source = 'CUOTA' OR account_period_id IS NULL",
            name="billing_usage_configuration",
        ),
        sa.UniqueConstraint("job_id", name="uq_billing_usage_job"),
        sa.Index("ix_billing_usage_user_created", "user_id", "created_at"),
    )
