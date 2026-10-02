"""0019 amplía el máximo de capacidad y carga por worker a 10."""

from __future__ import annotations

from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Los nombres reflejan NAMING_CONVENTION de models/base.py:
    # ck_<tabla>_<constraint_name>.
    op.drop_constraint(op.f("ck_workers_workers_capacity"), "workers", type_="check")
    op.drop_constraint(op.f("ck_workers_workers_running"), "workers", type_="check")
    op.drop_constraint(
        op.f("ck_worker_heartbeats_heartbeats_capacity"),
        "worker_heartbeats",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_worker_heartbeats_heartbeats_running"),
        "worker_heartbeats",
        type_="check",
    )
    op.create_check_constraint(
        "workers_capacity", "workers", "capacity BETWEEN 1 AND 10"
    )
    op.create_check_constraint(
        "workers_running", "workers", "running_jobs BETWEEN 0 AND 10"
    )
    op.create_check_constraint(
        "heartbeats_capacity", "worker_heartbeats", "capacity BETWEEN 1 AND 10"
    )
    op.create_check_constraint(
        "heartbeats_running", "worker_heartbeats", "running_jobs BETWEEN 0 AND 10"
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_workers_workers_capacity"), "workers", type_="check")
    op.drop_constraint(op.f("ck_workers_workers_running"), "workers", type_="check")
    op.drop_constraint(
        op.f("ck_worker_heartbeats_heartbeats_capacity"),
        "worker_heartbeats",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_worker_heartbeats_heartbeats_running"),
        "worker_heartbeats",
        type_="check",
    )
    op.create_check_constraint(
        "workers_capacity", "workers", "capacity BETWEEN 1 AND 5"
    )
    op.create_check_constraint(
        "workers_running", "workers", "running_jobs BETWEEN 0 AND 5"
    )
    op.create_check_constraint(
        "heartbeats_capacity", "worker_heartbeats", "capacity BETWEEN 1 AND 5"
    )
    op.create_check_constraint(
        "heartbeats_running", "worker_heartbeats", "running_jobs BETWEEN 0 AND 5"
    )
