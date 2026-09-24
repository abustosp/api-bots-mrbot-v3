"""Ejecucion: jobs, eventos, resultados y artefactos (plan 01 §5.3).

Una fila de ``jobs`` vive de creacion a estado terminal (sin copiar entre
activa e historica). El resultado variable por bot vive en
``job_results.payload`` JSONB con indice GIN; los bytes viven en S3/MinIO y
aqui solo quedan ``object_key`` y metadatos. ``protocol_version`` es entero
(``1``) segun la regla global, espejo de ``mrbot_contracts``.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from central_api.models.base import PROTOCOL_VERSION, Base, new_uuid7

JOB_STATUSES = (
    "PENDIENTE",
    "ASIGNADO",
    "CORRIENDO",
    "COMPLETO",
    "FALLIDO",
    "CANCELADO",
)
JOB_RESULTS = ("OK", "PARCIAL", "ERROR")
JOB_EVENT_TYPES = (
    "CREADO",
    "RESERVADO",
    "ASIGNADO",
    "INICIADO",
    "PROGRESO",
    "LEASE_RENOVADO",
    "REINTENTO",
    "COMPLETADO",
    "FALLIDO",
    "CANCELADO",
    "RESULTADO_RECIBIDO",
)


class Job(Base):
    """Fila unica del trabajo con ciclo de vida, lease e idempotencia."""

    __tablename__ = "jobs"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    user_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    bot: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    operation: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'PENDIENTE'")
    )
    result: Mapped[str | None] = mapped_column(sa.String(16))
    request_payload: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    # Custodia opcional de la credencial fiscal. Contiene únicamente RSA
    # ciphertext y nunca se incluye en request_payload, resultados ni auditoría.
    credential_ciphertext: Mapped[str | None] = mapped_column(sa.Text)
    idempotency_key: Mapped[str | None] = mapped_column(sa.String(128))
    priority: Mapped[int] = mapped_column(
        sa.SmallInteger, nullable=False, server_default=sa.text("100")
    )
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    assigned_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))
    started_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))
    finished_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))
    worker_id: Mapped[object | None] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("workers.id", ondelete="SET NULL"),
    )
    lease_expires_at: Mapped[object | None] = mapped_column(
        sa.DateTime(timezone=True)
    )
    attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("0")
    )
    max_attempts: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("3")
    )
    app_version: Mapped[str | None] = mapped_column(sa.String(64))
    protocol_version: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    cancel_reason: Mapped[str | None] = mapped_column(sa.Text)
    cancelled_by: Mapped[str | None] = mapped_column(sa.String(16))
    error_code: Mapped[str | None] = mapped_column(sa.String(80))
    error_message: Mapped[str | None] = mapped_column(sa.Text)

    __table_args__ = (
        sa.ForeignKeyConstraint(
            ["bot", "operation"],
            ["bot_operations.bot_code", "bot_operations.code"],
            ondelete="RESTRICT",
            name="fk_jobs_operation",
        ),
        sa.CheckConstraint(
            "status IN ('PENDIENTE','ASIGNADO','CORRIENDO','COMPLETO','FALLIDO','CANCELADO')",
            name="jobs_status",
        ),
        sa.CheckConstraint(
            "result IS NULL OR result IN ('OK','PARCIAL','ERROR')",
            name="jobs_result",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(request_payload) = 'object'", name="jobs_payload_object"
        ),
        sa.CheckConstraint(
            "credential_ciphertext IS NULL OR btrim(credential_ciphertext) <> ''",
            name="jobs_credential_ciphertext",
        ),
        sa.CheckConstraint(
            "priority BETWEEN 0 AND 1000", name="jobs_priority"
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND max_attempts BETWEEN 1 AND 20",
            name="jobs_attempts",
        ),
        sa.CheckConstraint(
            "cancelled_by IS NULL OR cancelled_by IN ('USER','ADMIN','SYSTEM')",
            name="jobs_cancelled_by",
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETO' AND result IN ('OK','PARCIAL','ERROR') AND finished_at IS NOT NULL)"
            " OR (status = 'FALLIDO' AND result = 'ERROR' AND finished_at IS NOT NULL)"
            " OR (status = 'CANCELADO' AND result IS NULL AND finished_at IS NOT NULL)"
            " OR (status IN ('PENDIENTE','ASIGNADO','CORRIENDO') AND finished_at IS NULL)",
            name="jobs_terminal_shape",
        ),
        sa.Index(
            "uq_jobs_idempotency",
            "user_id",
            "bot",
            "operation",
            "idempotency_key",
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        ),
        sa.Index(
            "ix_jobs_queue_pending",
            "priority",
            "created_at",
            "id",
            postgresql_where=sa.text("status = 'PENDIENTE'"),
        ),
        sa.Index(
            "ix_jobs_queue_live",
            "status",
            "priority",
            "created_at",
            "id",
            postgresql_where=sa.text(
                "status IN ('PENDIENTE','ASIGNADO','CORRIENDO')"
            ),
        ),
        sa.Index(
            "ix_jobs_lease_recovery",
            "lease_expires_at",
            "id",
            postgresql_where=sa.text("status IN ('ASIGNADO','CORRIENDO')"),
        ),
        sa.Index("ix_jobs_user_created", "user_id", "created_at", "id"),
        sa.Index(
            "ix_jobs_bot_finished",
            "bot",
            "finished_at",
            "id",
            postgresql_where=sa.text(
                "status IN ('COMPLETO','FALLIDO','CANCELADO')"
            ),
        ),
    )

    def __init__(self, **kwargs):  # type: ignore[no-untyped-def]
        kwargs.setdefault("protocol_version", PROTOCOL_VERSION)
        super().__init__(**kwargs)


class JobEvent(Base):
    """Hecho append-only del timeline de un job (idempotente por clave)."""

    __tablename__ = "job_events"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    job_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    event_key: Mapped[str] = mapped_column(sa.String(128), nullable=False)
    payload: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    occurred_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.UniqueConstraint(
            "job_id", "attempt", "event_key", name="uq_job_events_idempotency"
        ),
        sa.CheckConstraint("attempt >= 0", name="job_events_attempt"),
        sa.CheckConstraint(
            "event_type IN ('CREADO','RESERVADO','ASIGNADO','INICIADO','PROGRESO',"
            "'LEASE_RENOVADO','REINTENTO','COMPLETADO','FALLIDO','CANCELADO',"
            "'RESULTADO_RECIBIDO')",
            name="job_events_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="job_events_payload"
        ),
        sa.Index("ix_job_events_job_time", "job_id", "occurred_at", "id"),
    )


class JobResult(Base):
    """Resultado 1:0..1 del job; el payload variable vive en JSONB con GIN."""

    __tablename__ = "job_results"

    job_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    result: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    payload: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    summary: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    received_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.CheckConstraint("attempt > 0", name="job_results_attempt"),
        sa.CheckConstraint(
            "result IN ('OK','PARCIAL','ERROR')", name="job_results_result"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(payload) = 'object'", name="job_results_payload"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(summary) = 'object'", name="job_results_summary"
        ),
        sa.Index(
            "ix_job_results_payload_gin",
            "payload",
            postgresql_using="gin",
            postgresql_ops={"payload": "jsonb_path_ops"},
        ),
    )


class JobArtifact(Base):
    """Metadato de un objeto en S3/MinIO; nunca guarda URL prefirmadas."""

    __tablename__ = "job_artifacts"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    job_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    filename: Mapped[str] = mapped_column(sa.Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(sa.String(255))
    size_bytes: Mapped[int | None] = mapped_column(sa.BigInteger)
    sha256: Mapped[str | None] = mapped_column(sa.CHAR(64))
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    expires_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(
            "kind IN ('ARCHIVO','CAPTURA','TRACE','OTRO')",
            name="job_artifacts_kind",
        ),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes >= 0", name="job_artifacts_size"
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR sha256 ~ '^[0-9a-f]{64}$'",
            name="job_artifacts_sha256",
        ),
        sa.Index("ix_job_artifacts_job_id", "job_id", "created_at"),
        sa.Index(
            "ix_job_artifacts_expiry",
            "expires_at",
            postgresql_where=sa.text("expires_at IS NOT NULL"),
        ),
    )


__all__ = ["Job", "JobEvent", "JobResult", "JobArtifact"]
