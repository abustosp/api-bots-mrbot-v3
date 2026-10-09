"""Cobertura con PostgreSQL real de la migración 0022 y los tres tipos de cuenta."""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

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


def _accounts(engine, ids):
    with engine.connect() as conn:
        rows = conn.execute(
            sa.text(
                "SELECT user_id, account_type, plan_code, max_requests, expires_at, "
                "requests_consumed FROM billing_accounts WHERE user_id = ANY(:ids)"
            ),
            {"ids": list(ids)},
        ).mappings().all()
    return {row["user_id"]: row for row in rows}


def test_0022_maps_legacy_modes_and_roundtrips_on_postgres(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", DSN)
    engine = sa.create_engine(_sync_dsn())
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("DROP SCHEMA IF EXISTS legacy CASCADE"))
            conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
            conn.execute(sa.text("CREATE SCHEMA public"))

        command.upgrade(_cfg(), "0021")
        names = ("creditos", "gratuita", "tier", "libre")
        ids = {name: uuid.uuid4() for name in names}
        with engine.begin() as conn:
            for name, uid in ids.items():
                conn.execute(
                    sa.text("INSERT INTO users (id, email, habilitado) VALUES (:i, :e, true)"),
                    {"i": uid, "e": f"{name}@example.test"},
                )
            conn.execute(sa.text("DELETE FROM billing_accounts WHERE user_id = ANY(:ids)"),
                         {"ids": list(ids.values())})
            conn.execute(
                sa.text(
                    "INSERT INTO billing_accounts (user_id, account_type, plan_code, "
                    "max_requests, expires_at, requests_consumed) VALUES "
                    "(:c, 'CREDITOS', NULL, NULL, NULL, 0), "
                    "(:g, 'GRATUITA', 'gratuita', 100, now() + interval '5 days', 37), "
                    "(:t, 'TIER', 'tier-500', NULL, NULL, 0), "
                    "(:l, 'LIBRE', NULL, 250, NULL, 12)"
                ),
                {"c": ids["creditos"], "g": ids["gratuita"], "t": ids["tier"], "l": ids["libre"]},
            )

        command.upgrade(_cfg(), "0022")
        accounts = _accounts(engine, ids.values())
        for name in ("creditos", "gratuita"):
            row = accounts[ids[name]]
            assert (row["account_type"], row["max_requests"]) == ("PRUEBA", 100)
            assert row["plan_code"] is None and row["expires_at"] is None
        assert accounts[ids["gratuita"]]["requests_consumed"] == 37  # conserva el consumo
        assert accounts[ids["tier"]]["account_type"] == "NORMAL"
        assert accounts[ids["tier"]]["max_requests"] == 500  # unidades del tier
        assert accounts[ids["libre"]]["account_type"] == "NORMAL"
        assert accounts[ids["libre"]]["max_requests"] == 250

        with pytest.raises(sa.exc.IntegrityError):
            with engine.begin() as conn:
                conn.execute(
                    sa.text("UPDATE billing_accounts SET account_type = 'LIBRE' WHERE user_id = :u"),
                    {"u": ids["libre"]},
                )
        with pytest.raises(sa.exc.IntegrityError):  # PRUEBA exige exactamente 100
            with engine.begin() as conn:
                conn.execute(
                    sa.text("UPDATE billing_accounts SET max_requests = 5 WHERE user_id = :u"),
                    {"u": ids["creditos"]},
                )

        command.downgrade(_cfg(), "0021")
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one() == "0021"
        command.upgrade(_cfg(), "head")

        _durable_checks(engine, ids)
    finally:
        engine.dispose()


