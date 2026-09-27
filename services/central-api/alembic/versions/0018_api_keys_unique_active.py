"""0018 unicidad de API keys solo entre claves activas.

Las claves manuales derivan su selector de ``HMAC(valor)``: con ``UNIQUE``
global, un valor revocado (p. ej. ``abp``) no podía volver a asignarse al
mismo usuario. La autenticación ya filtra ``revoked_at IS NULL``; la unicidad
pasa a índices únicos parciales sobre claves activas.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_api_keys_key_prefix", "api_keys", type_="unique")
    op.drop_constraint("uq_api_keys_verifier_hmac", "api_keys", type_="unique")
    op.drop_index("ix_api_keys_active", table_name="api_keys")
    op.create_index(
        "uq_api_keys_active_prefix",
        "api_keys",
        ["key_prefix"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index(
        "uq_api_keys_active_verifier",
        "api_keys",
        ["verifier_hmac"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_index("ix_api_keys_key_prefix", "api_keys", ["key_prefix"])


def downgrade() -> None:
    # Falla si hay valores repetidos entre claves revocadas: es intencional,
    # volver a UNIQUE global exige depurar esos duplicados antes.
    op.drop_index("ix_api_keys_key_prefix", table_name="api_keys")
    op.drop_index("uq_api_keys_active_verifier", table_name="api_keys")
    op.drop_index("uq_api_keys_active_prefix", table_name="api_keys")
    op.create_index(
        "ix_api_keys_active",
        "api_keys",
        ["key_prefix"],
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.create_unique_constraint("uq_api_keys_verifier_hmac", "api_keys", ["verifier_hmac"])
    op.create_unique_constraint("uq_api_keys_key_prefix", "api_keys", ["key_prefix"])
