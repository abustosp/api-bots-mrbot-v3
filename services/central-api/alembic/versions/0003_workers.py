"""0003 flota: workers y worker_heartbeats (tope 5 en checks)."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("status", sa.String(16), server_default=sa.text("'REGISTRANDO'"), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("protocol_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("app_version", sa.String(64), nullable=False),
        sa.Column("capacity", sa.SmallInteger(), server_default=sa.text("5"), nullable=False),
        sa.Column("running_jobs", sa.SmallInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("queued_jobs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("registered_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("drained_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "status IN ('REGISTRANDO','SANO','DEGRADADO','DRENANDO','CAIDO')",
            name="ck_workers_workers_status",
        ),
        sa.CheckConstraint("capacity BETWEEN 1 AND 5", name="ck_workers_workers_capacity"),
        sa.CheckConstraint("running_jobs BETWEEN 0 AND 5", name="ck_workers_workers_running"),
        sa.CheckConstraint("queued_jobs >= 0", name="ck_workers_workers_queued"),
        sa.CheckConstraint("running_jobs <= capacity", name="ck_workers_workers_running_capacity"),
        sa.PrimaryKeyConstraint("id", name="pk_workers"),
        sa.UniqueConstraint("name", name="uq_workers_name"),
        sa.UniqueConstraint("endpoint", name="uq_workers_endpoint"),
    )
    op.create_index(
        "ix_workers_schedulable", "workers", ["last_heartbeat_at", "running_jobs"],
        postgresql_where=sa.text("status = 'SANO'"),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("worker_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("running_jobs", sa.SmallInteger(), nullable=False),
        sa.Column("queued_jobs", sa.Integer(), nullable=False),
        sa.Column("capacity", sa.SmallInteger(), nullable=False),
        sa.Column("metrics", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint(
            "status IN ('REGISTRANDO','SANO','DEGRADADO','DRENANDO','CAIDO')",
            name="ck_worker_heartbeats_heartbeats_status",
        ),
        sa.CheckConstraint("running_jobs BETWEEN 0 AND 5", name="ck_worker_heartbeats_heartbeats_running"),
        sa.CheckConstraint("queued_jobs >= 0", name="ck_worker_heartbeats_heartbeats_queue"),
        sa.CheckConstraint("capacity BETWEEN 1 AND 5", name="ck_worker_heartbeats_heartbeats_capacity"),
        sa.CheckConstraint("running_jobs <= capacity", name="ck_worker_heartbeats_heartbeats_load"),
        sa.CheckConstraint("jsonb_typeof(metrics) = 'object'", name="ck_worker_heartbeats_heartbeats_metrics"),
        sa.ForeignKeyConstraint(["worker_id"], ["workers.id"], ondelete="CASCADE", name="fk_worker_heartbeats_worker_id_workers"),
        sa.PrimaryKeyConstraint("id", name="pk_worker_heartbeats"),
    )
    op.create_index("ix_worker_heartbeats_worker_time", "worker_heartbeats", ["worker_id", "received_at"])


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_table("workers")
