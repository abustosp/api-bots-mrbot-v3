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

from fastapi import FastAPI, Header
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from central_api.admin import router as admin_router
from central_api.admin._common import require_admin
from central_api.api.bot_payloads import install_v1_openapi_patch
from central_api.api.router import router as public_router
from central_api.db import cerrar_motor, db_configurado
from central_api.internal.router import router as internal_router
from central_api.settings import get_settings

APP_VERSION = "0.1.0"


def _documentation_app(*, include_private: bool) -> FastAPI:
    """Construye el catálogo OpenAPI de una superficie concreta.

    La aplicación ejecutable mantiene las rutas internas y administrativas
    fuera de su esquema público. Para el catálogo privado se reutilizan los
    mismos routers, sin duplicar handlers ni contratos.
    """
    docs = FastAPI(
        title="central-api",
        version=APP_VERSION,
        description=(
            "Autenticación (cualquiera de las dos, desde Authorize en Swagger): "
            "HTTPBasic con usuario y API key, o headers email + X-API-Key como en la V1."
        ),
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    docs.include_router(public_router, prefix="/api/v3")
    if include_private:
        @docs.get("/health")
        def _health() -> dict:
            return {"status": "ok", "version": APP_VERSION}

        @docs.get("/ready")
        def _ready() -> dict:
            return {"ready": True, "version": APP_VERSION}

        docs.include_router(internal_router, prefix="/internal/v1")
        docs.include_router(admin_router, prefix="/admin")
    return install_v1_openapi_patch(docs)


def _admin_docs_html() -> HTMLResponse:
    """Swagger UI privado que reutiliza el token guardado por el panel."""
    response = get_swagger_ui_html(
        openapi_url="/admin/openapi.json",
        title="central-api administración",
    )
    html = response.body.decode("utf-8")
    interceptor = """
<script>
(function () {
  const token = window.sessionStorage.getItem("mrbot_admin_token");
  if (!token || !window.fetch) return;
  const originalFetch = window.fetch.bind(window);
  window.fetch = function (input, init) {
    const url = typeof input === "string" ? input : input.url;
    if (!url.endsWith("/admin/openapi.json")) return originalFetch(input, init);
    const options = Object.assign({}, init || {});
    options.headers = Object.assign({}, options.headers || {}, {
      Authorization: "Bearer " + token
    });
    return originalFetch(input, options);
  };
})();
</script>
"""
    return HTMLResponse(html.replace("</head>", interceptor + "</head>"))


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
    app = FastAPI(
        title="central-api",
        version=APP_VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.db_configurado = db_configurado()
    public_documentation = _documentation_app(include_private=False)
    admin_documentation = _documentation_app(include_private=True)

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok", "version": APP_VERSION}

    @app.get("/ready", include_in_schema=False)
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

    @app.get("/openapi.json", include_in_schema=False)
    def public_openapi() -> JSONResponse:
        """OpenAPI público: exclusivamente la superficie de clientes."""
        return JSONResponse(public_documentation.openapi())

    @app.get("/docs", include_in_schema=False)
    def public_docs() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url="/openapi.json",
            title="central-api clientes",
        )

    @app.get("/redoc", include_in_schema=False)
    def public_redoc() -> HTMLResponse:
        """Redoc público con el mismo catálogo exclusivo de clientes."""
        return get_redoc_html(
            openapi_url="/openapi.json",
            title="central-api clientes",
        )

    @app.get("/admin/openapi.json", include_in_schema=False)
    def admin_openapi(
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        """OpenAPI completo, protegido por el token administrativo."""
        require_admin(authorization)
        return JSONResponse(
            admin_documentation.openapi(),
            headers={"Cache-Control": "private, no-store"},
        )

    @app.get("/admin/docs", include_in_schema=False)
    def admin_docs(
        authorization: str | None = Header(default=None),
    ) -> HTMLResponse:
        # El shell HTML no contiene el esquema ni datos operativos. Se puede
        # abrir desde un navegador y el interceptor usa el token de
        # sessionStorage al solicitar /admin/openapi.json.
        if authorization:
            require_admin(authorization)
        response = _admin_docs_html()
        response.headers["Cache-Control"] = "private, no-store"
        return response

    app.include_router(public_router, prefix="/api/v3")
    # Los endpoints privados siguen funcionando, pero nunca aparecen en el
    # catálogo público. Se documentan en /admin/openapi.json.
    app.include_router(internal_router, prefix="/internal/v1", include_in_schema=False)
    # Panel privado: fuera del esquema OpenAPI público, nunca con API keys de cliente.
    app.include_router(admin_router, prefix="/admin", include_in_schema=False)
    return install_v1_openapi_patch(app)


app = create_app()
