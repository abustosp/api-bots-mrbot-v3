"""Login fiscal compartido (stub F2).

Portado real en F3: automatiza el inicio de sesion ARCA con Playwright
detras de BrowserFactory. Este stub define la interfaz y permite el
camino end-to-end sin navegador.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from bot_worker.runtime.captcha import CaptchaSolver
from bot_worker.runtime.context import FiscalCredentials


class ArcaLoginError(Exception):
    category = "CREDENTIALS_REJECTED"


class ArcaSession:
    """Sesion fiscal efimera: se cierra al salir del contexto."""

    def __init__(
        self,
        credentials: FiscalCredentials,
        captcha: CaptchaSolver | None = None,
    ) -> None:
        if credentials is None:
            raise ArcaLoginError("el plugin requiere credenciales")
        self._credentials = credentials
        self._captcha = captcha

    async def login(self) -> None:
        # F3: navegar al login ARCA, resolver CAPTCHA si aparece,
        # validar CUIT/clave y abrir la sesion fiscal.
        return None

    async def open_service(self, service: str) -> Any:
        # F3: devuelve el page-objeto del servicio (p. ej. "SIPER").
        raise NotImplementedError(f"servicio sin portar a F3: {service}")

    async def close(self) -> None:
        return None


@asynccontextmanager
async def arca_session(
    credentials: FiscalCredentials | None,
    captcha: CaptchaSolver | None = None,
    **_: Any,
) -> AsyncIterator[ArcaSession]:
    session = ArcaSession(credentials, captcha)  # type: ignore[arg-type]
    try:
        await session.login()
        yield session
    finally:
        await session.close()
