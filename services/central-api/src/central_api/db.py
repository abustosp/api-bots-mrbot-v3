"""Sesiones PostgreSQL de la central (único cliente de aplicación, I-4).

Sin ``DATABASE_URL`` configurada no hay base: los routers usan el
``store.py`` en memoria como fallback de desarrollo. Con base configurada,
``get_db`` entrega una ``AsyncSession`` por request y los routers usan
``repositories`` + ``models`` en vez del store. El worker nunca importa
este módulo (invariante W-1).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from central_api.settings import get_settings


def db_configurado() -> bool:
    """Indica si hay ``DATABASE_URL`` y corresponde usar PostgreSQL."""
    return bool(get_settings().database_url)


def normalizar_dsn(url: str) -> str:
    """Traduce un DSN PostgreSQL estándar al driver realmente instalado.

    Las dependencias declaran ``psycopg`` (v3), que sirve tanto para el motor
    síncrono de Alembic como para ``create_async_engine``. Un DSN
    ``postgresql://`` (o ``postgres://``) sin driver explícito haría que
    SQLAlchemy eligiera ``psycopg2``/``asyncpg``, que no están instalados, y el
    arranque fallaría con ``ModuleNotFoundError``. Por eso se reescribe a
    ``postgresql+psycopg://``; un DSN con driver explícito se respeta.
    """
    for prefijo in ("postgresql://", "postgres://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url[len(prefijo):]
    return url


def _url_async(url: str) -> str:
    """Adapta la URL al driver asíncrono instalado."""
    return normalizar_dsn(url)


@lru_cache
def _motor():
    """Motor asíncrono compartido (uno por proceso, pool acotado)."""
    from sqlalchemy.ext.asyncio import create_async_engine

    settings = get_settings()
    return create_async_engine(
        _url_async(settings.database_url),
        pool_size=max(1, settings.database_pool_size),
        max_overflow=0,
        pool_pre_ping=True,
    )


@lru_cache
def _fabrica():
    """Fábrica de sesiones asíncronas sobre el motor compartido."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(_motor(), expire_on_commit=False)


async def get_db() -> AsyncIterator[object]:
    """Dependencia FastAPI: sesión por request o ``None`` sin base.

    Devuelve ``None`` cuando no hay ``DATABASE_URL`` para que cada router
    use el fallback en memoria de desarrollo sin romper el arranque.
    """
    if not db_configurado():
        yield None
        return
    async with _fabrica()() as sesion:
        try:
            yield sesion
            await sesion.commit()
        except Exception:
            await sesion.rollback()
            raise


@asynccontextmanager
async def nueva_sesion() -> AsyncIterator[object]:
    """Sesión para tareas de fondo (scheduler, conciliación periódica)."""
    if not db_configurado():
        raise RuntimeError("DATABASE_URL no configurada")
    async with _fabrica()() as sesion:
        try:
            yield sesion
            await sesion.commit()
        except Exception:
            await sesion.rollback()
            raise


async def cerrar_motor() -> None:
    """Libera el pool en el apagado (lifespan de ``main``)."""
    for cached in (_fabrica, _motor):
        try:
            obj = cached()
        except Exception:  # noqa: BLE001 - sin motor no hay nada que cerrar
            continue
        dispose = getattr(obj, "dispose", None)
        if dispose is not None:
            resultado = dispose()
            if hasattr(resultado, "__await__"):
                await resultado
    _fabrica.cache_clear()
    _motor.cache_clear()


__all__ = [
    "cerrar_motor",
    "db_configurado",
    "get_db",
    "normalizar_dsn",
    "nueva_sesion",
]
