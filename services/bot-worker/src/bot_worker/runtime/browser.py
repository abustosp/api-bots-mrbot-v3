"""Fabrica de navegador del worker: Chromium via Playwright.

Solo este modulo conoce a Playwright. Los plugins reciben la fabrica
inyectada en ``BotRuntime`` y nunca importan ``playwright`` ni lanzan
Chromium por su cuenta (asi el worker cuenta, cierra y contabiliza cada
instancia). ``headless`` siempre es ``True``: ningun sobre puede
desactivarlo. El proxy llega ya construido como ``ProxyConfig``; sus
credenciales nunca se loguean.

El import de ``playwright`` es perezoso (dentro de cada apertura) para
que el paquete siga siendo importable en entornos sin navegador, como
los tests unitarios del supervisor o de la API.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

log = logging.getLogger("bot_worker.browser")


def _solvers_from_profiles(profiles: Any) -> dict[str, Any]:
    """Resolvedores por proveedor desde el perfil sellado (sin entorno)."""
    from bot_worker.runtime.captcha import CaptchaSolver

    profiles = profiles if isinstance(profiles, dict) else {}
    out: dict[str, Any] = {}
    for provider in ("arca", "srt"):
        solver = CaptchaSolver.from_profile(provider, profiles)
        if solver.enabled:
            out[provider] = solver
    return out


class BrowserUnavailableError(Exception):
    """Playwright/Chromium no esta disponible en esta imagen o entorno."""


#: Stealth portado de la V1 (``api-bots-mrbot/app/utils/arca_login.py``):
#: argumentos de lanzamiento, contexto con identidad de Windows en espanol y
#: un init script que evita las senales mas obvias de automatizacion. El
#: navegador sigue siendo headless y no se declara otra identidad que la real
#: del equipo: solo se dejan de publicar las marcas de CDP.
STEALTH_LAUNCH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-infobars",
)
STEALTH_CONTEXT_DEFAULTS: dict[str, Any] = {
    "locale": "es-AR",
    "timezone_id": "America/Argentina/Buenos_Aires",
    "viewport": {"width": 1366, "height": 768},
    "user_agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
}
STEALTH_INIT_SCRIPT = """
(() => {
    const override = (obj, prop, value) => {
        try { Object.defineProperty(obj, prop, { get: () => value, configurable: true }); } catch (_) {}
    };
    override(Navigator.prototype, 'webdriver', undefined);
    override(Navigator.prototype, 'languages', ['es-AR', 'es', 'en-US', 'en']);
    override(Navigator.prototype, 'platform', 'Win32');
    override(Navigator.prototype, 'hardwareConcurrency', 8);
    override(Navigator.prototype, 'deviceMemory', 8);
    override(Navigator.prototype, 'plugins', [1, 2, 3, 4, 5]);
    try {
        if (!window.chrome) {
            Object.defineProperty(window, 'chrome', { value: { runtime: {} }, configurable: true });
        }
    } catch (_) {}
    try {
        const originalQuery = window.navigator.permissions?.query;
        if (originalQuery) {
            window.navigator.permissions.query = (parameters) => {
                if (parameters?.name === 'notifications') {
                    return Promise.resolve({ state: Notification.permission });
                }
                return originalQuery.call(window.navigator.permissions, parameters);
            };
        }
    } catch (_) {}
    try {
        const webglGetParameter = WebGLRenderingContext.prototype.getParameter;
        WebGLRenderingContext.prototype.getParameter = function (parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return webglGetParameter.call(this, parameter);
        };
    } catch (_) {}
    try {
        const webgl2GetParameter = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function (parameter) {
            if (parameter === 37445) return 'Intel Inc.';
            if (parameter === 37446) return 'Intel Iris OpenGL Engine';
            return webgl2GetParameter.call(this, parameter);
        };
    } catch (_) {}
})();
"""


class BrowserCrashedError(Exception):
    """El driver o el proceso Chromium se perdio en pleno vuelo."""


def _playwright_proxy_dict(proxy: Any) -> dict[str, str] | None:
    """Convierte ``ProxyConfig`` al dict que espera Playwright."""
    if proxy is None:
        return None
    server = proxy.host
    if not server:
        return None
    if "://" not in server:
        server = f"http://{server}"
    out: dict[str, str] = {"server": server}
    if proxy.username:
        out["username"] = proxy.username
    if proxy.password:
        out["password"] = proxy.password
    return out


def _entorno_navegador() -> dict[str, str]:
    """Entorno del proceso con los directorios XDG en un tmpfs escribible.

    El binario completo de Chromium escribe configuración y caché fuera del
    perfil temporal; con la raíz de solo lectura (el worker la usa) y un HOME
    sin permiso de escritura falla al lanzar. Playwright ya ubica el perfil en
    ``/tmp``: acá se hace lo mismo con el resto, sin tocar la identidad del
    navegador.
    """
    import os
    import tempfile
    from pathlib import Path

    base = Path(tempfile.gettempdir()) / "mrbot-browser"
    entorno = dict(os.environ)
    for variable, subdirectorio in (
        ("XDG_CONFIG_HOME", "config"),
        ("XDG_CACHE_HOME", "cache"),
        ("XDG_DATA_HOME", "data"),
    ):
        destino = base / subdirectorio
        try:
            destino.mkdir(parents=True, exist_ok=True)
        except OSError:  # pragma: no cover - sin permiso se deja el entorno igual
            continue
        entorno[variable] = str(destino)
    return entorno


class PlaywrightBrowserFactory:
    """Lanza y cierra Chromium por sesion, con contabilidad interna."""

    def __init__(
        self,
        proxy: Any = None,
        headless: bool = True,
        captcha_profiles: Any = None,
        stealth: bool = False,
        canal: str = "",
    ) -> None:
        self._proxy = proxy
        self._headless = True if headless else True  # headless no negociable
        self._stealth = bool(stealth)
        self._canal = str(canal or "")
        self._solvers = _solvers_from_profiles(captcha_profiles)
        self.launch_count = 0
        self.crash_count = 0
        self.active_sessions = 0

    @property
    def proxy(self) -> Any:
        """Proxy inyectado (``repr`` oculto en ``ProxyConfig``)."""
        return self._proxy

    @asynccontextmanager
    async def new_context(
        self, **_: Any
    ) -> AsyncIterator[tuple[Any, Any]]:
        """Abre ``(browser, context)`` y los cierra al salir, siempre."""
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserUnavailableError(
                "playwright no instalado en esta imagen"
            ) from exc
        playwright = await async_playwright().start()
        browser = None
        context = None
        self.active_sessions += 1
        try:
            try:
                opciones: dict[str, Any] = {
                    "headless": self._headless,
                    "proxy": _playwright_proxy_dict(self._proxy),
                }
                if self._canal:
                    opciones["channel"] = self._canal
                    # El binario completo necesita directorios escribibles.
                    opciones["env"] = _entorno_navegador()
                if self._stealth:
                    opciones["args"] = list(STEALTH_LAUNCH_ARGS)
                browser = await playwright.chromium.launch(**opciones)
            except Exception as exc:
                raise BrowserCrashedError(
                    "no se pudo lanzar chromium"
                ) from exc
            self.launch_count += 1
            try:
                contexto: dict[str, Any] = {"accept_downloads": True}
                if self._stealth:
                    contexto.update(STEALTH_CONTEXT_DEFAULTS)
                context = await browser.new_context(**contexto)
                if self._stealth:
                    await context.add_init_script(STEALTH_INIT_SCRIPT)
            except Exception as exc:
                raise BrowserCrashedError(
                    "no se pudo crear el contexto"
                ) from exc
            yield browser, context
        finally:
            self.active_sessions = max(0, self.active_sessions - 1)
            if context is not None:
                try:
                    await context.close()
                except Exception:
                    log.debug("cierre de contexto con error (ignorado)")
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    self.crash_count += 1
                    log.debug("cierre de browser con error (contado)")
            try:
                await playwright.stop()
            except Exception:
                log.debug("playwright.stop con error (ignorado)")

    def arca_session(self, credentials: Any, **kwargs: Any) -> Any:
        """Sesion fiscal ligada a un contexto Chromium propio.

        Devuelve un context manager asincrono que cierra browser,
        contexto y driver al salir, incluso ante cancelacion.
        """
        from bot_worker.runtime.arca_login import ArcaLoginError

        if credentials is None:
            raise ArcaLoginError("el plugin requiere credenciales")
        factory = self
        captcha = kwargs.get("captcha", self._solvers.get("arca"))

        @asynccontextmanager
        async def _open() -> AsyncIterator[Any]:
            from bot_worker.runtime.arca_login import ArcaSession

            async with factory.new_context() as (_, context):
                page = await context.new_page()
                session = ArcaSession(
                    credentials, captcha, page=page, context=context
                )
                try:
                    await session.login()
                    yield session
                finally:
                    await session.close()

        return _open()


class _StubPage:
    """Página falsa: solo registra la navegación pedida, sin red."""

    def __init__(self) -> None:
        self.visitas: list[str] = []

    async def goto(self, url: str, **_: Any) -> None:
        """Anota la URL sin abrir ningún socket."""
        self.visitas.append(url)


class _StubContext:
    """Contexto falso con la superficie mínima que usan los plugins."""

    def __init__(self) -> None:
        self.closed = False

    async def new_page(self) -> _StubPage:
        """Devuelve una página falsa (sin Chromium)."""
        return _StubPage()

    async def close(self) -> None:
        """Marca el cierre; no hay proceso que recolectar."""
        self.closed = True


class _StubBrowser:
    """Browser falso: solo existe para cerrar el contexto."""

    def __init__(self, context: _StubContext) -> None:
        self._context = context

    async def close(self) -> None:
        """Cierra el contexto falso."""
        await self._context.close()


class _StubArcaSession:
    """Sesión fiscal falsa: login no-op y servicios no disponibles.

    Sirve para probar el cableado del worker (admisión, runtime,
    artefactos, reporte, cancelación, drenaje) sin Chromium.
    """

    def __init__(self, credentials: Any) -> None:
        self._credentials = credentials
        self.closed = False

    async def login(self) -> None:
        """No-op: no hay sitio fiscal en modo dev."""
        return None

    async def open_service(self, service: str) -> Any:
        """Falla como la real: el port F3 aún no existe."""
        raise NotImplementedError(f"servicio sin portar a F3: {service}")

    async def close(self) -> None:
        """Marca el cierre de la sesión falsa."""
        self.closed = True


class _StubBrowserFactory:
    """Fábrica de navegador solo para desarrollo sin Chromium.

    MODO DEV: no lanza ningún proceso ni abre sockets; implementa la
    misma superficie que ``PlaywrightBrowserFactory`` (``new_context`` y
    ``arca_session``) para que los tests y el arranque local funcionen
    sin la imagen base. NUNCA usar en producción: los bots necesitan un
    Chromium real.

    Camino al Chromium real: construir con el Dockerfile del servicio,
    cuya base ``PLAYWRIGHT_IMAGE`` provee Playwright + Chromium fijados
    por digest (ver ``services/bot-worker/Dockerfile``). Con Playwright
    instalado, ``build_browser_factory`` devuelve automáticamente la
    fábrica real.
    """

    def __init__(
        self,
        proxy: Any = None,
        headless: bool = True,
        captcha_profiles: Any = None,
    ) -> None:
        self._proxy = proxy
        self._headless = True if headless else True  # headless no negociable
        self._solvers = _solvers_from_profiles(captcha_profiles)
        self.launch_count = 0
        self.crash_count = 0
        self.active_sessions = 0

    @property
    def proxy(self) -> Any:
        """Proxy inyectado (``repr`` oculto en ``ProxyConfig``)."""
        return self._proxy

    @asynccontextmanager
    async def new_context(self, **_: Any) -> AsyncIterator[tuple[Any, Any]]:
        """Abre un contexto falso y lo cierra al salir, siempre."""
        context = _StubContext()
        browser = _StubBrowser(context)
        self.active_sessions += 1
        try:
            self.launch_count += 1
            yield browser, context
        finally:
            self.active_sessions = max(0, self.active_sessions - 1)
            await context.close()

    def arca_session(self, credentials: Any, **kwargs: Any) -> Any:
        """Sesión fiscal falsa ligada a un contexto falso propio."""
        from bot_worker.runtime.arca_login import ArcaLoginError

        if credentials is None:
            raise ArcaLoginError("el plugin requiere credenciales")

        @asynccontextmanager
        async def _open() -> AsyncIterator[Any]:
            async with self.new_context():
                session = _StubArcaSession(credentials)
                try:
                    await session.login()
                    yield session
                finally:
                    await session.close()

        return _open()


def _hay_playwright() -> bool:
    """Detecta si la imagen provee Playwright real (base del Dockerfile)."""
    try:
        from importlib.util import find_spec

        return find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def build_browser_factory(
    proxy: Any = None,
    captcha_profiles: Any = None,
    stealth: bool = False,
    canal: str = "",
) -> Any:
    """Devuelve la fábrica real si hay Playwright, o el stub dev.

    Producción: la imagen del Dockerfile trae Playwright + Chromium y
    aquí se obtiene ``PlaywrightBrowserFactory``. Desarrollo/tests sin
    navegador: se obtiene ``_StubBrowserFactory`` (documentado como modo
    dev, sin procesos ni red).
    """
    if _hay_playwright():
        return PlaywrightBrowserFactory(
            proxy=proxy,
            captcha_profiles=captcha_profiles,
            stealth=stealth,
            canal=canal,
        )
    log.warning(
        "sin playwright instalado: fábrica stub solo para desarrollo, "
        "sin Chromium real"
    )
    return _StubBrowserFactory(proxy=proxy, captcha_profiles=captcha_profiles)


__all__ = [
    "BrowserCrashedError",
    "BrowserUnavailableError",
    "PlaywrightBrowserFactory",
    "build_browser_factory",
    "STEALTH_LAUNCH_ARGS",
    "STEALTH_CONTEXT_DEFAULTS",
    "STEALTH_INIT_SCRIPT",
]
