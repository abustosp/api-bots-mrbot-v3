"""Real PostgreSQL coverage for migration 0020 account backfill and rollback."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
import uuid

import pytest

DSN = os.environ.get("CENTRAL_API_TEST_DATABASE_URL", "").strip()
pytestmark = pytest.mark.skipif(not DSN, reason="CENTRAL_API_TEST_DATABASE_URL no configurada")

import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

SERVICE_DIR = Path(__file__).resolve().parents[1]


def _cfg() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(SERVICE_DIR / "alembic"))
    return cfg


def _sync_dsn() -> str:
    if DSN.startswith("postgresql://"):
        return "postgresql+psycopg://" + DSN[len("postgresql://"):]
    if DSN.startswith("postgres://"):
        return "postgresql+psycopg://" + DSN[len("postgres://"):]
    return DSN


def test_0020_backfills_enabled_free_users_and_roundtrips_on_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    engine = sa.create_engine(_sync_dsn())
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("DROP SCHEMA IF EXISTS legacy CASCADE"))
            conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(sa.text("CREATE SCHEMA public"))

        command.upgrade(_cfg(), "0019")
        enabled_id, disabled_id = uuid.uuid4(), uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO users (id, email, habilitado) VALUES "
                    "(:enabled, 'legacy-enabled@example.test', true), "
                    "(:disabled, 'legacy-disabled@example.test', false)"
                ),
                {"enabled": enabled_id, "disabled": disabled_id},
            )

        command.upgrade(_cfg(), "head")
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT user_id, account_type, plan_code, max_requests, expires_at "
                    "FROM billing_accounts WHERE user_id IN (:enabled, :disabled)"
                ),
                {"enabled": enabled_id, "disabled": disabled_id},
            ).mappings().all()
        accounts = {row["user_id"]: row for row in rows}
        assert accounts[enabled_id]["account_type"] == "GRATUITA"
        assert accounts[enabled_id]["plan_code"] == "gratuita"
        assert accounts[enabled_id]["max_requests"] == 100
        assert accounts[enabled_id]["expires_at"] is not None
        assert accounts[disabled_id]["account_type"] == "CREDITOS"
        assert accounts[disabled_id]["plan_code"] is None
        assert accounts[disabled_id]["max_requests"] is None
        assert accounts[disabled_id]["expires_at"] is None

        # Baja explícitamente a 0019 (0020 revertida), sin depender de cuál es
        # la cabeza actual: migraciones posteriores no deben romper este test.
        command.downgrade(_cfg(), "0019")
        with engine.connect() as conn:
            assert conn.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one() == "0019"
        command.upgrade(_cfg(), "head")
        with engine.connect() as conn:
            count = conn.execute(
                sa.text("SELECT count(*) FROM billing_accounts")
            ).scalar_one()
        assert count == 2

        import sys
        from datetime import datetime, timedelta, timezone

        src = SERVICE_DIR / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        from central_api import db
        from central_api.billing.postgres import (
            _lock_account,
            balance_for_user,
            close_expired_periods_in_db,
            credit_purchase_in_session,
            force_period_for_user,
            reserve_in_session,
            settle_in_session,
        )
        from central_api.billing.reservation import QuotaExhausted
        from central_api.models.billing import BillingAccount, BillingAccountPeriod
        from central_api.models.execution import Job
        from central_api.settings import get_settings
        from sqlalchemy import select

        get_settings.cache_clear()
        db._fabrica.cache_clear()
        db._motor.cache_clear()
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "INSERT INTO bots (id, code, display_name) "
                    "VALUES (:id, 'ccma', 'billing test bot')"
                ),
                {"id": uuid.uuid4()},
            )
            conn.execute(
                sa.text(
                    "INSERT INTO bot_operations "
                    "(id, bot_code, code, input_schema_version) "
                    "VALUES (:id, 'ccma', 'billing-check', '1')"
                ),
                {"id": uuid.uuid4()},
            )

        async def durable_billing_checks():
            async def reserve_new_job(uid, job_id):
                async with db.nueva_sesion() as session:
                    session.add(
                        Job(
                            id=job_id,
                            user_id=uid,
                            bot="ccma",
                            operation="billing-check",
                        )
                    )
                    await session.flush()
                    return await reserve_in_session(
                        session,
                        user_id=uid,
                        job_id=job_id,
                        bot="ccma",
                        operation="billing-check",
                    )

            for _ in range(100):
                jid = uuid.uuid4()
                usage = await reserve_new_job(enabled_id, jid)
                assert usage.fuente_cobro == "CUOTA"
                async with db.nueva_sesion() as session:
                    await settle_in_session(session, jid, status="CONFIRMADO")

            async with db.nueva_sesion() as session:
                account = await session.get(BillingAccount, enabled_id)
                assert account.account_type == "CREDITOS"
                assert account.requests_consumed == 100
                period = (
                    await session.execute(
                        select(BillingAccountPeriod).where(
                            BillingAccountPeriod.user_id == enabled_id
                        )
                    )
                ).scalar_one()
                assert period.requests_consumed == 100
                assert period.status == "CERRADO"
            with pytest.raises(QuotaExhausted):
                await reserve_new_job(enabled_id, uuid.uuid4())

            async with db.nueva_sesion() as session:
                account = await _lock_account(session, disabled_id)
                account.account_type = "LIBRE"
                account.plan_code = None
                account.max_requests = 2
                account.expires_at = None
                account.requests_reserved = 0
                account.requests_consumed = 0
                account.generation_id = uuid.uuid4()
                account.updated_at = datetime.now(timezone.utc)
            for _ in range(2):
                jid = uuid.uuid4()
                await reserve_new_job(disabled_id, jid)
                async with db.nueva_sesion() as session:
                    await settle_in_session(session, jid, status="CONFIRMADO")
            with pytest.raises(QuotaExhausted):
                await reserve_new_job(disabled_id, uuid.uuid4())
            async with db.nueva_sesion() as session:
                account = await session.get(BillingAccount, disabled_id)
                assert account.requests_consumed == 2

            async with db.nueva_sesion() as session:
                account = await _lock_account(session, disabled_id)
                account.account_type = "CREDITOS"
                account.max_requests = None
                account.plan_code = None
                account.expires_at = None
                account.requests_reserved = 0
                account.requests_consumed = 0
                account.generation_id = uuid.uuid4()
                await credit_purchase_in_session(
                    session,
                    user_id=disabled_id,
                    amount=2,
                    payment_id="pg-billing-purchase",
                    idempotency_key="pg-billing-purchase:credited",
                )
            async with db.nueva_sesion() as session:
                await credit_purchase_in_session(
                    session,
                    user_id=disabled_id,
                    amount=2,
                    payment_id="pg-billing-purchase",
                    idempotency_key="pg-billing-purchase:credited",
                )
            assert await balance_for_user(str(disabled_id)) == 2

            outcomes = await asyncio.gather(
                *(
                    reserve_new_job(disabled_id, uuid.uuid4())
                    for _ in range(3)
                ),
                return_exceptions=True,
            )
            successful = [item for item in outcomes if not isinstance(item, BaseException)]
            exhausted = [item for item in outcomes if isinstance(item, QuotaExhausted)]
            assert len(successful) == 2
            assert len(exhausted) == 1
            for usage in successful:
                async with db.nueva_sesion() as session:
                    await settle_in_session(
                        session, usage.job_id, status="LIBERADO", reason="test release"
                    )
            assert await balance_for_user(str(disabled_id)) == 2

            forced = await force_period_for_user(
                str(disabled_id), "free", "integration test grant"
            )
            assert forced["plan_code"] == "gratuita"
            async with db.nueva_sesion() as session:
                account = await _lock_account(session, disabled_id)
                stale_at = datetime.now(timezone.utc) - timedelta(seconds=2)
                account.expires_at = stale_at + timedelta(seconds=1)
                period = await session.get(
                    BillingAccountPeriod, uuid.UUID(forced["id"])
                )
                period.starts_at = stale_at
                period.ends_at = stale_at + timedelta(seconds=1)
            assert await close_expired_periods_in_db() == [forced["id"]]
            async with db.nueva_sesion() as session:
                account = await session.get(BillingAccount, disabled_id)
                period = await session.get(BillingAccountPeriod, uuid.UUID(forced["id"]))
                assert account.account_type == "CREDITOS"
                assert period.status == "CERRADO"

        async def run_and_close():
            try:
                await durable_billing_checks()
            finally:
                await db.cerrar_motor()

        asyncio.run(run_and_close())
    finally:
        engine.dispose()
