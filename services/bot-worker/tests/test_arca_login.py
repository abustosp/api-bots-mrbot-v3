from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from bot_worker.bots.errors import CaptchaUnsolvableError, TargetUnavailableError
from bot_worker.runtime.arca_login import ArcaLoginError, ArcaServicePage, ArcaSession
from bot_worker.runtime.browser import PlaywrightBrowserFactory
from bot_worker.runtime.captcha import CaptchaUnsolvableError as SolverError
from bot_worker.runtime.context import FiscalCredentials


class _Locator:
    def __init__(self, page: "_Page", selector: str, exists: bool = False) -> None:
        self.page = page
        self.selector = selector
        self.exists = exists
        self.first = self

    async def count(self) -> int:
        return int(self.exists)

    async def fill(self, value: str, **_: object) -> None:
        self.page.filled[self.selector] = value
        if "captcha" in self.selector.lower():
            self.page.captcha = False

    async def click(self, **_: object) -> None:
        self.page.clicked.append(self.selector)
        if self.selector in {
            "button:has-text('Ingresar')",
            "input#F1\\:btnIngresar",
            "input[type='submit']",
        }:
            self.page.password_submitted = True

    async def press(self, key: str) -> None:
        self.page.pressed.append((self.selector, key))

    async def is_visible(self) -> bool:
        if self.selector in {
            "input#F1\\:password",
            "input[name='F1:password']",
            "input[type='password']",
            "#F1\\:password",
        }:
            return self.exists and not self.page.password_submitted
        return self.exists

    async def get_attribute(self, name: str) -> str | None:
        return self.page.attributes.get((self.selector, name))

    async def screenshot(self, **_: object) -> bytes:
        return b"fake-captcha-image"

    async def inner_text(self, **_: object) -> str:
        return self.page.messages.get(self.selector, "")

    async def text_content(self) -> str:
        return self.page.messages.get(self.selector, "")

    def filter(self, **_: object) -> "_Locator":
        return self

    def nth(self, _: int) -> "_Locator":
        return self


class _Page:
    def __init__(self, *, captcha: bool = False) -> None:
        self.captcha = captcha
        self.visited: list[str] = []
        self.filled: dict[str, str] = {}
        self.clicked: list[str] = []
        self.pressed: list[tuple[str, str]] = []
        self.attributes: dict[tuple[str, str], str] = {}
        self.messages: dict[str, str] = {}
        self.password_submitted = False
        self.frames: list[object] = []

    def locator(self, selector: str) -> _Locator:
        exists = selector in {
            "input#F1\\:login",
            "#F1\\:password",
            "button:has-text('Siguiente')",
            "button:has-text('Ingresar')",
        }
        if self.captcha and selector in {
            "#captcha",
            "#captcha img",
            "input#F1\\:captchaSolutionInput",
            "input[name='F1:captchaSolutionInput']",
            "input[placeholder*='captcha' i]",
            "input[id*='captcha' i]",
        }:
            exists = True
        if selector == "#captcha img" and exists:
            self.attributes[(selector, "src")] = "data:image/png;base64,ZmFrZQ=="
        if selector.startswith("small.pull-right:has-text"):
            exists = True
        if selector == "button, [role='button'], a":
            exists = True
        return _Locator(self, selector, exists)

    def get_by_role(self, role: str, *, name: object = None) -> _Locator:
        return _Locator(self, f"role:{role}:{name}", exists=True)

    def get_by_text(self, *_: object, **__: object) -> _Locator:
        return _Locator(self, "text:service", exists=False)

    async def goto(self, url: str, **_: object) -> None:
        self.visited.append(url)

    async def wait_for_load_state(self, *_: object, **__: object) -> None:
        return None

    def expect_popup(self, **_: object):
        return _NoPopup()


class _ModernLoginPage(_Page):
    """Simula los IDs y controles submit actuales publicados por ARCA."""

    def locator(self, selector: str) -> _Locator:
        modern_selectors = {
            "input#F1\\:username",
            "input[name='F1:username']",
            "input#F1\\:btnSiguiente",
            "input#F1\\:btnIngresar",
            "input[type='submit']",
            "#F1\\:password",
        }
        return _Locator(self, selector, exists=selector in modern_selectors)


