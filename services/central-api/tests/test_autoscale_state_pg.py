"""Persistencia real en PostgreSQL de politica e instancias manuales (0027).

Se omite sin ``CENTRAL_API_TEST_DATABASE_URL`` (base ya migrada).
"""

from __future__ import annotations

import asyncio
import os

import pytest

DSN_VAR = "CENTRAL_API_TEST_DATABASE_URL"
pytestmark = pytest.mark.skipif(not os.environ.get(DSN_VAR), reason="sin PostgreSQL de prueba")


def test_policy_and_manual_roundtrip(monkeypatch):
    from central_api import db
    from central_api.autoscaler import state
    from central_api.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", os.environ[DSN_VAR])
    get_settings.cache_clear()
    for cached in (db._fabrica, db._motor):
        cached.cache_clear()

    async def scenario():
        state.POLICY_OVERRIDES.clear()
        state.MANUAL_IDS.clear()
        state.POLICY_OVERRIDES.update({"queue_threshold": 7, "jobs_per_worker": 9})
        state.MANUAL_IDS.update({"i-a", "i-b"})
        await state.save_policy()
        await state.save_manual()
        state.POLICY_OVERRIDES.clear()
        state.MANUAL_IDS.clear()
        await state.load_state()
        got = (dict(state.POLICY_OVERRIDES), set(state.MANUAL_IDS))
        # Sobrescribir (upsert) y limpiar
        state.POLICY_OVERRIDES.clear()
        state.MANUAL_IDS.clear()
        await state.save_policy()
        await state.save_manual()
        await db.cerrar_motor()
        return got

    policy, manual = asyncio.run(scenario())
    get_settings.cache_clear()
    assert policy == {"queue_threshold": 7, "jobs_per_worker": 9}
    assert manual == {"i-a", "i-b"}


def test_hard_terminate_requeues_pg_jobs(monkeypatch):
    """Hard contra PostgreSQL: vence leases del nodo y el reaper reencola el job."""
    import uuid
    from datetime import timedelta

    import psycopg

    from central_api import db
    from central_api.autoscaler import controller
    from central_api.settings import get_settings
    from central_api.store import ADMIN_NODES, EPHEMERAL_WORKERS, WORKERS, utcnow

    dsn = os.environ[DSN_VAR].replace("postgresql+psycopg://", "postgresql://")
    node = "198.51.100.99:8080"
    uid, wid, jid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT bot_code, code FROM bot_operations LIMIT 1")
        row = cur.fetchone()
        seeded_bot = None
        if row is None:
            seeded_bot = "consulta_cuit"
            cur.execute("INSERT INTO bots (id, code, display_name) VALUES (%s,%s,'t') ON CONFLICT DO NOTHING",
                        (uuid.uuid4(), seeded_bot))
            cur.execute("INSERT INTO bot_operations (id, bot_code, code, input_schema_version)"
                        " VALUES (%s,%s,'op','1')", (uuid.uuid4(), seeded_bot))
            row = (seeded_bot, "op")
        cur.execute("INSERT INTO users (id, email, habilitado) VALUES (%s, %s, true)",
                    (uid, f"{uid}@t.local"))
        cur.execute("INSERT INTO workers (id, name, endpoint, app_version, running_jobs) "
                    "VALUES (%s, %s, %s, 'test', 1)", (wid, f"w-{wid}", node))
        cur.execute(
            "INSERT INTO jobs (id, user_id, bot, operation, status, attempts, max_attempts,"
            " worker_id, assigned_at, started_at, lease_expires_at, request_payload,"
            " credential_metadata) VALUES (%s,%s,%s,%s,'CORRIENDO',1,3,%s,"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '1 hour',"
            " '{}'::jsonb, '{}'::jsonb)", (jid, uid, row[0], row[1], wid))
        conn.commit()

    monkeypatch.setenv("DATABASE_URL", os.environ[DSN_VAR])
    get_settings.cache_clear()
    for cached in (db._fabrica, db._motor):
        cached.cache_clear()
    EPHEMERAL_WORKERS["i-pg"] = {"instance_id": "i-pg", "node": node, "launched_at": utcnow(),
                                 "inventory_owned": True}
    ADMIN_NODES.add(node)

    class Fleet:
        def terminate(self, ids): return list(ids)

    try:
        async def run():
            released = await controller._release_jobs_hard(EPHEMERAL_WORKERS["i-pg"])
            await db.cerrar_motor()
            return released

        released = asyncio.run(run())
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT status, worker_id, lease_expires_at FROM jobs WHERE id=%s", (jid,))
            status, worker_id, lease = cur.fetchone()
        assert released == 1
        assert status == "PENDIENTE" and worker_id is None and lease is None
    finally:
        EPHEMERAL_WORKERS.pop("i-pg", None)
        ADMIN_NODES.discard(node)
        WORKERS.pop(node, None)
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM job_events WHERE job_id=%s", (jid,))
            cur.execute("DELETE FROM jobs WHERE id=%s", (jid,))
            cur.execute("DELETE FROM workers WHERE id=%s", (wid,))
            cur.execute("DELETE FROM users WHERE id=%s", (uid,))
            if seeded_bot:
                cur.execute("DELETE FROM bot_operations WHERE bot_code=%s", (seeded_bot,))
                cur.execute("DELETE FROM bots WHERE code=%s", (seeded_bot,))
            conn.commit()
        get_settings.cache_clear()
