"""Catalogo declarativo de bots y operaciones (plan 01 §5.2).

El catalogo es dato versionado, no codigo de enrutamiento. ``effect_class``
gobierna el reintento: el default seguro es ``EFECTO`` (acto irreversible).
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from central_api.models.base import Base, new_uuid7


class Bot(Base):
    """Bot del registry (p. ej. ``libros_iva``)."""

    __tablename__ = "bots"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    code: Mapped[str] = mapped_column(sa.String(80), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(sa.String(160), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true()
    )
    manifest: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.CheckConstraint("btrim(code) <> ''", name="bots_code_nonblank"),
        sa.CheckConstraint(
            "jsonb_typeof(manifest) = 'object'", name="bots_manifest_object"
        ),
        sa.Index("ix_bots_enabled", "code", postgresql_where=sa.text("enabled")),
    )


class BotOperation(Base):
    """Operacion de un bot (``consulta``/``carga``) con costo y timeout."""

    __tablename__ = "bot_operations"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    bot_code: Mapped[str] = mapped_column(
        sa.String(80),
        sa.ForeignKey("bots.code", ondelete="RESTRICT"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(sa.String(80), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true()
    )
    unit_cost: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("1")
    )
    input_schema_version: Mapped[str] = mapped_column(sa.String(32), nullable=False)
    timeout_seconds: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, server_default=sa.text("600")
    )
    effect_class: Mapped[str] = mapped_column(
        sa.String(16), nullable=False, server_default=sa.text("'EFECTO'")
    )
    manifest: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )

    __table_args__ = (
        sa.UniqueConstraint("bot_code", "code", name="uq_bot_operations_code"),
        sa.CheckConstraint("unit_cost > 0", name="bot_operations_unit_cost"),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 1 AND 3600", name="bot_operations_timeout"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(manifest) = 'object'", name="bot_operations_manifest"
        ),
        sa.CheckConstraint(
            "effect_class IN ('CONSULTA', 'EFECTO')",
            name="bot_operations_effect_class",
        ),
        sa.Index(
            "ix_bot_operations_enabled",
            "bot_code",
            "code",
            postgresql_where=sa.text("enabled"),
        ),
    )


__all__ = ["Bot", "BotOperation"]
