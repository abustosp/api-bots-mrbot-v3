"""Auditoria inmutable de acciones administrativas y de servicio (plan 01 §5.6).

Sin FK polimorfica: la integridad del evento persiste aunque el destino se
retenga. Nunca guarda credenciales, secretos ni URL prefirmadas.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import INET, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from central_api.models.base import Base, new_uuid7


class AuditLog(Base):
    """Evento auditable con correlacion HTTP y diff saneado."""

    __tablename__ = "audit_log"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    occurred_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    actor_type: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    actor_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    target_type: Mapped[str] = mapped_column(sa.String(48), nullable=False)
    target_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True))
    request_id: Mapped[object | None] = mapped_column(PG_UUID(as_uuid=True))
    remote_addr: Mapped[object | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(sa.Text)
    # ``metadata`` es nombre reservado del API declarativa: el atributo se
    # llama ``meta`` pero la columna sigue siendo ``metadata`` (DDL intacto).
    meta: Mapped[object] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )

    __table_args__ = (
        sa.CheckConstraint(
            "actor_type IN ('ADMIN','USER','SERVICE','SYSTEM')",
            name="audit_log_actor_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'", name="audit_log_metadata"
        ),
        sa.Index("ix_audit_log_occurred_at", "occurred_at"),
        sa.Index("ix_audit_log_actor", "actor_type", "actor_id", "occurred_at"),
        sa.Index("ix_audit_log_target", "target_type", "target_id", "occurred_at"),
    )


__all__ = ["AuditLog"]
