"""0033 borra ``credential_ciphertext`` cuando un job pasa a estado terminal.

Un trigger ``BEFORE INSERT OR UPDATE`` cubre todas las rutas que cierran un
job (resultado, cancelación, reaper, admin). Se exceptúan los jobs de
depuración (``request_payload.logger = true``). También limpia lo que quedó
sin purgar desde la 0032. Irreversible: no hay downgrade de datos.
"""

from __future__ import annotations

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION jobs_purge_terminal_credential() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.credential_ciphertext IS NOT NULL
               AND NEW.status IN ('COMPLETO', 'FALLIDO', 'CANCELADO')
               AND COALESCE(NEW.request_payload->>'logger', '') <> 'true' THEN
                NEW.credential_ciphertext := NULL;
            END IF;
            RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_jobs_purge_terminal_credential "
        "BEFORE INSERT OR UPDATE ON jobs "
        "FOR EACH ROW EXECUTE FUNCTION jobs_purge_terminal_credential()"
    )
    # Dispara el trigger sobre lo ya terminado que aún conserva la clave.
    op.execute(
        "UPDATE jobs SET status = status "
        "WHERE credential_ciphertext IS NOT NULL "
        "AND status IN ('COMPLETO', 'FALLIDO', 'CANCELADO')"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_jobs_purge_terminal_credential ON jobs")
    op.execute("DROP FUNCTION IF EXISTS jobs_purge_terminal_credential()")
