from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from bot_worker.bots.errors import (
    CaptchaUnsolvableError as BotCaptchaUnsolvableError,
    CredentialsRejectedError,
    TargetUnavailableError,
)
from bot_worker.runtime.arca_login import (
    ARCA_LOGIN_URL,
    ArcaServicePage,
    ArcaSession,
)
from bot_worker.runtime.context import FiscalCredentials


class FakeLocator:
    def __init__(
        self,
        selector: str,
        exists: Callable[[str], bool],
        *,
        click_action: Callable[[str], None] | None = None,
        fill_action: Callable[[str, str], None] | None = None,
        text: str = "",
    ) -> None:
        self.selector = selector
        self._exists = exists
        self._click_action = click_action
        self._fill_action = fill_action
        self._text = text
        self.first = self

    async def count(self) -> int:
        return int(self._exists(self.selector))

    def nth(self, _index: int) -> FakeLocator:
        return self

    async def fill(self, value: str, **_kwargs: Any) -> None:
        if self._fill_action:
            self._fill_action(self.selector, value)

    async def click(self, **_kwargs: Any) -> None:
        if self._click_action:
            self._click_action(self.selector)

    async def press(self, _key: str, **_kwargs: Any) -> None:
        if self._click_action:
            self._click_action(self.selector)

    async def is_visible(self) -> bool:
        return bool(await self.count())

    async def inner_text(self, **_kwargs: Any) -> str:
        return self._text

    async def text_content(self) -> str:
        return self._text

    async def get_attribute(self, _name: str) -> str | None:
        return None

    async def screenshot(self, **_kwargs: Any) -> bytes:
        return b"fake-image"

    async def wait_for(self, **_kwargs: Any) -> None:
        return None

    async def scroll_into_view_if_needed(self, **_kwargs: Any) -> None:
        return None

    def filter(self, **_kwargs: Any) -> FakeLocator:
        return self


class FakePopup:
    def __init__(self, page: FakePage) -> None:
        self.value = page

    async def __aenter__(self) -> FakePopup:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None


class FakePage:
    def __init__(self, *, feedback: str = "", captcha: bool = False) -> None:
        self.url = "about:blank"
        self.feedback = feedback
        self.captcha = captcha
        self.fills: dict[str, str] = {}
        self.visits: list[str] = []
        self.clicks: list[str] = []
        self.popup_page: FakePage | None = None
        self.password_submitted = False

    def _exists(self, selector: str) -> bool:
        if "captcha" in selector.lower():
            return self.captcha and "input" not in selector.lower()
        if "F1\\:login" in selector or "type='number'" in selector:
            return True
        if "F1\\:password" in selector or "type='password'" in selector:
            return not self.password_submitted
        if "Siguiente" in selector or "Ingresar" in selector:
            return True
        if "Cambiar" in selector:
            return False
        if selector in ("#F1\\:msg", "span[id$=':msg']", "p.text-danger"):
            return bool(self.feedback)
        if selector.startswith("a, button") or selector.startswith("button,"):
            return True
        return False

    def _click(self, selector: str) -> None:
        self.clicks.append(selector)
        if "Ingresar" in selector or "btnIngresar" in selector:
            self.password_submitted = True

    def _fill(self, selector: str, value: str) -> None:
        self.fills[selector] = value

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(
            selector,
            self._exists,
            click_action=self._click,
            fill_action=self._fill,
            text=self.feedback,
        )

    def get_by_role(self, role: str, name: Any = None) -> FakeLocator:
        return self.locator(f"role={role}:{name}")

    async def goto(self, url: str, **_kwargs: Any) -> None:
        self.url = url
        self.visits.append(url)

    async def wait_for_load_state(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def expect_popup(self, **_kwargs: Any) -> FakePopup:
        return FakePopup(self.popup_page or self)


@pytest.fixture
def credentials() -> FiscalCredentials:
    return FiscalCredentials(cuit_representante="20123456789", clave="test-secret")


def test_login_navega_y_llena_credenciales_sin_exponerlas(credentials: FiscalCredentials) -> None:
    async def run() -> None:
        page = FakePage()
        session = ArcaSession(credentials, page=page)
        await session.login()
        assert page.visits == [ARCA_LOGIN_URL]
        assert "20123456789" in page.fills.values()
        assert "test-secret" in page.fills.values()
        await session.login()
        assert page.visits == [ARCA_LOGIN_URL]
        await session.close()
        assert session._credentials is None

    asyncio.run(run())


def test_login_rechazado_no_repropaga_mensaje_del_sitio_ni_clave(
    credentials: FiscalCredentials,
) -> None:
    async def run() -> None:
        page = FakePage(feedback="Clave o usuario incorrecto")
        session = ArcaSession(credentials, page=page)
        with pytest.raises(CredentialsRejectedError) as exc:
            await session.login()
        assert "test-secret" not in str(exc.value)
        assert "Clave o usuario incorrecto" not in str(exc.value)
        await session.close()

    asyncio.run(run())


def test_captcha_sin_solver_falla_con_error_tipado(credentials: FiscalCredentials) -> None:
    async def run() -> None:
        page = FakePage(captcha=True)
        session = ArcaSession(credentials, page=page)
        with pytest.raises(BotCaptchaUnsolvableError):
            await session.login()
        await session.close()

    asyncio.run(run())


def test_open_service_rechaza_hosts_no_fiscales(credentials: FiscalCredentials) -> None:
    async def run() -> None:
        page = FakePage()
        session = ArcaSession(credentials, page=page)
        session._logged_in = True
        with pytest.raises(TargetUnavailableError):
            await session.open_service("https://example.com/collect")
        assert page.visits == []
        await session.close()

    asyncio.run(run())


def test_open_service_permite_url_fiscal_y_devuelve_adaptador(
    credentials: FiscalCredentials,
) -> None:
    async def run() -> None:
        page = FakePage()
        session = ArcaSession(credentials, page=page)
        session._logged_in = True
        result = await session.open_service(
            "https://portalcf.cloud.afip.gob.ar/portal/app/"
        )
        assert isinstance(result, ArcaServicePage)
        assert page.visits == ["https://portalcf.cloud.afip.gob.ar/portal/app/"]
        await session.close()

    asyncio.run(run())
