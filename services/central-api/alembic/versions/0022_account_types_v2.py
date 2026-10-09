"""0022 tres tipos de cuenta: PRUEBA, TESTING y NORMAL.

Se retiran los pagos automáticos (MercadoPago), los créditos y los tiers.
Las cuentas pasan a uno de tres tipos administrados manualmente:

* ``PRUEBA``: tope fijo de 100 requests, no renovable, sin vencimiento.
* ``TESTING``: requests ilimitadas (se sigue contando el uso).
* ``NORMAL``: cupo mensual renovable (``max_requests``) con períodos de 30 días.

Equivalencias de los datos existentes:

* ``GRATUITA`` y ``CREDITOS`` -> ``PRUEBA``. El consumo se conserva como
  ``min(100, confirmados)`` para que quien ya agotó sus 100 siga agotado.
* ``TIER`` -> ``NORMAL`` con ``max_requests`` igual a las unidades del plan.
* ``LIBRE`` -> ``NORMAL`` conservando el tope manual como cupo mensual.

Las tablas de pagos, suscripciones y ledgers no se eliminan: conservan el
historial. El saldo de créditos deja de tener efecto.
"""

from __future__ import annotations

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_TABLES = ("billing_accounts", "billing_account_periods", "billing_usage")

_DROP_CHECKS = """
DO $$
DECLARE r record;
BEGIN
  FOR r IN
    SELECT conrelid::regclass AS t, conname
    FROM pg_constraint
    WHERE contype = 'c'
      AND conrelid IN ('billing_accounts'::regclass,
                       'billing_account_periods'::regclass,
                       'billing_usage'::regclass)
  LOOP
    EXECUTE format('ALTER TABLE %s DROP CONSTRAINT %I', r.t, r.conname);
  END LOOP;
END $$;
"""


def _add(table: str, name: str, expr: str) -> None:
    op.execute(f"ALTER TABLE {table} ADD CONSTRAINT {name} CHECK ({expr})")


