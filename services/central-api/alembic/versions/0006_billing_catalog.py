"""0006 catalogo de facturacion: planes, suscripciones y periodos."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("included_units", sa.Integer(), nullable=False),
        sa.Column("period_days", sa.SmallInteger(), server_default=sa.text("30"), nullable=False),
        sa.Column("price_cents", sa.Integer(), nullable=False),
        sa.Column("currency", sa.CHAR(3), server_default=sa.text("'ARS'"), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint("included_units >= 0", name="ck_plans_plans_units"),
        sa.CheckConstraint("period_days BETWEEN 1 AND 366", name="ck_plans_plans_period"),
        sa.CheckConstraint("price_cents >= 0", name="ck_plans_plans_price"),
        sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name="ck_plans_plans_currency"),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_plans_plans_metadata"),
        sa.PrimaryKeyConstraint("id", name="pk_plans"),
        sa.UniqueConstraint("code", name="uq_plans_code"),
    )
    op.create_index("ix_plans_active", "plans", ["code"], postgresql_where=sa.text("active"))
    op.create_table(
        "subscriptions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("provider", sa.String(32)),
        sa.Column("provider_subscription_id", sa.String(160)),
        sa.Column("current_period_start", sa.DateTime(timezone=True)),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column("cancel_at_period_end", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('PENDIENTE','ACTIVA','PAUSADA','CANCELADA','VENCIDA')",
            name="ck_subscriptions_subscriptions_status",
        ),
        sa.CheckConstraint("provider IS NULL OR provider IN ('MERCADOPAGO')", name="ck_subscriptions_subscriptions_provider"),
        sa.CheckConstraint(
            "current_period_end IS NULL OR current_period_start IS NULL OR current_period_end > current_period_start",
            name="ck_subscriptions_subscriptions_period",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_subscriptions_user_id_users"),
        sa.ForeignKeyConstraint(["plan_id"], ["plans.id"], ondelete="RESTRICT", name="fk_subscriptions_plan_id_plans"),
        sa.PrimaryKeyConstraint("id", name="pk_subscriptions"),
    )
    op.create_index(
        "uq_subscriptions_active_user", "subscriptions", ["user_id"], unique=True,
        postgresql_where=sa.text("status IN ('PENDIENTE','ACTIVA','PAUSADA')"),
    )
    op.create_index(
        "uq_subscriptions_provider_external", "subscriptions",
        ["provider", "provider_subscription_id"], unique=True,
        postgresql_where=sa.text("provider_subscription_id IS NOT NULL"),
    )
    op.create_index("ix_subscriptions_ended", "subscriptions", ["ended_at"])
    op.create_table(
        "subscription_periods",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("subscription_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'ABIERTO'"), nullable=False),
        sa.Column("included_units", sa.Integer(), nullable=False),
        sa.Column("credit_unit_price_cents", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("ends_at > starts_at", name="ck_subscription_periods_subscription_periods_range"),
        sa.CheckConstraint("status IN ('ABIERTO','CERRADO','ANULADO')", name="ck_subscription_periods_subscription_periods_status"),
        sa.CheckConstraint("included_units >= 0", name="ck_subscription_periods_subscription_periods_units"),
        sa.CheckConstraint("credit_unit_price_cents >= 0", name="ck_subscription_periods_subscription_periods_price"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="RESTRICT", name="fk_subscription_periods_subscription_id_subscriptions"),
        sa.PrimaryKeyConstraint("id", name="pk_subscription_periods"),
        sa.UniqueConstraint("subscription_id", "starts_at", name="uq_subscription_periods_start"),
    )
    op.create_index(
        "ix_subscription_periods_open", "subscription_periods", ["starts_at", "ends_at"],
        postgresql_where=sa.text("status = 'ABIERTO'"),
    )


def downgrade() -> None:
    op.drop_table("subscription_periods")
    op.drop_table("subscriptions")
    op.drop_table("plans")
