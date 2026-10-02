"""Regresión de la migración 0019 contra PostgreSQL real (W-2: tope 5 -> 10).

El bug que cubre: la 0019 original asumía nombres de CHECK que solo existen en
el modelo, no en una base migrada desde 0003 (``ck_workers_ck_workers_workers_*``),
y fallaba con UndefinedObject. Offline no se ve. Se omite sin base de pruebas:

    export CENTRAL_API_TEST_DATABASE_URL='postgresql+psycopg://user:pass@127.0.0.1:5432/db_vacia'
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

_DSN = os.environ.get("CENTRAL_API_TEST_DATABASE_URL")
if not _DSN:
    pytest.skip("CENTRAL_API_TEST_DATABASE_URL no configurada", allow_module_level=True)

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

ALEMBIC = Path(__file__).resolve().parents[1] / "alembic"


def _cfg() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC))
    return cfg


@pytest.fixture()
def engine(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", _DSN)
    eng = sa.create_engine(_DSN)
    with eng.begin() as c:
        c.execute(sa.text("DROP SCHEMA IF EXISTS legacy CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    yield eng
    eng.dispose()


def _insert(eng, capacity: int, running: int, node: str):
    with eng.begin() as c:
        c.execute(
            sa.text(
                "INSERT INTO workers(id,name,endpoint,status,capacity,running_jobs,app_version,sealed_pubkey_pem) "
                "VALUES (gen_random_uuid(),'t',:n,'SANO',:c,:r,'v','x')"
            ),
            {"n": node, "c": capacity, "r": running},
        )


def test_0019_sube_el_tope_a_10_sobre_una_base_migrada_desde_0003(engine):
    command.upgrade(_cfg(), "0018")
    with pytest.raises(sa.exc.IntegrityError):
        _insert(engine, 6, 0, "1.1.1.1:1")  # antes del 0019 el tope es 5

    command.upgrade(_cfg(), "0019")
    _insert(engine, 10, 10, "1.1.1.2:1")  # ahora 10 entra
    with pytest.raises(sa.exc.IntegrityError):
        _insert(engine, 11, 0, "1.1.1.3:1")
    with pytest.raises(sa.exc.IntegrityError):
        _insert(engine, 5, 6, "1.1.1.4:1")  # running <= capacity se conserva


def test_0019_downgrade_y_reupgrade_son_idempotentes(engine):
    command.upgrade(_cfg(), "0019")
    command.downgrade(_cfg(), "0018")
    with pytest.raises(sa.exc.IntegrityError):
        _insert(engine, 6, 0, "1.1.1.5:1")
    command.upgrade(_cfg(), "head")
    _insert(engine, 10, 0, "1.1.1.6:1")
