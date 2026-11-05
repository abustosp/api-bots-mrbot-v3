"""0031 elimina las tablas legacy de pagos, suscripciones, ledgers y planes.

Quedan solo los tipos de cuenta manuales (PRUEBA, TESTING, NORMAL) en
``billing_accounts``, ``billing_account_periods`` y ``billing_usage``.
Se pierde el historial de esas tablas: no hay downgrade.
"""

from __future__ import annotations

from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_LEGACY = (
    "payment_events", "credit_ledger", "usage_ledger", "payments",
    "subscription_periods", "subscriptions", "plans",
)


def upgrade() -> None:
    op.execute(
        "ALTER TABLE billing_accounts "
        "DROP CONSTRAINT IF EXISTS ck_billing_accounts_billing_accounts_configuration"
    )
    op.execute("ALTER TABLE billing_accounts DROP COLUMN IF EXISTS plan_code")
    op.execute("ALTER TABLE billing_account_periods DROP COLUMN IF EXISTS plan_code")
    op.execute(
        "ALTER TABLE billing_accounts ADD CONSTRAINT "
        "ck_billing_accounts_billing_accounts_configuration CHECK ("
        "expires_at IS NULL AND ("
        "(account_type = 'PRUEBA' AND max_requests = 100)"
        " OR (account_type = 'TESTING' AND max_requests IS NULL)"
        " OR (account_type = 'NORMAL' AND max_requests IS NOT NULL)))"
    )
    for table in _LEGACY:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
    raise NotImplementedError("0031 elimina datos legacy; restaurar desde backup")
