"""0021 tickets de upload e idempotencia durables.

Los tickets de carga son capacidades de vida corta asociadas a un usuario. La
idempotencia conserva únicamente el fingerprint del request y el UUID del job,
nunca el payload ni una clave API.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "upload_tickets",
        sa.Column("object_key", sa.String(255), primary_key=True),
        sa.Column("upload_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bot", sa.String(80), nullable=False),
        sa.Column("operation", sa.String(80), nullable=False),
        sa.Column("field", sa.String(120), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(128), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["bot", "operation"],
            ["bot_operations.bot_code", "bot_operations.code"],
            ondelete="RESTRICT",
            name="fk_upload_tickets_operation",
        ),
        sa.UniqueConstraint("upload_id", name="uq_upload_tickets_upload_id"),
        sa.CheckConstraint("size_bytes > 0", name="upload_tickets_size_positive"),
    )
    op.create_index(
        "ix_upload_tickets_user_expires",
        "upload_tickets",
        ["user_id", "expires_at"],
    )
    op.create_index("ix_upload_tickets_expires", "upload_tickets", ["expires_at"])

    op.create_table(
        "api_idempotency",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=False),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.current_timestamp()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("user_id", "idempotency_key", name="pk_api_idempotency"),
        sa.CheckConstraint("btrim(idempotency_key) <> ''", name="api_idempotency_key_nonblank"),
        sa.CheckConstraint("btrim(fingerprint) <> ''", name="api_idempotency_fingerprint_nonblank"),
    )
    op.create_index("ix_api_idempotency_expires", "api_idempotency", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_api_idempotency_expires", table_name="api_idempotency")
    op.drop_table("api_idempotency")
    op.drop_index("ix_upload_tickets_expires", table_name="upload_tickets")
    op.drop_index("ix_upload_tickets_user_expires", table_name="upload_tickets")
    op.drop_table("upload_tickets")
