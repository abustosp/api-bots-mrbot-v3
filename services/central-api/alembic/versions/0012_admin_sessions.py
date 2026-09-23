"""0012 panel: sesiones administrativas revocables (plan 05 §3.2).

PK UUIDv7 generada en la aplicación y FK UUID: sin ``serial``,
``bigserial``, ``GENERATED AS IDENTITY`` ni secuencias (I-1/I-2). Solo se
guarda el hash del token opaco, nunca su valor.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "admin_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column(
            "issued_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "last_activity_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.current_timestamp(),
            nullable=False,
        ),
        sa.Column("ip_inicial", sa.Text()),
        sa.Column("user_agent_resumen", sa.String(256)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "expires_at > issued_at", name="ck_admin_sessions_admin_sessions_lifetime"
        ),
        sa.CheckConstraint(
            "btrim(token_hash) <> ''",
            name="ck_admin_sessions_admin_sessions_token_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["admin_user_id"],
            ["admin_users.id"],
            ondelete="CASCADE",
            name="fk_admin_sessions_admin_user_id_admin_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_admin_sessions"),
        sa.UniqueConstraint("token_hash", name="uq_admin_sessions_token_hash"),
    )
    op.create_index(
        "ix_admin_sessions_user_id", "admin_sessions", ["admin_user_id"]
    )
    op.create_index(
        "ix_admin_sessions_active",
        "admin_sessions",
        ["token_hash"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_admin_sessions_active", table_name="admin_sessions")
    op.drop_index("ix_admin_sessions_user_id", table_name="admin_sessions")
    op.drop_table("admin_sessions")
