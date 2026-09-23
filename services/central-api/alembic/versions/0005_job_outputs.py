"""0005 salidas: job_events, job_results (GIN) y job_artifacts."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("event_key", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint("attempt >= 0", name="ck_job_events_job_events_attempt"),
        sa.CheckConstraint(
            "event_type IN ('CREADO','RESERVADO','ASIGNADO','INICIADO','PROGRESO',"
            "'LEASE_RENOVADO','REINTENTO','COMPLETADO','FALLIDO','CANCELADO','RESULTADO_RECIBIDO')",
            name="ck_job_events_job_events_type",
        ),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_job_events_job_events_payload"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE", name="fk_job_events_job_id_jobs"),
        sa.PrimaryKeyConstraint("id", name="pk_job_events"),
        sa.UniqueConstraint("job_id", "attempt", "event_key", name="uq_job_events_idempotency"),
    )
    op.create_index("ix_job_events_job_time", "job_events", ["job_id", "occurred_at", "id"])
    op.create_table(
        "job_results",
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("result", sa.String(16), nullable=False),
        sa.Column("payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("summary", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint("attempt > 0", name="ck_job_results_job_results_attempt"),
        sa.CheckConstraint("result IN ('OK','PARCIAL','ERROR')", name="ck_job_results_job_results_result"),
        sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name="ck_job_results_job_results_payload"),
        sa.CheckConstraint("jsonb_typeof(summary) = 'object'", name="ck_job_results_job_results_summary"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE", name="fk_job_results_job_id_jobs"),
        sa.PrimaryKeyConstraint("job_id", name="pk_job_results"),
    )
    op.create_index(
        "ix_job_results_payload_gin", "job_results", ["payload"],
        postgresql_using="gin", postgresql_ops={"payload": "jsonb_path_ops"},
    )
    op.create_table(
        "job_artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(255)),
        sa.Column("size_bytes", sa.BigInteger()),
        sa.Column("sha256", sa.CHAR(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint("kind IN ('ARCHIVO','CAPTURA','TRACE','OTRO')", name="ck_job_artifacts_job_artifacts_kind"),
        sa.CheckConstraint("size_bytes IS NULL OR size_bytes >= 0", name="ck_job_artifacts_job_artifacts_size"),
        sa.CheckConstraint("sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'", name="ck_job_artifacts_job_artifacts_sha256"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE", name="fk_job_artifacts_job_id_jobs"),
        sa.PrimaryKeyConstraint("id", name="pk_job_artifacts"),
        sa.UniqueConstraint("object_key", name="uq_job_artifacts_object_key"),
    )
    op.create_index("ix_job_artifacts_job_id", "job_artifacts", ["job_id", "created_at"])
    op.create_index(
        "ix_job_artifacts_expiry", "job_artifacts", ["expires_at"],
        postgresql_where=sa.text("expires_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("job_artifacts")
    op.drop_table("job_results")
    op.drop_table("job_events")