def upgrade() -> None:
    op.execute(_DROP_CHECKS)
    op.execute("ALTER TABLE billing_account_periods ALTER COLUMN plan_code DROP NOT NULL")

    # --- períodos: los gratuitos se cierran; los tier pasan a NORMAL -------
    op.execute(
        "UPDATE billing_account_periods SET status = 'CERRADO', "
        "close_reason = 'migracion 0022: cuenta de prueba sin períodos' "
        "WHERE account_type = 'GRATUITA' AND status = 'ABIERTO'"
    )
    op.execute(
        "UPDATE billing_account_periods SET account_type = 'PRUEBA' "
        "WHERE account_type = 'GRATUITA'"
    )
    op.execute(
        "UPDATE billing_account_periods SET account_type = 'NORMAL' "
        "WHERE account_type = 'TIER'"
    )

    # --- historial de uso --------------------------------------------------
    op.execute(
        "UPDATE billing_usage SET account_type = CASE account_type "
        "WHEN 'TIER' THEN 'NORMAL' WHEN 'LIBRE' THEN 'NORMAL' ELSE 'PRUEBA' END"
    )

    # --- cuentas: PRUEBA (desde GRATUITA y CREDITOS) -----------------------
    op.execute(
        "UPDATE billing_accounts a SET "
        "requests_consumed = LEAST(100, GREATEST(a.requests_consumed, COALESCE(("
        "  SELECT count(*) FROM billing_usage u "
        "  WHERE u.user_id = a.user_id AND u.status = 'CONFIRMADO' "
        "    AND u.billing_source = 'CUOTA'), 0))) "
        "WHERE a.account_type IN ('GRATUITA', 'CREDITOS')"
    )
    op.execute(
        "UPDATE billing_accounts a SET "
        "requests_reserved = LEAST(100 - a.requests_consumed, COALESCE(("
        "  SELECT count(*) FROM billing_usage u "
        "  WHERE u.user_id = a.user_id AND u.status = 'RESERVADO' "
        "    AND u.billing_source = 'CUOTA'), 0)) "
        "WHERE a.account_type IN ('GRATUITA', 'CREDITOS')"
    )
    op.execute(
        "UPDATE billing_accounts SET account_type = 'PRUEBA', plan_code = NULL, "
        "max_requests = 100, expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
        "WHERE account_type IN ('GRATUITA', 'CREDITOS')"
    )

    # --- cuentas: NORMAL (desde TIER y LIBRE) ------------------------------
    op.execute(
        "UPDATE billing_accounts a SET account_type = 'NORMAL', "
        "max_requests = COALESCE((SELECT p.included_units FROM plans p "
        "  WHERE p.code = a.plan_code), 100), "
        "plan_code = NULL, expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
        "WHERE a.account_type = 'TIER'"
    )
    op.execute(
        "UPDATE billing_accounts SET account_type = 'NORMAL', plan_code = NULL, "
        "expires_at = NULL, updated_at = CURRENT_TIMESTAMP "
        "WHERE account_type = 'LIBRE'"
    )

    # --- restricciones nuevas ----------------------------------------------
    t = "billing_accounts"
    _add(t, "ck_billing_accounts_billing_accounts_type",
         "account_type IN ('PRUEBA','TESTING','NORMAL')")
    _add(t, "ck_billing_accounts_billing_accounts_max_requests",
         "max_requests IS NULL OR max_requests BETWEEN 1 AND 10000000")
    _add(t, "ck_billing_accounts_billing_accounts_reserved", "requests_reserved >= 0")
    _add(t, "ck_billing_accounts_billing_accounts_consumed", "requests_consumed >= 0")
    _add(
        t,
        "ck_billing_accounts_billing_accounts_configuration",
        "plan_code IS NULL AND expires_at IS NULL AND ("
        "(account_type = 'PRUEBA' AND max_requests = 100)"
        " OR (account_type = 'TESTING' AND max_requests IS NULL)"
        " OR (account_type = 'NORMAL' AND max_requests IS NOT NULL))",
    )

    p = "billing_account_periods"
    _add(p, "ck_billing_account_periods_billing_account_periods_type",
         "account_type IN ('NORMAL','PRUEBA')")
    _add(p, "ck_billing_account_periods_billing_account_periods_status",
         "status IN ('ABIERTO','CERRADO')")
    _add(p, "ck_billing_account_periods_billing_account_periods_range", "ends_at > starts_at")
    _add(p, "ck_billing_account_periods_billing_account_periods_included",
         "included_requests >= 0")
    _add(p, "ck_billing_account_periods_billing_account_periods_reserved",
         "requests_reserved >= 0")
    _add(p, "ck_billing_account_periods_billing_account_periods_consumed",
         "requests_consumed >= 0")

    u = "billing_usage"
    _add(u, "ck_billing_usage_billing_usage_account_type",
         "account_type IN ('PRUEBA','TESTING','NORMAL')")
    _add(u, "ck_billing_usage_billing_usage_source", "billing_source IN ('CUOTA','CREDITOS')")
    _add(u, "ck_billing_usage_billing_usage_status",
         "status IN ('RESERVADO','CONFIRMADO','LIBERADO','REEMBOLSADO')")
    _add(u, "ck_billing_usage_billing_usage_units", "units > 0")
    _add(u, "ck_billing_usage_billing_usage_configuration",
         "billing_source = 'CUOTA' OR account_period_id IS NULL")


