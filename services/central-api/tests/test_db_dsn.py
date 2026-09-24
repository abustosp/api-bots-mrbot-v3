"""El DSN de PostgreSQL debe resolverse al driver que realmente está instalado.

Un DSN ``postgresql://`` sin driver explícito hace que SQLAlchemy elija
``psycopg2`` (síncrono) o ``asyncpg`` (asíncrono); ninguna de las dos es
dependencia del proyecto. El contrato es ``psycopg`` v3 para la API y las
migraciones, así que el DSN se normaliza antes de crear los motores.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.db import _url_async, normalizar_dsn  # noqa: E402


def test_dsn_estandar_usa_psycopg() -> None:
    assert (
        normalizar_dsn("postgresql://app:clave@postgres:5432/mrbot")
        == "postgresql+psycopg://app:clave@postgres:5432/mrbot"
    )
    assert (
        normalizar_dsn("postgres://app:clave@postgres:5432/mrbot")
        == "postgresql+psycopg://app:clave@postgres:5432/mrbot"
    )


def test_dsn_con_driver_explicito_se_respeta() -> None:
    explicito = "postgresql+psycopg://app:clave@postgres:5432/mrbot"
    assert normalizar_dsn(explicito) == explicito
    assert normalizar_dsn("postgresql+psycopg2://app@postgres/mrbot") == (
        "postgresql+psycopg2://app@postgres/mrbot"
    )


def test_dsn_no_postgres_no_se_toca() -> None:
    assert normalizar_dsn("sqlite:///./local.db") == "sqlite:///./local.db"
    assert normalizar_dsn("") == ""


def test_motor_asincrono_usa_el_mismo_driver() -> None:
    assert _url_async("postgresql://app:clave@postgres:5432/mrbot") == (
        "postgresql+psycopg://app:clave@postgres:5432/mrbot"
    )
