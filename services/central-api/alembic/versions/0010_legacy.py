"""0010 esquema legacy de solo lectura y mapa de usuarios migrados."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS legacy")
    op.create_table(
        "user_id_map",
        sa.Column("legacy_id", sa.Integer(), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mapped_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_legacy_user_id_map_user_id_users"),
        sa.PrimaryKeyConstraint("legacy_id", name="pk_legacy_user_id_map"),
        sa.UniqueConstraint("user_id", name="uq_legacy_user_id_map_user"),
        schema="legacy",
    )
    # La importacion V2 (28 consulta_*_logs, jobs activos/historial, audits)
    # la ejecuta el plan 07-migracion como copia de solo lectura; el rol
    # runtime solo recibe USAGE + SELECT sobre legacy (sin DML).


def downgrade() -> None:
    op.drop_table("user_id_map", schema="legacy")
    op.execute("DROP SCHEMA IF EXISTS legacy")
