"""Entorno Alembic de la central (plan 01 §9.1).

Una sola base PostgreSQL y una sola cabeza. ``DATABASE_URL`` sale de la
configuracion tipada (``central_api.settings``) con ``NullPool``; el
``metadata`` es el declarativo unico de ``central_api.models``. Sin
``render_as_batch`` (innecesario en PostgreSQL 17).
"""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# Permite ``from central_api...`` al correr desde ``services/central-api``.
SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from central_api.models import Base  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    try:
        from central_api.settings import get_settings

        url = get_settings().database_url
        if url:
            return url
    except Exception:  # noqa: BLE001 - cae al entorno en migraciones puras
        pass
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL no configurada para Alembic")
    return url


def run_migrations_offline() -> None:
    """Emite SQL sin conectar (``--sql``)."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Aplica migraciones con conexiones cortas de ``NullPool``."""
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
