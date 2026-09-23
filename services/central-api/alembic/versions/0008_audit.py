"""0008 auditoria: audit_log append-only con indices forenses."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(120), nullable=False),
        sa.Column("target_type", sa.String(48), nullable=False),
        sa.Column("target_id", postgresql.UUID(as_uuid=True)),
        sa.Column("request_id", postgresql.UUID(as_uuid=True)),
        sa.Column("remote_addr", postgresql.INET()),
        sa.Column("user_agent", sa.Text()),
        sa.Column("metadata", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint(
            "actor_type IN ('ADMIN','USER','SERVICE','SYSTEM')",
            name="ck_audit_log_audit_log_actor_type",
        ),
        sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name="ck_audit_log_audit_log_metadata"),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
    )
    op.create_index("ix_audit_log_occurred_at", "audit_log", ["occurred_at"])
    op.create_index("ix_audit_log_actor", "audit_log", ["actor_type", "actor_id", "occurred_at"])
    op.create_index("ix_audit_log_target", "audit_log", ["target_type", "target_id", "occurred_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