def _durable_checks(engine, ids):
    src = SERVICE_DIR / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from central_api import db
    from central_api.billing.entitlements import assign_account, hydrate_billing_account
    from central_api.billing.postgres import (
        close_expired_periods_in_db,
        reserve_in_session,
        settle_in_session,
    )
    from central_api.billing.reservation import QuotaExhausted
    from central_api.models.billing import BillingAccount, BillingAccountPeriod
    from central_api.models.execution import Job
    from central_api.models.identity import User
    from central_api.settings import get_settings
    from sqlalchemy import select

    get_settings.cache_clear()
    db._fabrica.cache_clear()
    db._motor.cache_clear()
    with engine.begin() as conn:
        conn.execute(sa.text("INSERT INTO bots (id, code, display_name) VALUES (:id, 'ccma', 'b')"),
                     {"id": uuid.uuid4()})
        conn.execute(
            sa.text("INSERT INTO bot_operations (id, bot_code, code, input_schema_version) "
                    "VALUES (:id, 'ccma', 'billing-check', '1')"),
            {"id": uuid.uuid4()},
        )

    async def reserve(uid):
        jid = uuid.uuid4()
        async with db.nueva_sesion() as session:
            session.add(Job(id=jid, user_id=uid, bot="ccma", operation="billing-check"))
            await session.flush()
            usage = await reserve_in_session(session, user_id=uid, job_id=jid,
                                             bot="ccma", operation="billing-check")
        return usage

    async def settle(usage, status):
        async with db.nueva_sesion() as session:
            await settle_in_session(session, usage.job_id, status=status)

    async def checks():
        # --- PRUEBA: ya tenía 37; quedan 63, sin renovación ---------------
        prueba = ids["gratuita"]
        for _ in range(63):
            await settle(await reserve(prueba), "CONFIRMADO")
        # Al confirmarse el ultimo request la prueba termina: NORMAL + deshabilitado.
        account = await hydrate_billing_account(str(prueba))
        assert (account.account_type, account.max_requests) == ("NORMAL", 100)
        assert account.requests_consumed == 0
        async with db.nueva_sesion() as session:
            user = await session.get(User, prueba)
            assert user.habilitado is False
        # El admin habilita manualmente y el usuario ya tiene su cupo mensual.
        async with db.nueva_sesion() as session:
            (await session.get(User, prueba)).habilitado = True
        await reserve(prueba)

        # --- concurrencia: nunca se pasa del tope ---------------------------
        racer = ids["creditos"]
        for _ in range(98):
            await settle(await reserve(racer), "CONFIRMADO")
        outcomes = await asyncio.gather(*(reserve(racer) for _ in range(5)),
                                        return_exceptions=True)
        ok = [o for o in outcomes if not isinstance(o, BaseException)]
        assert len(ok) == 2 and sum(isinstance(o, QuotaExhausted) for o in outcomes) == 3
        for usage in ok:
            await settle(usage, "LIBERADO")  # liberar devuelve el cupo
        await reserve(racer)

        # --- TESTING: ilimitado ---------------------------------------------
        await assign_account(str(ids["libre"]), "TESTING")
        for _ in range(150):
            await settle(await reserve(ids["libre"]), "CONFIRMADO")
        async with db.nueva_sesion() as session:
            row = await session.get(BillingAccount, ids["libre"])
            assert (row.account_type, row.max_requests, row.requests_consumed) == ("TESTING", None, 150)

        # --- NORMAL: cupo mensual y renovación ------------------------------
        normal = ids["tier"]
        await assign_account(str(normal), "NORMAL", max_requests=2)
        first = await reserve(normal)
        await settle(first, "CONFIRMADO")
        await settle(await reserve(normal), "CONFIRMADO")
        with pytest.raises(QuotaExhausted):
            await reserve(normal)
        await assign_account(str(normal), "NORMAL", max_requests=3)  # sube el cupo del mes
        await settle(await reserve(normal), "CONFIRMADO")
        with pytest.raises(QuotaExhausted):
            await reserve(normal)
        # Pasa el mes: el período vence y el siguiente se abre solo.
        async with db.nueva_sesion() as session:
            period = (await session.execute(
                select(BillingAccountPeriod).where(
                    BillingAccountPeriod.user_id == normal,
                    BillingAccountPeriod.status == "ABIERTO"))).scalar_one()
            stale = datetime.now(timezone.utc) - timedelta(seconds=5)
            period.starts_at, period.ends_at = stale - timedelta(days=30), stale
            old_id = period.id
        assert await close_expired_periods_in_db() == [str(old_id)]
        for _ in range(3):  # cupo completo nuevo sin intervención del admin
            await settle(await reserve(normal), "CONFIRMADO")
        with pytest.raises(QuotaExhausted):
            await reserve(normal)
        async with db.nueva_sesion() as session:
            periods = (await session.execute(
                select(BillingAccountPeriod).where(BillingAccountPeriod.user_id == normal)
            )).scalars().all()
            assert sorted(p.status for p in periods) == ["ABIERTO", "CERRADO"]

        # --- cambiar de tipo reinicia contadores ----------------------------
        await assign_account(str(ids["creditos"]), "NORMAL", max_requests=5)
        account = await hydrate_billing_account(str(ids["creditos"]))
        assert (account.account_type, account.requests_consumed) == ("NORMAL", 0)
        await reserve(ids["creditos"])

    async def run():
        try:
            await checks()
        finally:
            await db.cerrar_motor()

    asyncio.run(run())
