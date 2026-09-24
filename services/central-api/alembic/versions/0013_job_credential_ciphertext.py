"""0013 custodia RSA de credenciales fiscales por job."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("credential_ciphertext", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_jobs_credential_ciphertext",
        "jobs",
        "credential_ciphertext IS NULL OR btrim(credential_ciphertext) <> ''",
    )


def downgrade() -> None:
    op.drop_constraint("ck_jobs_credential_ciphertext", "jobs", type_="check")
    op.drop_column("jobs", "credential_ciphertext")
