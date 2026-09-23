"""0011 flota: pubkey del sobre sellado en workers.

Guarda la RSA **pública** efímera que el worker publica al registrarse;
jamas material privado (la privada nunca sale de la memoria del worker).
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "workers", sa.Column("sealed_pubkey_pem", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("workers", "sealed_pubkey_pem")