def downgrade() -> None:
    op.execute(_DROP_CHECKS)

    op.execute(
        "UPDATE billing_account_periods SET account_type = 'GRATUITA' "
        "WHERE account_type = 'PRUEBA'"
    )
    op.execute(
        "UPDATE billing_account_periods SET account_type = 'TIER' "
        "WHERE account_type = 'NORMAL'"
    )
    op.execute(
        "UPDATE billing_account_periods SET plan_code = 'tier-100' "
        "WHERE plan_code IS NULL"
    )
    op.execute("ALTER TABLE billing_account_periods ALTER COLUMN plan_code SET NOT NULL")

    # La restricción vieja exige TIER/GRATUITA con período y LIBRE/CREDITOS sin
    # él, y CREDITOS solo con origen CREDITOS. Se mapea según esa combinación.
    op.execute(
        "UPDATE billing_usage SET account_type = CASE "
        "WHEN account_period_id IS NOT NULL AND account_type = 'NORMAL' THEN 'TIER' "
        "WHEN account_period_id IS NOT NULL THEN 'GRATUITA' "
        "WHEN billing_source = 'CREDITOS' THEN 'CREDITOS' "
        "ELSE 'LIBRE' END"
    )
    op.execute(
        "UPDATE billing_accounts SET account_type = 'CREDITOS', max_requests = NULL, "
        "plan_code = NULL, expires_at = NULL WHERE account_type = 'PRUEBA'"
    )
    op.execute(
        "UPDATE billing_accounts SET account_type = 'LIBRE', "
        "max_requests = COALESCE(max_requests, 10000000), plan_code = NULL, "
        "expires_at = NULL WHERE account_type IN ('NORMAL', 'TESTING')"
    )

    t = "billing_accounts"
    _add(t, "ck_billing_accounts_billing_accounts_type",
         "account_type IN ('CREDITOS','TIER','GRATUITA','LIBRE')")
    _add(t, "ck_billing_accounts_billing_accounts_max_requests",
         "max_requests IS NULL OR max_requests BETWEEN 1 AND 10000000")
    _add(t, "ck_billing_accounts_billing_accounts_reserved", "requests_reserved >= 0")
    _add(t, "ck_billing_accounts_billing_accounts_consumed", "requests_consumed >= 0")
    _add(
        t,
        "ck_billing_accounts_billing_accounts_configuration",
        "(account_type = 'TIER' AND plan_code LIKE 'tier-%' AND max_requests IS NULL AND expires_at IS NULL)"
        " OR (account_type = 'GRATUITA' AND plan_code = 'gratuita' AND max_requests = 100 AND expires_at IS NOT NULL)"
        " OR (account_type = 'LIBRE' AND plan_code IS NULL AND max_requests IS NOT NULL AND expires_at IS NULL)"
        " OR (account_type = 'CREDITOS' AND plan_code IS NULL AND max_requests IS NULL AND expires_at IS NULL)",
    )

    p = "billing_account_periods"
    _add(p, "ck_billing_account_periods_billing_account_periods_type",
         "account_type IN ('TIER','GRATUITA')")
    _add(p, "ck_billing_account_periods_billing_account_periods_status",
         "status IN ('ABIERTO','CERRADO')")
    _add(p, "ck_billing_account_periods_billing_account_periods_range", "ends_at > starts_at")
    _add(p, "ck_billing_account_periods_billing_account_periods_included",
         "included_requests >= 0")
    _add(p, "ck_billing_account_periods_billing_account_periods_reserved",
         "requests_reserved >= 0")
    _add(p, "ck_billing_account_periods_billing_account_periods_consumed",
         "requests_consumed >= 0")

    u = "billing_usage"
    _add(u, "ck_billing_usage_billing_usage_account_type",
         "account_type IN ('CREDITOS','TIER','GRATUITA','LIBRE')")
    _add(u, "ck_billing_usage_billing_usage_source", "billing_source IN ('CUOTA','CREDITOS')")
    _add(u, "ck_billing_usage_billing_usage_status",
         "status IN ('RESERVADO','CONFIRMADO','LIBERADO','REEMBOLSADO')")
    _add(u, "ck_billing_usage_billing_usage_units", "units > 0")
    _add(
        u,
        "ck_billing_usage_billing_usage_configuration",
        "(account_type IN ('TIER','GRATUITA') AND billing_source = 'CUOTA' AND account_period_id IS NOT NULL)"
        " OR (account_type = 'TIER' AND billing_source = 'CREDITOS' AND account_period_id IS NULL)"
        " OR (account_type = 'LIBRE' AND billing_source = 'CUOTA' AND account_period_id IS NULL)"
        " OR (account_type = 'CREDITOS' AND billing_source = 'CREDITOS' AND account_period_id IS NULL)",
    )
