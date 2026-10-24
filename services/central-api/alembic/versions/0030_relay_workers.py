"""0030: workers conectados por relay frp (puerto y hash del token).

Tabla ``relay_workers`` (nodo ``relay:puerto`` -> alias, puerto, sha256 del
token). El token en claro nunca se guarda.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "relay_workers",
        sa.Column("node", sa.String(255), primary_key=True),
        sa.Column("alias", sa.String(64), nullable=False, server_default=""),
        sa.Column("port", sa.Integer, nullable=False, unique=True),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("relay_workers")
