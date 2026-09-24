"""0014 contexto administrativo no secreto de credenciales por job."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "credential_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_jobs_credential_metadata",
        "jobs",
        "jsonb_typeof(credential_metadata) = 'object'",
    )


def downgrade() -> None:
    op.drop_constraint("ck_jobs_credential_metadata", "jobs", type_="check")
    op.drop_column("jobs", "credential_metadata")
