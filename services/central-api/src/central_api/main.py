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
from central_api.api.dependencies import apply_auth_openapi
from central_api.api.router import router as public_router
from central_api.db import cerrar_motor, db_configurado
from central_api.internal.router import router as internal_router
from central_api.settings import get_settings

APP_VERSION = "0.1.0"
APP_DESCRIPTION = (
    "Servicio central para crear y consultar ejecuciones de trámites, "
    "administrar cuentas y archivos asociados, y coordinar el procesamiento "
    "de las solicitudes. Las operaciones de clientes "
    "requieren autenticación con las credenciales de la cuenta."
)
OPENAPI_TAGS = [
    {"name": "bots", "description": "Crear ejecuciones de bots y consultar las operaciones disponibles."},
    {"name": "jobs", "description": "Consultar, cancelar y revisar los resultados de las ejecuciones."},
    {"name": "cuenta", "description": "Consultar y administrar la cuenta del usuario autenticado."},
    {"name": "uploads", "description": "Gestionar la recepción de archivos de entrada asociados a solicitudes."},
    {"name": "utilidades", "description": "Consultar información auxiliar y funciones de apoyo de la API."},
    {"name": "seguridad", "description": "Consultar información pública necesaria para proteger las solicitudes."},
    {"name": "apoc", "description": "Consultar información de operaciones vinculadas a APoc."},
    {"name": "Scrapping PDF", "description": "Consultar y procesar documentos PDF enviados para extracción de información."},
    {"name": "VEP", "description": "Preparar y consultar información de volantes electrónicos de pago."},
    {"name": "usuarios", "description": "Gestionar usuarios y sus datos de acceso."},
    {"name": "schemas", "description": "Consultar los campos y ejemplos disponibles para una operación."},
    {"name": "internal-workers", "description": "Registrar workers y mantener actualizada su disponibilidad."},
    {"name": "internal-events", "description": "Recibir avisos de avance y cambios de estado de las ejecuciones."},
    {"name": "internal-jobs", "description": "Recibir resultados finales y archivos generados por los workers."},
    {"name": "internal-artifacts", "description": "Autorizar subidas de archivos y recibir acuses de cancelación."},
    {"name": "admin", "description": "Administrar la configuración general del servicio."},
    {"name": "admin-workers", "description": "Administrar y monitorear las instancias de worker."},
    {"name": "admin-users", "description": "Administrar usuarios y permisos de acceso."},
    {"name": "admin-reporting", "description": "Consultar informes de actividad y resultados."},
    {"name": "admin-retention", "description": "Consultar y administrar la conservación de datos."},
    {"name": "admin-jobs", "description": "Inspeccionar y administrar ejecuciones desde el panel."},
    {"name": "admin-fleet", "description": "Consultar y administrar el conjunto de workers disponibles."},
    {"name": "admin-billing", "description": "Consultar y administrar saldos, reservas y movimientos."},
    {"name": "admin-audit", "description": "Consultar el historial de acciones administrativas."},
    {"name": "admin-autoscale", "description": "Consultar y ajustar la capacidad disponible según la demanda."},
]


