"""Runtime inyectado a los plugins: navegador, workdir, captcha y proxy.

Los plugins nunca tocan red, disco o entorno por su cuenta: todo efecto
lateral vive aqui y se construye por job desde el sobre ya abierto.
"""

from bot_worker.runtime.browser import (
    BrowserCrashedError,
    BrowserUnavailableError,
    PlaywrightBrowserFactory,
)
from bot_worker.runtime.context import (
    ArtifactSlot,
    ArtifactStore,
    BotResult,
    BotRuntime,
    CancellationToken,
    DeadlineBudget,
    FiscalCredentials,
    JobCancelled,
    ProxyConfig,
)

__all__ = [
    "ArtifactSlot",
    "ArtifactStore",
    "BotResult",
    "BotRuntime",
    "BrowserCrashedError",
    "BrowserUnavailableError",
    "CancellationToken",
    "DeadlineBudget",
    "FiscalCredentials",
    "JobCancelled",
    "PlaywrightBrowserFactory",
    "ProxyConfig",
]
