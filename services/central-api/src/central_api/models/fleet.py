"""Flota de workers: registro y telemetria (plan 01 §5.4).

La central solo conoce al worker por ``endpoint`` (``ip:port`` privado) y lo
contacta por HTTP. Capacidad y ejecuciones estan acotadas a 5 (tope W-2)
tambien en la base, no solo en el worker. La columna ``sealed_pubkey_pem``
guarda la RSA **publica** efimera del worker para el sobre sellado
(``central_api.security.sealed``); jamas se persiste material privado.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from central_api.models.base import PROTOCOL_VERSION, Base, new_uuid7

WORKER_STATUSES = ("REGISTRANDO", "SANO", "DEGRADADO", "DRENANDO", "CAIDO")


class Worker(Base):
    """Identidad estable del worker emitida por la central."""

    __tablename__ = "workers"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    name: Mapped[str] = mapped_column(sa.String(120), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'REGISTRANDO'")
    )
    endpoint: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    protocol_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    sealed_pubkey_pem: Mapped[str | None] = mapped_column(sa.Text)
    app_version: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    capacity: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("5")
    )
    running_jobs: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("0")
    )
    queued_jobs: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    last_heartbeat_at: Mapped[object | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    registered_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    drained_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('REGISTRANDO','SANO','DEGRADADO','DRENANDO','CAIDO')",
            name="workers_status",
        ),
        sa.CheckConstraint("capacity BETWEEN 1 AND 5", name="workers_capacity"),
        sa.CheckConstraint("running_jobs BETWEEN 0 AND 5", name="workers_running"),
        sa.CheckConstraint("queued_jobs >= 0", name="workers_queued"),
        sa.CheckConstraint(
            "running_jobs <= capacity", name="workers_running_capacity"
        ),
        sa.Index(
            "ix_workers_schedulable",
            "last_heartbeat_at",
            "running_jobs",
            postgresql_where=sa.text("status = 'SANO'"),
        ),
    )

    def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("protocol_version", PROTOCOL_VERSION)
        super().__init__(**kwargs)


class WorkerHeartbeat(Base):
    """Hecho de salud reportado por HTTP; la hora valida es la de central."""

    __tablename__ = "worker_heartbeats"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    worker_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("workers.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    running_jobs: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    queued_jobs: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    capacity: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    metrics: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    received_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('REGISTRANDO','SANO','DEGRADADO','DRENANDO','CAIDO')",
            name="heartbeats_status",
        ),
        sa.CheckConstraint(
            "running_jobs BETWEEN 0 AND 5", name="heartbeats_running"
        ),
        sa.CheckConstraint("queued_jobs >= 0", name="heartbeats_queue"),
        sa.CheckConstraint("capacity BETWEEN 1 AND 5", name="heartbeats_capacity"),
        sa.CheckConstraint(
            "running_jobs <= capacity", name="heartbeats_load"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metrics) = 'object'", name="heartbeats_metrics"
        ),
        sa.Index("ix_worker_heartbeats_worker_time", "worker_id", "received_at"),
    )


__all__ = ["Worker", "WorkerHeartbeat", "WORKER_STATUSES"]
