"""0032 borra la credencial cifrada de los jobs ya terminados.

Se conserva ``credential_ciphertext`` en jobs que aún pueden ejecutarse
(PENDIENTE, ASIGNADO, CORRIENDO: el scheduler y el reencolado la necesitan) y
en jobs de depuración (``request_payload.logger = true``). La columna no se
elimina. Se pierde la credencial de los jobs históricos: no hay downgrade.
"""

from __future__ import annotations

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE jobs SET credential_ciphertext = NULL
        WHERE credential_ciphertext IS NOT NULL
          AND status IN ('COMPLETO', 'FALLIDO', 'CANCELADO')
          AND COALESCE(request_payload->>'logger', '') <> 'true'
        """
    )


def downgrade() -> None:
    pass  # irreversible: las credenciales borradas no se pueden reconstruir
