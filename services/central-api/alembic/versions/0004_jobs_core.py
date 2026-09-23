"""0004 nucleo de jobs: tabla unica, checks e indices parciales de cola."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bot", sa.String(80), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'PENDIENTE'"), nullable=False),
        sa.Column("result", sa.String(16)),
        sa.Column("request_payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("idempotency_key", sa.String(128)),
        sa.Column("priority", sa.SmallInteger(), server_default=sa.text("100"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("assigned_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("worker_id", postgresql.UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("app_version", sa.String(64)),
        sa.Column("protocol_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("cancel_reason", sa.Text()),
        sa.Column("cancelled_by", sa.String(16)),
        sa.Column("error_code", sa.String(80)),
        sa.Column("error_message", sa.Text()),
        sa.CheckConstraint(
            "status IN ('PENDIENTE','ASIGNADO','CORRIENDO','COMPLETO','FALLIDO','CANCELADO')",
            name="ck_jobs_jobs_status",
        ),
        sa.CheckConstraint("result IS NULL OR result IN ('OK','PARCIAL','ERROR')", name="ck_jobs_jobs_result"),
        sa.CheckConstraint("jsonb_typeof(request_payload) = 'object'", name="ck_jobs_jobs_payload_object"),
        sa.CheckConstraint("priority BETWEEN 0 AND 1000", name="ck_jobs_jobs_priority"),
        sa.CheckConstraint("attempts >= 0 AND max_attempts BETWEEN 1 AND 20", name="ck_jobs_jobs_attempts"),
        sa.CheckConstraint(
            "cancelled_by IS NULL OR cancelled_by IN ('USER','ADMIN','SYSTEM')",
            name="ck_jobs_jobs_cancelled_by",
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETO' AND result IN ('OK','PARCIAL','ERROR') AND finished_at IS NOT NULL)"
            " OR (status = 'FALLIDO' AND result = 'ERROR' AND finished_at IS NOT NULL)"
            " OR (status = 'CANCELADO' AND result IS NULL AND finished_at IS NOT NULL)"
            " OR (status IN ('PENDIENTE','ASIGNADO','CORRIENDO') AND finished_at IS NULL)",
            name="ck_jobs_jobs_terminal_shape",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_jobs_user_id_users"),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="SET NULL", name="fk_jobs_worker_id_workers"),
        sa.ForeignKeyConstraint(
            ["bot", "operation"], ["bot_operations.bot_code", "bot_operations.code"],
            ondelete="RESTRICT", name="fk_jobs_operation",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_jobs"),
    )
    op.create_index(
        "uq_jobs_idempotency", "jobs",
        ["user_id", "bot", "operation", "idempotency_key"], unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )
    op.create_index(
        "ix_jobs_queue_pending", "jobs", ["priority", "created_at", "id"],
        postgresql_where=sa.text("status = 'PENDIENTE'"),
    )
    op.create_index(
        "ix_jobs_queue_live", "jobs", ["status", "priority", "created_at", "id"],
        postgresql_where=sa.text("status IN ('PENDIENTE','ASIGNADO','CORRIENDO')"),
    )
    op.create_index(
        "ix_jobs_lease_recovery", "jobs", ["lease_expires_at", "id"],
        postgresql_where=sa.text("status IN ('ASIGNADO','CORRIENDO')"),
    )
    op.create_index("ix_jobs_user_created", "jobs", ["user_id", "created_at", "id"])
    op.create_index(
        "ix_jobs_bot_finished", "jobs", ["bot", "finished_at", "id"],
        postgresql_where=sa.text("status IN ('COMPLETO','FALLIDO','CANCELADO')"),
    )


def downgrade() -> None:
    op.drop_table("jobs")
