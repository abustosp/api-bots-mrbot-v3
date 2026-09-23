"""Repositorio de auditoria append-only (plan 01 §5.6).

Solo inserta; jamas actualiza ni borra por logica de negocio. El ``metadata``
es un diff saneado sin credenciales ni secretos.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from central_api.models.audit import AuditLog
from central_api.models.base import new_uuid7
from central_api.repositories.base import assert_no_secretos


class AuditRepository:
    """Escritura de eventos auditables con correlacion HTTP."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(
        self,
        *,
        actor_type: str,
        action: str,
        target_type: str,
        actor_id: UUID | None = None,
        target_id: UUID | None = None,
        request_id: UUID | None = None,
        remote_addr: str | None = None,
        user_agent: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AuditLog:
        """Agrega un hecho de auditoria inmutable."""
        assert_no_secretos(metadata, "audit metadata")
        row = AuditLog(
            id=new_uuid7(),
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            request_id=request_id,
            remote_addr=remote_addr,  # type: ignore[arg-type]
            user_agent=user_agent,
            meta=dict(metadata or {}),
        )
        self._session.add(row)
        await self._session.flush()
        return row


__all__ = ["AuditRepository"]
