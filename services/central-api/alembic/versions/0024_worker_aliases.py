"""0024: alias de workers editables desde el panel de administración.

Tabla ``worker_aliases`` (nodo ``host:puerto`` -> alias legible). Tiene
prioridad sobre ``WORKER_ALIASES`` del entorno, que queda como valor inicial.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_aliases",
        sa.Column("node", sa.String(255), primary_key=True),
        sa.Column("alias", sa.String(64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("worker_aliases")