def _documentation_app(*, include_private: bool) -> FastAPI:
    """Construye el catálogo OpenAPI de una superficie concreta.

    La aplicación ejecutable mantiene las rutas internas y administrativas
    fuera de su esquema público. Para el catálogo privado se reutilizan los
    mismos routers, sin duplicar handlers ni contratos.
    """
    docs = FastAPI(
        title="API central de ejecución de trámites",
        version=APP_VERSION,
        description=(
            APP_DESCRIPTION
            + " Para las operaciones de clientes se admite autenticación con "
            "usuario y clave de API, o con correo electrónico y clave de API. "
            "Ingrese las credenciales desde el botón Authorize de esta página."
        ),
        openapi_tags=OPENAPI_TAGS,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    docs.include_router(public_router, prefix="/api/v3")
    if include_private:
        @docs.get(
            "/health",
            summary="Comprobar que la API está activa",
            description="Confirma que el proceso de la API responde. No requiere una cuenta.",
            responses={200: {"description": "Proceso activo.", "content": {"application/json": {"example": {"status": "ok", "version": APP_VERSION}}}}},
            tags=["admin"],
        )
        def _health() -> dict:
            """Devuelve una señal básica de disponibilidad del proceso."""
            return {"status": "ok", "version": APP_VERSION}

        @docs.get(
            "/ready",
            summary="Comprobar la disponibilidad para procesar solicitudes",
            description="Informa si la API cuenta con la configuración necesaria para atender solicitudes.",
            responses={200: {"description": "Estado actual de disponibilidad.", "content": {"application/json": {"examples": {
                "disponible": {"summary": "Servicio listo", "value": {"ready": True, "version": APP_VERSION}},
                "no_disponible": {"summary": "Falta configuración", "value": {"ready": False, "reason": "DATABASE_URL no configurada"}},
            }}}}},
            tags=["admin"],
        )
        def _ready() -> dict:
            """Describe si la API tiene los recursos requeridos para operar."""
            return {"ready": True, "version": APP_VERSION}

        docs.include_router(internal_router, prefix="/internal/v1")
        docs.include_router(admin_router, prefix="/admin")
    patched = install_v1_openapi_patch(docs)
    base_openapi = patched.openapi

    def openapi_with_auth() -> dict:
        return apply_auth_openapi(base_openapi())

    patched.openapi = openapi_with_auth
    return patched


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


# Swagger UI solo dibuja un campo por esquema apiKey. Para el método V1
# (email + X-API-Key) se agrega el campo email dentro del mismo recuadro de
# Authorize y un requestInterceptor que envía ese header junto con la key.
_PUBLIC_DOCS_V1_AUTH = """
<script>
(function () {
  const KEY = "mrbot_v1_email";
  const getEmail = () => window.sessionStorage.getItem(KEY) || "";
  window.__mrbotRequestInterceptor = function (req) {
    const headers = req.headers || {};
    const hasKey = Object.keys(headers).some((h) => h.toLowerCase() === "x-api-key");
    const email = getEmail();
    if (hasKey && email) headers["email"] = email;
    req.headers = headers;
    return req;
  };
  function inject() {
    document.querySelectorAll(".auth-container").forEach((box) => {
      if (box.querySelector("input[name=mrbot-v1-email]")) return;
      const title = box.querySelector("h4");
      if (!title || !/\\bV1\\b/.test(title.textContent || "")) return;
      const keyInput = box.querySelector("input[type=text], input[type=password]");
      if (!keyInput) return;
      const wrap = document.createElement("div");
      wrap.className = "wrapper";
      wrap.innerHTML = '<label>email (usuario):</label><section class=""><input name="mrbot-v1-email" type="text" autocomplete="username" placeholder="usuario@example.com"></section>';
      const input = wrap.querySelector("input");
      input.value = getEmail();
      input.addEventListener("input", () => window.sessionStorage.setItem(KEY, input.value.trim()));
      const keyWrapper = keyInput.closest(".wrapper") || keyInput.parentElement;
      keyWrapper.parentElement.insertBefore(wrap, keyWrapper);
    });
  }
  new MutationObserver(inject).observe(document.documentElement, { childList: true, subtree: true });
})();
</script>
"""


def _public_docs_html() -> HTMLResponse:
    """Swagger UI público con los dos métodos: HTTPBasic y V1 (email + key)."""
    response = get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="central-api clientes",
        swagger_ui_parameters={"persistAuthorization": True},
    )
    html = response.body.decode("utf-8")
    html = html.replace("</head>", _PUBLIC_DOCS_V1_AUTH + "</head>")
    html = html.replace(
        "SwaggerUIBundle({",
        "SwaggerUIBundle({\n    requestInterceptor: (req) => window.__mrbotRequestInterceptor(req),",
        1,
    )
    return HTMLResponse(html)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ciclo de vida: reserva el motor DB, arranca el scheduler y libera al apagar.

    El bucle del scheduler (claim + dispatch a workers) corre como tarea
    supervisada por réplica mientras ``scheduler_enabled`` esté activo; sin
    él los jobs quedarían PENDIENTE para siempre. Las migraciones siguen
    siendo un job one-off externo, nunca parte del arranque.
    """
    import asyncio

    # Ninguna URL (prefirmada o no) puede salir en los logs: solo bucket/clave.
    from mrbot_contracts.log_redaction import instalar_redaccion_de_urls

    instalar_redaccion_de_urls()

    app.state.db_configurado = db_configurado()
    if app.state.db_configurado:
        # El catálogo debe existir para que la FK fk_jobs_operation acepte
        # jobs. El upsert es idempotente y barato; si falla (p. ej. esquema
        # aún sin migrar) no bloquea el arranque y se reintenta en el próximo.
        import logging

        try:
            from central_api.migrate import seed_catalog_async

            bots, ops = await seed_catalog_async()
            logging.getLogger(__name__).info(
                "catálogo sembrado al arrancar: %s bots, %s operaciones", bots, ops
            )
        except Exception as exc:  # noqa: BLE001 - no bloquear el arranque
            logging.getLogger(__name__).warning(
                "no se pudo sembrar el catálogo al arrancar: %s", type(exc).__name__
            )
    tarea_scheduler: asyncio.Task | None = None
    tarea_autoscaler: asyncio.Task | None = None
    from central_api.services.apoc_base import refresh_loop as apoc_refresh_loop

    tarea_apoc = asyncio.create_task(apoc_refresh_loop())
    if get_settings().scheduler_enabled:
        from central_api.scheduler.loop import scheduler_loop

        tarea_scheduler = asyncio.create_task(scheduler_loop())
    if get_settings().autoscale_enabled:
        from central_api.autoscaler.controller import start_loop_task

        tarea_autoscaler = start_loop_task()
    try:
        yield
    finally:
        tarea_apoc.cancel()
        try:
            await tarea_apoc
        except (asyncio.CancelledError, Exception):
            pass
        if tarea_scheduler is not None:
            tarea_scheduler.cancel()
            try:
                await tarea_scheduler
            except (asyncio.CancelledError, Exception):
                pass
        # Cancela tareas del controller/drain monitor, pero nunca termina EC2.
        from central_api.autoscaler.controller import shutdown_tasks

        await shutdown_tasks()
        if app.state.db_configurado:
            await cerrar_motor()


def create_app() -> FastAPI:
    app = FastAPI(
        title="API central de ejecución de trámites",
        version=APP_VERSION,
        description=APP_DESCRIPTION,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.db_configurado = db_configurado()
    public_documentation = _documentation_app(include_private=False)
    admin_documentation = _documentation_app(include_private=True)

    @app.get(
        "/health",
        include_in_schema=False,
        summary="Comprobar que la API está activa",
        description="Confirma que el proceso de la API responde sin comprobar sus dependencias.",
        responses={200: {"description": "Proceso activo.", "content": {"application/json": {"example": {"status": "ok", "version": APP_VERSION}}}}},
    )
    def health() -> dict:
        """Devuelve una señal básica de disponibilidad del proceso."""
        return {"status": "ok", "version": APP_VERSION}

    @app.get(
        "/ready",
        include_in_schema=False,
        summary="Comprobar disponibilidad para procesar solicitudes",
        description="Informa si la configuración y los datos necesarios para operar están disponibles.",
        responses={200: {"description": "Estado de disponibilidad; una respuesta no lista incluye una causa resumida.", "content": {"application/json": {"examples": {
            "lista": {"summary": "Servicio listo", "value": {"ready": True, "version": APP_VERSION, "esquema": "20261012_a1b2c3d4"}},
            "pendiente": {"summary": "Configuración pendiente", "value": {"ready": False, "reason": "DATABASE_URL no configurada"}},
        }}}}},
    )
    def ready() -> dict:
        """Comprueba si los recursos necesarios están disponibles."""
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

    @app.get(
        "/openapi.json",
        include_in_schema=False,
        summary="Consultar el catálogo de operaciones para clientes",
        description="Devuelve la descripción de las operaciones disponibles para clientes.",
        responses={200: {"description": "Catálogo de operaciones para clientes."}},
    )
    def public_openapi() -> JSONResponse:
        """OpenAPI público: exclusivamente la superficie de clientes."""
        return JSONResponse(public_documentation.openapi())

    @app.get(
        "/docs",
        include_in_schema=False,
        summary="Abrir la documentación interactiva para clientes",
        description="Presenta el catálogo de operaciones para clientes y permite explorar sus solicitudes.",
        responses={200: {"description": "Página de documentación interactiva."}},
    )
    def public_docs() -> HTMLResponse:
        """Muestra la documentación interactiva pública."""
        return _public_docs_html()

    @app.get(
        "/redoc",
        include_in_schema=False,
        summary="Abrir la referencia de operaciones para clientes",
        description="Presenta una referencia de lectura del catálogo público de operaciones.",
        responses={200: {"description": "Página de referencia de operaciones."}},
    )
    def public_redoc() -> HTMLResponse:
        """Redoc público con el mismo catálogo exclusivo de clientes."""
        return get_redoc_html(
            openapi_url="/openapi.json",
            title="central-api clientes",
        )

    @app.get(
        "/admin/openapi.json",
        include_in_schema=False,
        summary="Consultar el catálogo administrativo",
        description="Devuelve las operaciones administrativas e internas tras validar la credencial administrativa.",
        responses={
            200: {"description": "Catálogo administrativo autorizado."},
            401: {"description": "No se envió una credencial administrativa. Incluya una cabecera `Authorization: Bearer` válida.", "content": {"application/json": {"example": {"detail": "Falta Bearer [REDACTED]"}}}},
            403: {"description": "La credencial no coincide o el acceso administrativo no está configurado. Revise la credencial o solicite que se habilite el acceso.", "content": {"application/json": {"examples": {
                "invalida": {"summary": "Credencial inválida", "value": {"detail": "ADMIN_TOKEN inválido"}},
                "no_configurada": {"summary": "Acceso no habilitado", "value": {"detail": "ADMIN_TOKEN no configurado"}},
            }}}},
        },
    )
    def admin_openapi(
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        """OpenAPI completo, protegido por el token administrativo."""
        require_admin(authorization)
        return JSONResponse(
            admin_documentation.openapi(),
            headers={"Cache-Control": "private, no-store"},
        )

    @app.get(
        "/admin/docs",
        include_in_schema=False,
        summary="Abrir la documentación administrativa",
        description="Muestra la interfaz de referencia; la consulta del catálogo requiere una credencial administrativa válida.",
        responses={
            200: {"description": "Página de documentación administrativa."},
            401: {"description": "Se envió una cabecera administrativa sin credencial. Incluya una credencial válida o abra la página sin cabecera.", "content": {"application/json": {"example": {"detail": "Falta Bearer [REDACTED]"}}}},
            403: {"description": "La credencial enviada no coincide o el acceso administrativo no está configurado. Revise la credencial o solicite que se habilite el acceso.", "content": {"application/json": {"example": {"detail": "ADMIN_TOKEN inválido"}}}},
        },
    )
    def admin_docs(
        authorization: str | None = Header(default=None),
    ) -> HTMLResponse:
        """Muestra la interfaz de referencia para operaciones administrativas."""
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
