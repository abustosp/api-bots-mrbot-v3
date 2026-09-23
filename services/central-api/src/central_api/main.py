"""Punto de entrada de central-api (esqueleto F0-F2).

- ``GET /health``: vivacidad del proceso, sin dependencias.
- ``GET /ready``: disponibilidad real (base de datos + versión de esquema).
- ``/api/v3``: API pública (crea jobs con 202 + job_id, S-1).
- ``/internal/v1``: API privada para workers (register, heartbeat, result).
- ``/admin``: administración (panel de workers: alta de IPs + monitoreo).

PostgreSQL es el único source of truth cuando ``DATABASE_URL`` está
configurada (ver ``central_api.db``): los routers usan ``repositories`` +
``models`` y ``store.py`` queda como fallback de desarrollo. Las
migraciones corren como job one-off (``python -m central_api.migrate``,
nunca en el arranque).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from central_api.admin import router as admin_router
from central_api.api.router import router as public_router
from central_api.db import cerrar_motor, db_configurado
from central_api.internal.router import router as internal_router
from central_api.settings import get_settings

APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida: reserva el motor DB, arranca el scheduler y libera al apagar.

    El bucle del scheduler (claim + dispatch a workers) corre como tarea
    supervisada por réplica mientras ``scheduler_enabled`` esté activo; sin
    él los jobs quedarían PENDIENTE para siempre. Las migraciones siguen
    siendo un job one-off externo, nunca parte del arranque.
    """
    import asyncio

    app.state.db_configurado = db_configurado()
    tarea_scheduler: asyncio.Task | None = None
    if get_settings().scheduler_enabled:
        from central_api.scheduler.loop import scheduler_loop

        tarea_scheduler = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        if tarea_scheduler is not None:
            tarea_scheduler.cancel()
            try:
                await tarea_scheduler
            except (asyncio.CancelledError, Exception):
                pass
        if app.state.db_configurado:
            await cerrar_motor()


def create_app() -> FastAPI:
    app = FastAPI(title="central-api", version=APP_VERSION, lifespan=lifespan)
    app.state.db_configurado = db_configurado()

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": APP_VERSION}

    @app.get("/ready")
    def ready() -> dict:
        settings = get_settings()
        if not settings.database_url:
            return {"ready": False, "reason": "DATABASE_URL no configurada"}
        try:
            import sqlalchemy

            engine = sqlalchemy.create_engine(
                settings.database_url, pool_size=1, max_overflow=0
            )
            with engine.connect() as conn:
                conn.execute(sqlalchemy.text("SELECT 1"))
                try:
                    version = conn.execute(
                        sqlalchemy.text("SELECT version_num FROM alembic_version")
                    ).scalar()
                except Exception:  # noqa: BLE001 - sin tabla aún, solo vivacidad
                    version = None
            engine.dispose()
        except Exception as exc:  # noqa: BLE001 - el motivo no expone secretos
            return {"ready": False, "reason": type(exc).__name__}
        return {"ready": True, "version": APP_VERSION,
                "esquema": version}

    app.include_router(public_router, prefix="/api/v3")
    app.include_router(internal_router, prefix="/internal/v1")
    # Panel privado: fuera del esquema OpenAPI público, nunca con API keys de cliente.
    app.include_router(admin_router, prefix="/admin", include_in_schema=False)
    return app


app = create_app()
