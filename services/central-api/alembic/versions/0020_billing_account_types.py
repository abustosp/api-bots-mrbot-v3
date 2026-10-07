"""0020 tipos de cuenta, entitlements y catálogo de requests."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_PLANS = (
    ("gratuita", "Gratuita", 100, 0, 0),
    ("tier-100", "100 requests", 100, 1_800_000, 18_000),
    ("tier-500", "500 requests", 500, 8_000_000, 16_000),
    ("tier-1000", "1.000 requests", 1000, 14_000_000, 14_000),
    ("tier-2500", "2.500 requests", 2500, 30_000_000, 12_000),
    ("tier-5000", "5.000 requests", 5000, 50_000_000, 10_000),
    ("tier-10000", "10.000 requests", 10000, 80_000_000, 8_000),
    # El código legacy se conserva para suscripciones e integraciones previas.
    ("free", "Gratuita", 100, 0, 0),
)


def upgrade() -> None:
    bind = op.get_bind()
    for index, (code, name, units, price, unit_price) in enumerate(_PLANS, start=1):
        bind.execute(
            sa.text(
                "INSERT INTO plans (id, code, name, included_units, period_days, "
                "price_cents, currency, active, metadata) "
                "VALUES (:id, :code, :name, :units, 30, :price, 'ARS', true, "
                "CAST(:metadata AS jsonb)) "
                "ON CONFLICT (code) DO UPDATE SET name = EXCLUDED.name, "
                "included_units = EXCLUDED.included_units, "
                "period_days = EXCLUDED.period_days, price_cents = EXCLUDED.price_cents, "
                "currency = EXCLUDED.currency, active = true, metadata = EXCLUDED.metadata"
            ),
            {
                "id": f"02000000-0000-7000-8000-{index:012d}",
                "code": code,
                "name": name,
                "units": units,
                "price": price,
                "metadata": (
                    '{"account_type":"GRATUITA","unit_price_cents":0}'
                    if code in {"free", "gratuita"}
                    else f'{{"account_type":"TIER","unit_price_cents":{unit_price}}}'
                ),
            },
        )

    op.create_table(
        "billing_accounts",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_type", sa.String(16), nullable=False),
        sa.Column("plan_code", sa.String(64), nullable=True),
        sa.Column("max_requests", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requests_reserved", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("requests_consumed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint(
            "account_type IN ('CREDITOS','TIER','GRATUITA','LIBRE')",
            name="ck_billing_accounts_billing_accounts_type",
        ),
        sa.CheckConstraint(
            "max_requests IS NULL OR max_requests BETWEEN 1 AND 10000000",
            name="ck_billing_accounts_billing_accounts_max_requests",
        ),
        sa.CheckConstraint("requests_reserved >= 0", name="ck_billing_accounts_billing_accounts_reserved"),
        sa.CheckConstraint("requests_consumed >= 0", name="ck_billing_accounts_billing_accounts_consumed"),
        sa.CheckConstraint(
            "(account_type = 'TIER' AND plan_code LIKE 'tier-%' AND max_requests IS NULL AND expires_at IS NULL)"
            " OR (account_type = 'GRATUITA' AND plan_code = 'gratuita' AND max_requests = 100 AND expires_at IS NOT NULL)"
            " OR (account_type = 'LIBRE' AND plan_code IS NULL AND max_requests IS NOT NULL AND expires_at IS NULL)"
            " OR (account_type = 'CREDITOS' AND plan_code IS NULL AND max_requests IS NULL AND expires_at IS NULL)",
            name="ck_billing_accounts_billing_accounts_configuration",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_billing_accounts_user_id_users"),
        sa.ForeignKeyConstraint(["plan_code"], ["plans.code"], ondelete="RESTRICT", name="fk_billing_accounts_plan_code_plans"),
        sa.PrimaryKeyConstraint("user_id", name="pk_billing_accounts"),
    )
    op.create_index("ix_billing_accounts_type", "billing_accounts", ["account_type"])
    op.execute(
        sa.text(
            "INSERT INTO billing_accounts (user_id, account_type) "
            "SELECT id, 'CREDITOS' FROM users ON CONFLICT (user_id) DO NOTHING"
        )
    )


def downgrade() -> None:
    op.drop_index("ix_billing_accounts_type", table_name="billing_accounts")
    op.drop_table("billing_accounts")
    # No se eliminan los tiers sembrados porque pueden estar referenciados por
    # suscripciones o períodos existentes. El plan legacy queda en la cuota nueva.
