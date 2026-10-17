"""Regresion de la apertura del servicio SIFERE verificada contra el portal real.

Dos defectos solo visibles en vivo (09/10/2026):
1. El portal recorta el titulo de la tarjeta ("... SIFERE WEB - Cons..."), por
   lo que buscar el nombre completo nunca coincidia.
2. La regla que fuerza https en COMARB, instalada antes de buscar la tarjeta,
   impedia que el catalogo del portal se hidratara (0 enlaces).
"""

from __future__ import annotations

import asyncio
import re

from bot_worker.runtime.arca_login import ArcaSession

TITULO_RECORTADO = "Convenio Multilateral – SIFERE WEB - Cons..."


def test_patron_sifere_acepta_titulo_recortado_y_rechaza_ddjj() -> None:
    patron = ArcaSession._PATRONES_SERVICIO_RECORTADO["sifere"]
    assert patron.search(TITULO_RECORTADO)
    assert patron.search("CONVENIO MULTILATERAL – SIFERE WEB - CONSULTAS")
    # La tarjeta de DDJJ es otro servicio y no debe abrirse por error.
    assert not patron.search("Convenio Multilateral – SIFERE WEB - DDJJ")
    assert not patron.search("CONVENIO MULTILATERAL - PADRÓN WEB")


class _Locator:
    def __init__(self, ok: bool) -> None:
        self._ok = ok

    async def count(self) -> int:
        return 1 if self._ok else 0

    def nth(self, _i: int) -> "_Locator":
        return self

    async def is_visible(self) -> bool:
        return True

    def filter(self, **_k: object) -> "_Locator":
        return self


class _Ctx:
    def __init__(self, titulo: str) -> None:
        self._titulo = titulo

    def _hit(self, m: re.Pattern[str]) -> _Locator:
        return _Locator(bool(m.search(self._titulo)))

    def get_by_role(self, _rol: str, name: re.Pattern[str]) -> _Locator:
        return _Locator(False)

    def get_by_text(self, m: re.Pattern[str], exact: bool = False) -> _Locator:
        return self._hit(m)

    def locator(self, _css: str) -> _Locator:
        return _Locator(False)


def test_find_service_con_patron_ubica_la_tarjeta_recortada() -> None:
    sesion = ArcaSession.__new__(ArcaSession)
    ctx = _Ctx(TITULO_RECORTADO)
    sesion._page_contexts = lambda: (ctx,)  # type: ignore[method-assign]
    patron = ArcaSession._PATRONES_SERVICIO_RECORTADO["sifere"]

    async def _buscar() -> tuple[object, object]:
        con = await sesion._find_service("CONVENIO MULTILATERAL – SIFERE WEB - CONSULTAS", patron)
        sin = await sesion._find_service("CONVENIO MULTILATERAL – SIFERE WEB - CONSULTAS")
        return con, sin

    con_patron, sin_patron = asyncio.run(_buscar())
    assert con_patron is not None
    assert sin_patron is None  # el nombre completo no coincide con el titulo recortado


def test_open_service_sifere_expande_catalogo_antes_de_esperar_tarjeta() -> None:
    eventos: list[str] = []
    servicio = object()
    pagina_servicio = object()

    class _ServiceLocator:
        async def click(self, **_kwargs: object) -> None:
            eventos.append("click_servicio")

    class _Popup:
        async def __aenter__(self) -> "_Popup":
            return self

        async def __aexit__(self, *_args: object) -> bool:
            return False

        @property
        def value(self):
            async def _valor() -> object:
                return pagina_servicio

            return _valor()

    class _Page:
        url = "https://auth.afip.gob.ar/contribuyente_/"

        def expect_popup(self, **_kwargs: object) -> _Popup:
            return _Popup()

    sesion = ArcaSession.__new__(ArcaSession)
    sesion._page = _Page()
    sesion._context = object()
    sesion._credentials = None
    sesion._captcha = None
    sesion._logged_in = True
    locator = _ServiceLocator()
    llamadas_busqueda = 0

    async def _buscar(_value: str, _patron: re.Pattern[str] | None = None):
        nonlocal llamadas_busqueda
        llamadas_busqueda += 1
        eventos.append("buscar_tarjeta")
        return locator if llamadas_busqueda == 2 else None

    async def _ver_todos(
        _selectores: tuple[str, ...], *, timeout_ms: int
    ) -> bool:
        assert timeout_ms == 10_000
        eventos.append("click_ver_todos")
        return True

    async def _esperar_ready() -> None:
        eventos.append("esperar_catalogo")

    async def _asegurar_https() -> None:
        eventos.append("route_https")

    async def _esperar_pagina(_pagina: object) -> None:
        eventos.append("esperar_servicio")

    sesion._find_service = _buscar  # type: ignore[method-assign]
    sesion._click_first_visible_in_contexts = _ver_todos  # type: ignore[method-assign]
    sesion._wait_ready = _esperar_ready  # type: ignore[method-assign]
    sesion._asegurar_route_https_comarb = _asegurar_https  # type: ignore[method-assign]
    sesion._esperar_pagina = _esperar_pagina  # type: ignore[method-assign]
    sesion._servicio_para = lambda _pagina, _portal: servicio  # type: ignore[method-assign]

    resultado = asyncio.run(
        sesion.open_service("CONVENIO MULTILATERAL – SIFERE WEB - CONSULTAS", portal="sifere")
    )

    assert resultado is servicio
    assert eventos[:4] == [
        "buscar_tarjeta",
        "click_ver_todos",
        "esperar_catalogo",
        "buscar_tarjeta",
    ]
    assert eventos.index("click_ver_todos") < eventos.index("click_servicio")


def test_route_https_comarb_no_se_instala_antes_de_ubicar_la_tarjeta() -> None:
    import inspect

    fuente = inspect.getsource(ArcaSession.open_service)
    pos_ruta = fuente.index("await self._asegurar_route_https_comarb()")
    pos_busqueda = fuente.index("self._find_service(value, patron_servicio)")
    assert pos_ruta > pos_busqueda
