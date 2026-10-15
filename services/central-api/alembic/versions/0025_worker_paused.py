"""0025: workers pausados desde el panel de administración.

Un worker pausado no recibe jobs nuevos (se reparten entre los demás) pero
termina los que ya tiene. La marca sobrevive a reinicios de la central.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "worker_paused",
        sa.Column("node", sa.String(255), primary_key=True),
        sa.Column(
            "paused_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("worker_paused")
