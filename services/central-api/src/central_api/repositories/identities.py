"""Repositorio de identidad: usuarios y claves API (plan 01 §§4.3 y 5.1).

Autenticar solo identifica, valida vigencia y ``habilitado``: nunca escribe
cuotas ni abre periodos. En ``api_keys`` solo vive HMAC versionado y ciphertext
cifrado; la comparación del verificador ocurre en tiempo constante.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from central_api.models.base import new_uuid4, new_uuid7
from central_api.models.identity import ApiKey, User
from central_api.repositories.base import NotFoundError, RepositoryError


class IdentityRepository:
    """Altas y consultas de ``users`` y ``api_keys``."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_user(self, *, email: str) -> User:
        """Crea un usuario con UUIDv4; el email se normaliza a minusculas."""
        normalized = email.strip().lower()
        if not normalized:
            raise RepositoryError("email vacio")
        user = User(id=new_uuid4(), email=normalized, habilitado=False)
        self._session.add(user)
        try:
            await self._session.flush()
        except Exception as exc:
            raise RepositoryError(
                f"no se pudo crear el usuario: {type(exc).__name__}"
            ) from exc
        return user

    async def issue_api_key(
        self,
        *,
        user_id: UUID,
        key_prefix: str,
        verifier_hmac: str,
        encrypted_value: str,
        label: str | None = None,
        replaces_key_id: UUID | None = None,
    ) -> ApiKey:
        """Emite una clave con HMAC y ciphertext, nunca el secreto en claro."""
        row = ApiKey(
            id=new_uuid7(),
            user_id=user_id,
            key_prefix=key_prefix,
            verifier_hmac=verifier_hmac,
            encrypted_value=encrypted_value,
            label=label,
            replaces_key_id=replaces_key_id,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def revoke_api_key(self, *, key_id: UUID) -> ApiKey:
        """Revoca sin borrar evidencia (conserva trazabilidad de rotacion)."""
        row = await self._session.get(ApiKey, key_id)
        if row is None:
            raise NotFoundError("clave no encontrada")
        await self._session.execute(
            text("UPDATE api_keys SET revoked_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"id": str(key_id)},
        )
        await self._session.refresh(row)
        return row

    async def active_key_by_prefix(self, prefix: str) -> ApiKey | None:
        """Localiza la clave activa por prefijo para luego comparar HMAC."""
        rows = (
            await self._session.execute(
                text(
                    "SELECT id FROM api_keys WHERE key_prefix = :p"
                    " AND revoked_at IS NULL"
                    " AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)"
                ),
                {"p": prefix},
            )
        ).first()
        if rows is None:
            return None
        return await self._session.get(ApiKey, rows[0])


__all__ = ["IdentityRepository"]
