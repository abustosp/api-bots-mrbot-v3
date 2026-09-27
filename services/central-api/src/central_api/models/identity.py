"""Identidad: usuarios, claves API y administradores (plan 01 §5.1).

``users`` contiene exactamente ``id`` (UUIDv4), ``email`` y ``habilitado``:
sin ``fecha_ultimo_reset``/``created_at``/``updated_at``, sin contadores de
cuota y sin secretos en claro. ``api_keys`` contiene HMAC de verificación y,
para claves emitidas tras 0017, un ciphertext RSA híbrido recuperable.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from central_api.models.base import Base, new_uuid4, new_uuid7


class User(Base):
    """Cuenta cliente identificada por UUIDv4 no enumerable."""

    __tablename__ = "users"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid4
    )
    email: Mapped[str] = mapped_column(sa.Text, nullable=False)
    habilitado: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.false()
    )

    __table_args__ = (
        sa.CheckConstraint("btrim(email) <> ''", name="users_email_nonblank"),
        sa.Index("uq_users_email_lower", sa.func.lower(email), unique=True),
    )


class ApiKey(Base):
    """Clave rotada con HMAC de verificación y ciphertext recuperable opcional."""

    __tablename__ = "api_keys"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    user_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Únicos solo entre claves activas (0018): un valor revocado puede volver
    # a asignarse. Ver índices parciales en ``__table_args__``.
    key_prefix: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    verifier_hmac: Mapped[str] = mapped_column(sa.Text, nullable=False)
    encrypted_value: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    label: Mapped[str | None] = mapped_column(sa.String(120))
    scopes: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    expires_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))
    revoked_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))
    replaces_key_id: Mapped[object | None] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("api_keys.id", ondelete="RESTRICT"),
    )
    last_used_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.CheckConstraint(
            "encrypted_value IS NULL OR btrim(encrypted_value) <> ''",
            name="api_keys_encrypted_value",
        ),
        sa.CheckConstraint("jsonb_typeof(scopes) = 'array'", name="api_keys_scopes"),
        sa.CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at",
            name="api_keys_lifetime",
        ),
        sa.Index("ix_api_keys_user_id", "user_id"),
        sa.Index("ix_api_keys_key_prefix", "key_prefix"),
        sa.Index(
            "uq_api_keys_active_prefix",
            "key_prefix",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
        sa.Index(
            "uq_api_keys_active_verifier",
            "verifier_hmac",
            unique=True,
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
    )


class AdminSession(Base):
    """Sesion administrativa revocable (plan 05 §3.2).

    PK UUIDv7 generado en la aplicacion: sin ``serial``, ``identity`` ni
    secuencias (I-1/I-2). La cookie solo porta el identificador opaco; aqui
    vive su hash SHA-256 (nunca el token en claro), mas emision, expiracion,
    ultima actividad, IP inicial, user agent resumido y ``revoked_at``.
    """

    __tablename__ = "admin_sessions"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    admin_user_id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True),
        sa.ForeignKey("admin_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(sa.Text, nullable=False, unique=True)
    issued_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    expires_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    last_activity_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )
    ip_inicial: Mapped[str | None] = mapped_column(sa.Text)
    user_agent_resumen: Mapped[str | None] = mapped_column(sa.String(256))
    revoked_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(
            "expires_at > issued_at", name="admin_sessions_lifetime"
        ),
        sa.CheckConstraint(
            "btrim(token_hash) <> ''", name="admin_sessions_token_nonblank"
        ),
        sa.Index("ix_admin_sessions_user_id", "admin_user_id"),
        sa.Index(
            "ix_admin_sessions_active",
            "token_hash",
            postgresql_where=sa.text("revoked_at IS NULL"),
        ),
    )


class AdminUser(Base):
    """Principal administrativo; sustituye al admin global del ``.env``."""

    __tablename__ = "admin_users"

    id: Mapped[object] = mapped_column(
        PG_UUID(as_uuid=True), primary_key=True, default=new_uuid7
    )
    email: Mapped[str] = mapped_column(sa.Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(sa.Text, nullable=False)
    roles: Mapped[object] = mapped_column(
        JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")
    )
    habilitado: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, server_default=sa.true()
    )
    mfa_secret_ref: Mapped[str | None] = mapped_column(sa.Text)
    last_login_at: Mapped[object | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[object] = mapped_column(
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.func.current_timestamp(),
    )

    __table_args__ = (
        sa.CheckConstraint("jsonb_typeof(roles) = 'array'", name="admin_roles"),
        sa.Index(
            "uq_admin_users_email_lower", sa.func.lower(email), unique=True
        ),
    )


__all__ = ["User", "ApiKey", "AdminUser", "AdminSession"]
