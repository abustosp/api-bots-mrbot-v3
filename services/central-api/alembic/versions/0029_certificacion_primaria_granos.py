"""0029: bot ``certificacion_primaria_granos`` (certificados de depósito como depositante).

Agrega la tabla física ``bot_jobs_certificacion_primaria_granos`` con la misma forma
que las de la revisión 0015 y reemplaza las funciones de sincronización para
que el ``CASE`` por bot incluya el código nuevo.

El bot no existía en V1/V2: no hay vista legacy que crear. Las definiciones se reutilizan cargando la revisión 0015 por ruta: no
se importa código de la aplicación desde Alembic.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

NEW_BOT_CODES = ("certificacion_primaria_granos",)

_VERSIONS = Path(__file__).resolve().parent


def _load(nombre: str, archivo: str):
    spec = importlib.util.spec_from_file_location(nombre, _VERSIONS / archivo)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _r0015():
    modulo = _load("bot_jobs_revision_0015_for_0029", "0015_bot_job_tables.py")
    modulo.op = op
    return modulo


def _sync_functions(codes: tuple[str, ...]) -> tuple[str, ...]:
    r0015 = _r0015()
    original = r0015.BOT_CODES
    r0015.BOT_CODES = codes
    try:
        statements = r0015._sync_functions_sql()
    finally:
        r0015.BOT_CODES = original
    return tuple(
        s.replace("CREATE FUNCTION ", "CREATE OR REPLACE FUNCTION ", 1)
        for s in statements
    )


def _codigos_previos() -> tuple[str, ...]:
    """Códigos agregados por 0023 y 0026: el sync debe seguir cubriéndolos."""
    codigos: list[str] = []
    for archivo in ("0023_ccma_compensacion.py", "0026_libro_sueldos_digital.py"):
        modulo = _load(f"bot_codes_{archivo[:4]}_for_0029", archivo)
        codigos.extend(getattr(modulo, "NEW_BOT_CODES", ()))
    return tuple(codigos)


def upgrade() -> None:
    r0015 = _r0015()
    original = r0015.BOT_CODES
    r0015.BOT_CODES = NEW_BOT_CODES
    try:
        r0015._create_detail_tables()
    finally:
        r0015.BOT_CODES = original
    for statement in _sync_functions(
        tuple(original) + _codigos_previos() + NEW_BOT_CODES
    ):
        op.execute(statement)


def downgrade() -> None:
    r0015 = _r0015()
    for statement in _sync_functions(
        tuple(r0015.BOT_CODES) + _codigos_previos()
    ):
        op.execute(statement)
    for code in NEW_BOT_CODES:
        op.drop_table(r0015._table_name(code))