class _CatalogFrame(_Page):
    """Frame de portal con el servicio mientras la página principal no lo tiene."""

    def get_by_role(self, role: str, *, name: object = None) -> _Locator:
        matches = role == "link" and bool(
            hasattr(name, "search") and name.search("MIS COMPROBANTES")
        )
        return _Locator(self, f"role:{role}:{name}", exists=matches)

    def get_by_text(self, *_: object, **__: object) -> _Locator:
        return _Locator(self, "text:service", exists=False)

    def locator(self, selector: str) -> _Locator:
        return _Locator(
            self,
            selector,
            exists=selector == "button, [role='button'], a",
        )


class _PageWithServiceFrame(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.frames = [_CatalogFrame()]

    def get_by_role(self, role: str, *, name: object = None) -> _Locator:
        return _Locator(self, f"role:{role}:{name}", exists=False)

    def locator(self, selector: str) -> _Locator:
        if selector == "button, [role='button'], a":
            return _Locator(self, selector, exists=False)
        return super().locator(selector)


class _RejectedLoginPage(_Page):
    @property
    def password_submitted(self) -> bool:
        return False

    @password_submitted.setter
    def password_submitted(self, _: bool) -> None:
        pass


class _BrokenLoginPage(_Page):
    async def goto(self, url: str, **_: object) -> None:
        raise TimeoutError("private page details must not escape")


class _NoPopup:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_: object) -> None:
        raise TimeoutError("fake page did not create a popup")


class _Context:
    def __init__(self, page: _Page) -> None:
        self.page = page

    async def new_page(self) -> _Page:
        return self.page


class _Solver:
    enabled = True
    max_attempts = 2

    def __init__(self) -> None:
        self.received: list[bytes] = []

    async def solve_image(self, image: bytes) -> str:
        self.received.append(image)
        return "AB12"


def test_login_usa_page_inyectada_y_es_idempotente() -> None:
    page = _Page()
    session = ArcaSession(FiscalCredentials("20123456789", "clave-ficticia"), page=page)

    async def run() -> None:
        await session.login()
        await session.login()

    asyncio.run(run())
    assert page.visited == ["https://auth.afip.gob.ar/contribuyente_/login.xhtml"]
    assert page.filled["input#F1\\:login"] == "20123456789"
    assert page.filled["#F1\\:password"] == "clave-ficticia"


def test_login_admite_ids_y_submit_actuales_de_arca() -> None:
    page = _ModernLoginPage()
    session = ArcaSession(FiscalCredentials("20123456789", "clave-ficticia"), page=page)

    asyncio.run(session.login())

    assert page.filled["input#F1\\:username"] == "20123456789"
    assert page.filled["#F1\\:password"] == "clave-ficticia"
    assert "input#F1\\:btnSiguiente" in page.clicked
    assert "input#F1\\:btnIngresar" in page.clicked


def test_error_de_navegacion_expone_codigo_seguro_sin_detalle_crudo() -> None:
    session = ArcaSession(
        FiscalCredentials("20123456789", "clave-ficticia"),
        page=_BrokenLoginPage(),
    )

    with pytest.raises(TargetUnavailableError) as caught:
        asyncio.run(session.login())

    assert caught.value.diagnostic_code == "arca_login_navigation_failed"
    assert "private page details" not in str(caught.value)


def test_login_no_acepta_formulario_de_clave_que_sigue_visible() -> None:
    session = ArcaSession(
        FiscalCredentials("20123456789", "clave-ficticia"),
        page=_RejectedLoginPage(),
    )

    with pytest.raises(ArcaLoginError) as caught:
        asyncio.run(session.login())

    assert caught.value.diagnostic_code == "arca_login_not_completed"
    assert "clave-ficticia" not in str(caught.value)


def test_login_rechaza_pantalla_auth_sin_redireccion_a_portal() -> None:
    class _StuckLoginPage(_Page):
        url = "https://auth.afip.gob.ar/contribuyente_/login.xhtml"

    session = ArcaSession(
        FiscalCredentials("20123456789", "clave-ficticia"),
        page=_StuckLoginPage(),
    )

    with pytest.raises(ArcaLoginError) as caught:
        asyncio.run(session.login())

    assert caught.value.diagnostic_code == "arca_login_still_on_auth_page"
    assert "clave-ficticia" not in str(caught.value)


