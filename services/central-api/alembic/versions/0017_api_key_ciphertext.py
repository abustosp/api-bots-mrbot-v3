"""0017 cifrado recuperable de API keys para el panel admin."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("api_keys", sa.Column("encrypted_value", sa.Text(), nullable=True))
    op.create_check_constraint(
        "api_keys_encrypted_value",
        "api_keys",
        "encrypted_value IS NULL OR btrim(encrypted_value) <> ''",
    )


def downgrade() -> None:
    op.drop_constraint("ck_api_keys_api_keys_encrypted_value", "api_keys", type_="check")
    op.drop_column("api_keys", "encrypted_value")
