"""0001 identidad: users (UUIDv4), api_keys y admin_users.

Sin extension UUID obligatoria: los IDs los genera la aplicacion.
``users`` queda exactamente con (id, email, habilitado).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS public")
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("habilitado", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.CheckConstraint("btrim(email) <> ''", name="ck_users_users_email_nonblank"),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
    )
    op.create_index("uq_users_email_lower", "users", [sa.text("lower(email)")], unique=True)
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("verifier_hmac", sa.Text(), nullable=False),
        sa.Column("label", sa.String(120)),
        sa.Column("scopes", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("replaces_key_id", postgresql.UUID(as_uuid=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint("jsonb_typeof(scopes) = 'array'", name="ck_api_keys_api_keys_scopes"),
        sa.CheckConstraint("expires_at IS NULL OR expires_at > created_at", name="ck_api_keys_api_keys_lifetime"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT", name="fk_api_keys_user_id_users"),
        sa.ForeignKeyConstraint(["replaces_key_id"], ["api_keys.id"], ondelete="RESTRICT", name="fk_api_keys_replaces_key_id_api_keys"),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.UniqueConstraint("key_prefix", name="uq_api_keys_key_prefix"),
        sa.UniqueConstraint("verifier_hmac", name="uq_api_keys_verifier_hmac"),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_active", "api_keys", ["key_prefix"], postgresql_where=sa.text("revoked_at IS NULL"))
    op.create_table(
        "admin_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("roles", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("habilitado", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("mfa_secret_ref", sa.Text()),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.current_timestamp(), nullable=False),
        sa.CheckConstraint("jsonb_typeof(roles) = 'array'", name="ck_admin_users_admin_roles"),
        sa.PrimaryKeyConstraint("id", name="pk_admin_users"),
    )
    op.create_index("uq_admin_users_email_lower", "admin_users", [sa.text("lower(email)")], unique=True)


def downgrade() -> None:
    op.drop_table("admin_users")
    op.drop_table("api_keys")
    op.drop_table("users")
