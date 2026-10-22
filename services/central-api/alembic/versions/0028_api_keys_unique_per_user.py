"""0028 unicidad de API keys activas por usuario.

La autenticación exige usuario + clave, así que dos usuarios pueden compartir
el mismo valor. Los índices únicos parciales pasan de ``(key_prefix)`` y
``(verifier_hmac)`` a ``(user_id, key_prefix)`` y ``(user_id, verifier_hmac)``.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_ACTIVA = sa.text("revoked_at IS NULL")


def upgrade() -> None:
    op.drop_index("uq_api_keys_active_prefix", table_name="api_keys")
    op.drop_index("uq_api_keys_active_verifier", table_name="api_keys")
    op.create_index(
        "uq_api_keys_active_user_prefix", "api_keys", ["user_id", "key_prefix"],
        unique=True, postgresql_where=_ACTIVA,
    )
    op.create_index(
        "uq_api_keys_active_user_verifier", "api_keys", ["user_id", "verifier_hmac"],
        unique=True, postgresql_where=_ACTIVA,
    )


def downgrade() -> None:
    # Falla si hay valores compartidos entre usuarios: depurar antes de volver.
    op.drop_index("uq_api_keys_active_user_verifier", table_name="api_keys")
    op.drop_index("uq_api_keys_active_user_prefix", table_name="api_keys")
    op.create_index(
        "uq_api_keys_active_verifier", "api_keys", ["verifier_hmac"],
        unique=True, postgresql_where=_ACTIVA,
    )
    op.create_index(
        "uq_api_keys_active_prefix", "api_keys", ["key_prefix"],
        unique=True, postgresql_where=_ACTIVA,
    )