def test_busca_servicio_en_iframes_del_portal_arca() -> None:
    page = _PageWithServiceFrame()
    session = ArcaSession(FiscalCredentials("20123456789", "clave-ficticia"), page=page)

    async def run() -> ArcaServicePage:
        await session.login()
        return await session.open_service("MIS COMPROBANTES")

    service = asyncio.run(run())

    assert isinstance(service, ArcaServicePage)
    frame = page.frames[0]
    assert len(frame.clicked) == 1
    assert frame.clicked[0].startswith("role:link:")


def test_captcha_usa_solver_inyectado_y_no_entorno() -> None:
    page = _Page(captcha=True)
    solver = _Solver()
    session = ArcaSession(FiscalCredentials("20123456789", "clave-ficticia"), solver, page=page)
    asyncio.run(session.login())

    assert solver.received == [b"fake"]
    assert any("captchaSolutionInput" in selector for selector in page.filled)


def test_captcha_sin_solver_falla_con_error_tipado() -> None:
    page = _Page(captcha=True)
    session = ArcaSession(FiscalCredentials("20123456789", "clave-ficticia"), page=page)

    with pytest.raises(CaptchaUnsolvableError):
        asyncio.run(session.login())


def test_open_service_por_url_devuelve_adaptador_playwright() -> None:
    page = _Page()
    session = ArcaSession(FiscalCredentials("20123456789", "clave-ficticia"), page=page)

    async def run():
        await session.login()
        return await session.open_service("https://servicios.afip.gob.ar/app")

    servicio = asyncio.run(run())
    assert isinstance(servicio, ArcaServicePage)
    assert servicio._page is page
    assert page.visited[-1] == "https://servicios.afip.gob.ar/app"


def test_open_service_rechaza_url_fuera_del_dominio_fiscal() -> None:
    page = _Page()
    session = ArcaSession(FiscalCredentials("20123456789", "clave-ficticia"), page=page)

    async def run() -> None:
        await session.login()
        await session.open_service("https://example.org/callback")

    with pytest.raises(TargetUnavailableError, match="URL de servicio ARCA no permitida") as caught:
        asyncio.run(run())
    assert caught.value.diagnostic_code == "arca_service_url_rejected"
    assert page.visited == ["https://auth.afip.gob.ar/contribuyente_/login.xhtml"]


def test_open_service_por_nombre_y_seleccion_representado() -> None:
    page = _Page()
    session = ArcaSession(FiscalCredentials("20123456789", "clave-ficticia"), page=page)

    async def run() -> ArcaServicePage:
        await session.login()
        service = await session.open_service("MIS COMPROBANTES")
        await service.seleccionar_representado("20-12345678-9")
        return service

    service = asyncio.run(run())
    assert isinstance(service, ArcaServicePage)
    assert any(selector.startswith("role:button:") for selector in page.clicked)
    assert "small.pull-right:has-text('20-12345678-9')" in page.clicked


def test_browser_factory_inyecta_page_en_sesion_real(monkeypatch: pytest.MonkeyPatch) -> None:
    page = _Page()
    context = _Context(page)
    factory = PlaywrightBrowserFactory()

    @asynccontextmanager
    async def fake_context(**_: object):
        yield object(), context

    monkeypatch.setattr(factory, "new_context", fake_context)

    async def run() -> None:
        async with factory.arca_session(FiscalCredentials("20123456789", "clave-ficticia")) as session:
            assert session.page is page
            await session.login()

    asyncio.run(run())
    assert len(page.visited) == 1


def test_solver_error_no_se_filtra_al_diagnostico() -> None:
    class _FailingSolver(_Solver):
        async def solve_image(self, image: bytes) -> str:
            raise SolverError("sensitive provider response")

    page = _Page(captcha=True)
    session = ArcaSession(
        FiscalCredentials("20123456789", "clave-ficticia"), _FailingSolver(), page=page
    )

    with pytest.raises(CaptchaUnsolvableError) as exc:
        asyncio.run(session.login())
    assert "sensitive provider response" not in str(exc.value)
