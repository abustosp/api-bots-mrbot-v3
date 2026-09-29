"""La renovación de la lease de trabajo queda escrita en PostgreSQL.

Motivo (29/09/2026): el reaper en PostgreSQL decide con
``jobs.lease_expires_at``, que solo se escribía al asignar el job. La renovación
en memoria de los eventos válidos (``started``/``progress``/``heartbeat_hint``)
no llegaba a la fila, así que cualquier job que trabajara más de
``WORKER_ACK_LEASE_SECONDS`` (20 s) se reencolaba o se fallaba con
``lease_expired`` aunque el worker estuviera sano. Observado en vivo con
``liquidacion_granos`` y ``retper_iibb_agip``.

Se omite salvo que ``CENTRAL_API_TEST_DATABASE_URL`` apunte a una base
PostgreSQL ya migrada. La prueba **crea su propio job y lo borra al final**: no
toca datos preexistentes (reutiliza el catálogo, el usuario y el worker que ya
estén en la base).

    export CENTRAL_API_TEST_DATABASE_URL='postgresql://usuario:***@127.0.0.1:5432/mrbot_v3'
    ../../.venv/bin/python -m pytest tests/test_lease_renewal_pg.py -v
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg
import pytest

AQUI = Path(__file__).resolve()
SRC = AQUI.parents[1] / "src"
sys.path.insert(0, str(SRC))

DSN_VAR = "CENTRAL_API_TEST_DATABASE_URL"
BOT = "liquidacion_granos"
OPERATION = "consultar"

pytestmark = pytest.mark.skipif(
    not os.environ.get(DSN_VAR),
    reason=f"{DSN_VAR} no configurada: prueba contra PostgreSQL real omitida",
)


def _dsn_psycopg() -> str:
    """DSN plano para psycopg (el resto del código lo normaliza a +psycopg)."""
    return os.environ[DSN_VAR].replace("postgresql+psycopg://", "postgresql://")


def _preparar_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Apunta la central del proceso a la base de la prueba."""
    from central_api import db
    from central_api.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", os.environ[DSN_VAR])
    get_settings.cache_clear()
    for cache in (db._fabrica, db._motor):
        try:
            cache.cache_clear()
        except Exception:  # noqa: BLE001 - caché no inicializada
            pass


def _insertar_job(conn: psycopg.Connection, *, estado: str) -> tuple[uuid.UUID, datetime | None]:
    """Job mínimo con catálogo/usuario/worker existentes.

    Los jobs terminales tienen que respetar la forma que exige el esquema
    (resultado y ``finished_at``), así que se completan según el estado.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users LIMIT 1")
        fila_usuario = cur.fetchone()
        cur.execute("SELECT id FROM workers LIMIT 1")
        fila_worker = cur.fetchone()
    if not fila_usuario or not fila_worker:
        pytest.skip("la base no tiene usuario/worker para sembrar el job")

    job_id = uuid.uuid4()
    terminal = estado in ("COMPLETO", "FALLIDO", "CANCELADO")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO jobs (id, user_id, bot, operation, status, result,"
            " attempts, max_attempts, worker_id, assigned_at, started_at,"
            " finished_at, lease_expires_at, request_payload, credential_metadata)"
            " VALUES (%s, %s, %s, %s, %s, %s, 1, 3, %s,"
            " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP,"
            " CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,"
            " CASE WHEN %s THEN NULL"
            "      ELSE CURRENT_TIMESTAMP + INTERVAL '20 seconds' END,"
            " '{}'::jsonb, '{}'::jsonb)"
            " RETURNING lease_expires_at",
            (
                job_id,
                fila_usuario[0],
                BOT,
                OPERATION,
                estado,
                "OK" if terminal else None,
                fila_worker[0],
                terminal,
                terminal,
            ),
        )
        lease_inicial = cur.fetchone()[0]
    conn.commit()
    return job_id, lease_inicial


def _borrar_job(conn: psycopg.Connection, job_id: uuid.UUID) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM job_events WHERE job_id = %s", (job_id,))
        cur.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
    conn.commit()


def _leer_lease(conn: psycopg.Connection, job_id: uuid.UUID) -> datetime | None:
    with conn.cursor() as cur:
        cur.execute("SELECT lease_expires_at FROM jobs WHERE id = %s", (job_id,))
        fila = cur.fetchone()
    return fila[0] if fila else None


def _persistir_evento(job_id: uuid.UUID, *, tipo: str, clave: str) -> None:
    """Ejercita el mismo camino que el callback HTTP del worker."""
    from central_api.db import nueva_sesion
    from central_api.repositories.jobs import JobRepository

    async def ejecutar() -> None:
        async with nueva_sesion() as sesion:
            repo = JobRepository(sesion)  # type: ignore[arg-type]
            await repo.persist_event(
                job_id, attempt=1, event_key=clave, event_type=tipo
            )

    asyncio.run(ejecutar())


@pytest.mark.parametrize("tipo", ["started", "progress", "heartbeat_hint"])
def test_evento_valido_extiende_la_lease_en_la_base(
    monkeypatch: pytest.MonkeyPatch, tipo: str
) -> None:
    _preparar_settings(monkeypatch)
    with psycopg.connect(_dsn_psycopg(), autocommit=False) as conn:
        job_id, lease_inicial = _insertar_job(conn, estado="CORRIENDO")
        try:
            _persistir_evento(job_id, tipo=tipo, clave=f"evt-{tipo}-{uuid.uuid4().hex[:8]}")
            lease_final = _leer_lease(conn, job_id)
            conn.rollback()  # lectura fresca, sin snapshots viejos
            lease_final = _leer_lease(conn, job_id)
        finally:
            _borrar_job(conn, job_id)

    assert lease_inicial is not None and lease_final is not None
    # La lease de asignación (20 s) tiene que haber pasado a la de trabajo
    # (60 s por defecto): el margen de 30 s distingue una de otra sin depender
    # de la latencia del test.
    assert lease_final >= lease_inicial + timedelta(seconds=30), (
        lease_inicial,
        lease_final,
    )
    assert lease_final >= datetime.now(timezone.utc) + timedelta(seconds=30)


def test_control_negativo_job_terminal_no_renueva(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin asignación viva no hay renovación: el control tiene que fallar."""
    from central_api.repositories.jobs import StaleAssignmentError

    _preparar_settings(monkeypatch)
    with psycopg.connect(_dsn_psycopg(), autocommit=False) as conn:
        job_id, lease_inicial = _insertar_job(conn, estado="COMPLETO")
        try:
            with pytest.raises(StaleAssignmentError):
                _persistir_evento(
                    job_id, tipo="progress", clave=f"evt-neg-{uuid.uuid4().hex[:8]}"
                )
            lease_final = _leer_lease(conn, job_id)
        finally:
            _borrar_job(conn, job_id)

    assert lease_final == lease_inicial
