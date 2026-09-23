"""0007 ledgers: payments, usage_ledger, credit_ledger y payment_events."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True)),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_payment_id", sa.String(160)),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'PENDIENTE'"), nullable=False),
        sa.Column("amount_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(3), server_default=sa.text("'ARS'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("provider IN ('MERCADOPAGO')", name="ck_payments_payments_provider"),
        sa.CheckConstraint("kind IN ('SUSCRIPCION','CREDITOS','REEMBOLSO')", name="ck_payments_payments_kind"),
        sa.CheckConstraint(
            "status IN ('PENDIENTE','APROBADO','RECHAZADO','CANCELADO','REEMBOLSADO')",
            name="ck_payments_payments_status",
        ),
        sa.CheckConstraint("amount_cents >= 0", name="ck_payments_payments_amount"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_payments_payments_currency"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_payments_user_id_users"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="RESTRICT", name="fk_payments_subscription_id_subscriptions"),
        sa.PrimaryKeyConstraint("id", name="pk_payments"),
    )
    op.create_index(
        "uq_payments_provider_payment", "payments", ["provider", "provider_payment_id"],
        unique=True, postgresql_where=sa.text("provider_payment_id IS NOT NULL"),
    )
    op.create_index("ix_payments_user_created", "payments", ["user_id", "created_at"])
    op.create_table(
        "usage_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("period_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(16), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(160)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint("event_type IN ('RESERVA','CONFIRMACION','REEMBOLSO')", name="ck_usage_ledger_usage_ledger_type"),
        sa.CheckConstraint(
            "(event_type = 'RESERVA' AND units > 0) OR (event_type = 'CONFIRMACION' AND units = 0)"
            " OR (event_type = 'REEMBOLSO' AND units < 0)",
            name="ck_usage_ledger_usage_ledger_units",
        ),
        sa.ForeignKeyConstraint(["period_id"], ["subscription_periods.id"], ondelete="RESTRICT", name="fk_usage_ledger_period_id_subscription_periods"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT", name="fk_usage_ledger_job_id_jobs"),
        sa.PrimaryKeyConstraint("id", name="pk_usage_ledger"),
        sa.UniqueConstraint("job_id", "event_type", name="uq_usage_ledger_job_event"),
    )
    op.create_index("ix_usage_ledger_period_created", "usage_ledger", ["period_id", "created_at"])
    op.create_table(
        "credit_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True)),
        sa.Column("job_id", postgresql.UUID(as_uuid=True)),
        sa.Column("entry_type", sa.String(16), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("reason", sa.String(160)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint("entry_type IN ('COMPRA','DEBITO','REEMBOLSO','AJUSTE')", name="ck_credit_ledger_credit_ledger_type"),
        sa.CheckConstraint("units <> 0", name="ck_credit_ledger_credit_ledger_units"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_credit_ledger_user_id_users"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT", name="fk_credit_ledger_payment_id_payments"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="RESTRICT", name="fk_credit_ledger_job_id_jobs"),
        sa.PrimaryKeyConstraint("id", name="pk_credit_ledger"),
        sa.UniqueConstraint("idempotency_key", name="uq_credit_ledger_idempotency"),
    )
    op.create_index("ix_credit_ledger_user_created", "credit_ledger", ["user_id", "created_at"])
    op.create_table(
        "payment_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("payment_id", postgresql.UUID(as_uuid=True)),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("provider_event_id", sa.String(160), nullable=False),
        sa.Column("event_type", sa.String(80), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint("provider IN ('MERCADOPAGO')", name="ck_payment_events_payment_events_provider"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_payment_events_payment_events_payload"),
        sa.ForeignKeyConstraint(["payment_id"], ["payments.id"], ondelete="RESTRICT", name="fk_payment_events_payment_id_payments"),
        sa.PrimaryKeyConstraint("id", name="pk_payment_events"),
        sa.UniqueConstraint("provider", "provider_event_id", name="uq_payment_events_provider_event"),
    )
    op.create_index("ix_payment_events_received", "payment_events", ["received_at"])


def downgrade() -> None:
    op.drop_table("payment_events")
    op.drop_table("credit_ledger")
    op.drop_table("usage_ledger")
    op.drop_table("payments")
