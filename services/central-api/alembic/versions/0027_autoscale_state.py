"""0027: estado editable del autoscaler (política y workers efímeros manuales).

Tabla clave/valor ``autoscale_state``. ``policy`` guarda los overrides de la
política definidos desde el panel y ``manual_instances`` la lista de
instancias lanzadas a mano (no las retira el autoscaler automático).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "autoscale_state",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_table("autoscale_state")
