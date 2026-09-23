"""Repositorio de catalogo y artefactos (plan 01 §§5.2 y 5.3).

El catalogo es dato: alta de bots/operaciones con ``effect_class`` seguro por
defecto (``EFECTO``). Los artefactos guardan ``object_key`` y metadatos,
nunca URL prefirmadas (se firman al leer).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from central_api.models.base import new_uuid7
from central_api.models.catalog import Bot, BotOperation
from central_api.models.execution import JobArtifact
from central_api.repositories.base import RepositoryError


class CatalogRepository:
    """Altas y consultas del catalogo declarativo de bots."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_bot(
        self, *, code: str, display_name: str, manifest: dict | None = None
    ) -> Bot:
        """Crea o actualiza el manifiesto de un bot sin duplicar codigo."""
        from sqlalchemy import select

        existing = (
            await self._session.execute(select(Bot).where(Bot.code == code))
        ).scalar_one_or_none()
        if existing is not None:
            existing.display_name = display_name
            existing.manifest = dict(manifest or {})
            await self._session.flush()
            return existing
        row = Bot(
            id=new_uuid7(),
            code=code,
            display_name=display_name,
            manifest=dict(manifest or {}),
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def upsert_operation(
        self,
        *,
        bot_code: str,
        code: str,
        input_schema_version: str,
        unit_cost: int = 1,
        timeout_seconds: int = 600,
        effect_class: str = "EFECTO",
        manifest: dict | None = None,
    ) -> BotOperation:
        """Crea o actualiza una operacion con salvaguarda de reintento."""
        from sqlalchemy import select

        existing = (
            await self._session.execute(
                select(BotOperation).where(
                    BotOperation.bot_code == bot_code, BotOperation.code == code
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.unit_cost = unit_cost
            existing.timeout_seconds = timeout_seconds
            existing.effect_class = effect_class
            existing.input_schema_version = input_schema_version
            existing.manifest = dict(manifest or {})
            await self._session.flush()
            return existing
        row = BotOperation(
            id=new_uuid7(),
            bot_code=bot_code,
            code=code,
            input_schema_version=input_schema_version,
            unit_cost=unit_cost,
            timeout_seconds=timeout_seconds,
            effect_class=effect_class,
            manifest=dict(manifest or {}),
        )
        self._session.add(row)
        try:
            await self._session.flush()
        except Exception as exc:
            raise RepositoryError(
                f"no se pudo registrar la operacion: {type(exc).__name__}"
            ) from exc
        return row


class ArtifactRepository:
    """Registro de artefactos (solo metadatos + ``object_key`` estable)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def register(
        self,
        *,
        job_id: UUID,
        kind: str,
        object_key: str,
        filename: str,
        content_type: str | None = None,
        size_bytes: int | None = None,
        sha256: str | None = None,
    ) -> JobArtifact:
        """Registra un artefacto subido por URL prefirmada de un solo uso."""
        row = JobArtifact(
            id=new_uuid7(),
            job_id=job_id,
            kind=kind,
            object_key=object_key,
            filename=filename,
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
        )
        self._session.add(row)
        await self._session.flush()
        return row


__all__ = ["ArtifactRepository", "CatalogRepository"]
