"""0009 vista operativa de flota (solo lectura; no muta workers.status)."""

from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_VIEW_SQL = """
CREATE OR REPLACE VIEW worker_fleet_health AS
SELECT w.id, w.name, w.status, w.protocol_version, w.app_version,
       w.capacity, w.running_jobs, w.queued_jobs, w.last_heartbeat_at,
       CURRENT_TIMESTAMP - w.last_heartbeat_at AS heartbeat_age,
       CASE
           WHEN w.status = 'DRENANDO' THEN 'DRENANDO'
           WHEN w.last_heartbeat_at IS NULL THEN 'CAIDO'
           WHEN w.last_heartbeat_at < CURRENT_TIMESTAMP - INTERVAL '30 seconds'
               THEN 'CAIDO'
           WHEN w.running_jobs >= w.capacity THEN 'SATURADO'
           WHEN w.status = 'SANO' THEN 'SANO'
           ELSE 'DEGRADADO'
       END AS effective_health
FROM workers w
"""


def upgrade() -> None:
    op.execute(_VIEW_SQL)
    # Roles de minimo privilegio y append-only se crean en el despliegue
    # (06-infra): esta revision deja constancia del contrato esperado.
    # - rol runtime central: DML total en public salvo borrar ledgers/auditoria.
    # - rol worker: sin privilegios de DB (W-1); solo callbacks HTTP.


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS worker_fleet_health")
