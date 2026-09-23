"""Repositorio de sesiones administrativas (plan 05 §3.2).

Alta con UUIDv7 generado en la aplicación (sin correlativos, I-1/I-2);
revocación por servidor (logout, revocación individual o masiva) sin borrar
evidencia. Solo guarda el hash del token opaco, nunca su valor.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from central_api.models.base import new_uuid7
from central_api.models.identity import AdminSession
from central_api.repositories.base import NotFoundError
from central_api.security.admin_sessions import (
    IP_RESUMEN_MAX,
    UA_RESUMEN_MAX,
    expira_en,
    hash_session_token,
    resumir,
)


class AdminSessionRepository:
    """Altas, lecturas y revocaciones de ``admin_sessions``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def crear(
        self,
        *,
        admin_user_id: UUID,
        token: str,
        ip_inicial: str | None = None,
        user_agent: str | None = None,
    ) -> AdminSession:
        """Persiste una sesión nueva; devuelve la fila (no el token)."""
        fila = AdminSession(
            id=new_uuid7(),
            admin_user_id=admin_user_id,
            token_hash=hash_session_token(token),
            expires_at=expira_en(),
            ip_inicial=resumir(ip_inicial, IP_RESUMEN_MAX),
            user_agent_resumen=resumir(user_agent, UA_RESUMEN_MAX),
        )
        self._session.add(fila)
        await self._session.flush()
        return fila

    async def viva_por_hash(self, token_hash: str) -> AdminSession | None:
        """Localiza la sesión vigente (no revocada ni expirada) por hash."""
        filas = (
            await self._session.execute(
                text(
                    "SELECT id FROM admin_sessions WHERE token_hash = :h"
                    " AND revoked_at IS NULL"
                    " AND expires_at > CURRENT_TIMESTAMP"
                ),
                {"h": token_hash},
            )
        ).first()
        if filas is None:
            return None
        return await self._session.get(AdminSession, filas[0])

    async def tocar_actividad(self, *, sesion_id: UUID, cuando: datetime) -> None:
        """Refresca la última actividad sin reemitir la sesión."""
        await self._session.execute(
            text(
                "UPDATE admin_sessions SET last_activity_at = :t"
                " WHERE id = :id AND revoked_at IS NULL"
            ),
            {"t": cuando, "id": str(sesion_id)},
        )

    async def revocar(self, *, sesion_id: UUID) -> AdminSession:
        """Revoca una sesión (logout); conserva la fila para auditoría."""
        fila = await self._session.get(AdminSession, sesion_id)
        if fila is None:
            raise NotFoundError("sesión no encontrada")
        await self._session.execute(
            text(
                "UPDATE admin_sessions SET revoked_at = CURRENT_TIMESTAMP"
                " WHERE id = :id AND revoked_at IS NULL"
            ),
            {"id": str(sesion_id)},
        )
        await self._session.refresh(fila)
        return fila

    async def revocar_todas(
        self, *, admin_user_id: UUID, excepto: UUID | None = None
    ) -> int:
        """Revoca las sesiones vigentes de un administrador; devuelve el conteo."""
        if excepto is None:
            resultado = await self._session.execute(
                text(
                    "UPDATE admin_sessions SET revoked_at = CURRENT_TIMESTAMP"
                    " WHERE admin_user_id = :u AND revoked_at IS NULL"
                ),
                {"u": str(admin_user_id)},
            )
        else:
            resultado = await self._session.execute(
                text(
                    "UPDATE admin_sessions SET revoked_at = CURRENT_TIMESTAMP"
                    " WHERE admin_user_id = :u AND revoked_at IS NULL"
                    " AND id <> CAST(:salvo AS UUID)"
                ),
                {"u": str(admin_user_id), "salvo": str(excepto)},
            )
        return int(resultado.rowcount or 0)


__all__ = ["AdminSessionRepository"]
