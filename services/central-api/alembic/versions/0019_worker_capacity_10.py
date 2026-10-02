"""0019 amplía el máximo de capacidad y carga por worker a 10 (W-2: 5 -> 10).

Los CHECK de ``workers`` y ``worker_heartbeats`` se localizan por su
definición y no por nombre: la naming convention de la 0003 duplicó el
prefijo (``ck_workers_ck_workers_workers_capacity``), y un nombre supuesto
rompe sobre una base real. Se reemplazan por CHECK con el mismo nombre
convencional que declara ``models/fleet.py``.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

# (tabla, columna, nombre declarado en models/fleet.py)
_LIMITES = (
    ("workers", "capacity", "workers_capacity"),
    ("workers", "running_jobs", "workers_running"),
    ("worker_heartbeats", "capacity", "heartbeats_capacity"),
    ("worker_heartbeats", "running_jobs", "heartbeats_running"),
)


def _reemplazar(tabla: str, columna: str, nombre: str, minimo: int, maximo: int) -> None:
    """Quita todo CHECK de rango simple sobre ``columna`` y crea el nuevo."""
    bind = op.get_bind()
    filas = bind.execute(
        sa.text(
            "SELECT c.conname, pg_get_constraintdef(c.oid) "
            "FROM pg_constraint c WHERE c.conrelid = CAST(:t AS regclass) AND c.contype = 'c'"
        ),
        {"t": tabla},
    ).fetchall()
    for conname, definicion in filas:
        d = definicion.replace(" ", "")
        # Solo CHECK de tope sobre la columna (no 'running <= capacity', estado, etc.).
        if f"{columna}<=" in d and f"{columna}>=" in d and "capacity)" not in d.split("<=")[-1]:
            op.execute(sa.text(f'ALTER TABLE "{tabla}" DROP CONSTRAINT "{conname}"'))
    op.create_check_constraint(
        nombre, tabla, f"{columna} BETWEEN {minimo} AND {maximo}"
    )


def upgrade() -> None:
    for tabla, columna, nombre in _LIMITES:
        _reemplazar(tabla, columna, nombre, 1 if columna == "capacity" else 0, 10)


def downgrade() -> None:
    for tabla, columna, nombre in _LIMITES:
        _reemplazar(tabla, columna, nombre, 1 if columna == "capacity" else 0, 5)
