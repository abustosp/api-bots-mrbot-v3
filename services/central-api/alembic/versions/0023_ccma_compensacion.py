"""0023: bot ``ccma_compensacion`` (compensación de saldos a favor de CCMA).

Agrega la tabla física ``bot_jobs_ccma_compensacion`` con la misma forma que
las de la revisión 0015, reemplaza las funciones de sincronización para que el
``CASE`` por bot incluya el código nuevo y crea la vista legacy
``consulta_ccma_compensacion_logs`` (criterio de la revisión 0016).

Las definiciones se reutilizan cargando las revisiones 0015/0016 por ruta: no
se importa código de la aplicación desde Alembic.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

NEW_BOT_CODES = ("ccma_compensacion",)
LEGACY_VIEWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "consulta_ccma_compensacion_logs",
        "ccma_compensacion",
        (
            "id", "user_id", "timestamp", "cuit_representante",
            "cuit_representado", "compensar", "confirmar", "archivos",
            "response_data", "job_id", "status", "error_message",
        ),
    ),
)

_VERSIONS = Path(__file__).resolve().parent


def _load(nombre: str, archivo: str):
    spec = importlib.util.spec_from_file_location(nombre, _VERSIONS / archivo)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _r0015():
    modulo = _load("bot_jobs_revision_0015_for_0023", "0015_bot_job_tables.py")
    modulo.op = op
    return modulo


def _r0016():
    return _load("legacy_views_revision_0016_for_0023", "0016_legacy_log_views.py")


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


def _bool_columns_sql(vista_sql: str) -> str:
    for columna in ("compensar", "confirmar"):
        vista_sql = vista_sql.replace(
            f"t.request_payload->>'{columna}' AS {columna}",
            _r0016()._bool_expr("t.request_payload", columna),
        )
    return vista_sql


def upgrade() -> None:
    r0015 = _r0015()
    original = r0015.BOT_CODES
    r0015.BOT_CODES = NEW_BOT_CODES
    try:
        r0015._create_detail_tables()
    finally:
        r0015.BOT_CODES = original
    for statement in _sync_functions(tuple(original) + NEW_BOT_CODES):
        op.execute(statement)
    r0016 = _r0016()
    for vista, bot, columnas in LEGACY_VIEWS:
        op.execute(_bool_columns_sql(r0016._vista_sql(vista, bot, columnas)))


def downgrade() -> None:
    for vista, _bot, _columnas in LEGACY_VIEWS:
        op.execute(f"DROP VIEW IF EXISTS {vista}")
    r0015 = _r0015()
    for statement in _sync_functions(tuple(r0015.BOT_CODES)):
        op.execute(statement)
    for code in NEW_BOT_CODES:
        op.drop_table(r0015._table_name(code))
