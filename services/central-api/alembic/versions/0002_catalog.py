"""0002 catalogo: bots y bot_operations (default seguro EFECTO)."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint("btrim(code) <> ''", name="ck_bots_bots_code_nonblank"),
        sa.CheckConstraint("jsonb_typeof(manifest) = 'object'", name="ck_bots_bots_manifest_object"),
        sa.PrimaryKeyConstraint("id", name="pk_bots"),
        sa.UniqueConstraint("code", name="uq_bots_code"),
    )
    op.create_index("ix_bots_enabled", "bots", ["code"], postgresql_where=sa.text("enabled"))
    op.create_table(
        "bot_operations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("bot_code", sa.String(80), nullable=False),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("unit_cost", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("input_schema_version", sa.String(32), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default=sa.text("600"), nullable=False),
        sa.Column("effect_class", sa.String(16), server_default=sa.text("'EFECTO'"), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.CheckConstraint("unit_cost > 0", name="ck_bot_operations_bot_operations_unit_cost"),
        sa.CheckConstraint("timeout_seconds BETWEEN 1 AND 3600", name="ck_bot_operations_bot_operations_timeout"),
        sa.CheckConstraint("jsonb_typeof(manifest) = 'object'", name="ck_bot_operations_bot_operations_manifest"),
        sa.CheckConstraint("effect_class IN ('CONSULTA', 'EFECTO')", name="ck_bot_operations_bot_operations_effect_class"),
        sa.ForeignKeyConstraint(["bot_code"], ["bots.code"], ondelete="RESTRICT", name="fk_bot_operations_bot_code_bots"),
        sa.PrimaryKeyConstraint("id", name="pk_bot_operations"),
        sa.UniqueConstraint("bot_code", "code", name="uq_bot_operations_code"),
    )
    op.create_index(
        "ix_bot_operations_enabled", "bot_operations", ["bot_code", "code"],
        postgresql_where=sa.text("enabled"),
    )


def downgrade() -> None:
    op.drop_table("bot_operations")
    op.drop_table("bots")
