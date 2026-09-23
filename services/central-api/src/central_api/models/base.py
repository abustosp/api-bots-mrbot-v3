"""Base declarativa y utilidades de identificadores (plans/01-database §3).

Solo la central importa este paquete (invariante W-1): el worker no recibe
DSN ni modelos ORM. Todos los UUID se generan en la aplicacion antes del
``INSERT`` para permitir pruebas deterministas con un generador inyectable.
"""

from __future__ import annotations

import random
import time
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase
from mrbot_contracts.version import PROTOCOL_VERSION

#: Tope duro de trabajos simultaneos por worker (invariante W-2).
WORKER_CAPACITY_MAX: int = 5

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base declarativa unica de la central; su ``metadata`` es la de Alembic."""

    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def new_uuid4() -> uuid.UUID:
    """Genera el ``users.id`` (UUIDv4, no enumerable ni ordenable)."""
    return uuid.uuid4()


def new_uuid7() -> uuid.UUID:
    """Genera un UUIDv7 (orden temporal aproximado) sin dependencias externas.

    Implementacion RFC 9562: 48 bits de milisegundos Unix, version 7 y
    74 bits aleatorios con los 2 bits de variante fijados a ``10``.
    """
    ms = time.time_ns() // 1_000_000
    rand_a = random.getrandbits(12)
    rand_b = random.getrandbits(62)
    # Variante RFC 4122 (``10``) en los bits 62-63; versión 7 en 76-79.
    value = (ms << 80) | (7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


__all__ = [
    "Base",
    "NAMING_CONVENTION",
    "PROTOCOL_VERSION",
    "WORKER_CAPACITY_MAX",
    "new_uuid4",
    "new_uuid7",
]
