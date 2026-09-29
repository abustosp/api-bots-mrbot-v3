"""Opciones de navegador por bot: stealth y canal completo.

El perímetro de AGIP bloquea al shell headless recortado de Playwright; con el
stealth portado de la V1 y el binario completo (``channel="chromium"``) el flujo
entra y descarga (medido el 29/09/2026, ver el runbook de validación). Estas
pruebas fijan el contrato del manifiesto y de la fábrica, con un Playwright falso
para no abrir navegadores.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from bot_worker.runtime import browser as browser_mod


class _ContextoFalso:
    def __init__(self) -> None:
        self.init_scripts: list[str] = []

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)

    async def close(self) -> None:
        return None


class _NavegadorFalso:
    def __init__(self, capturado: dict[str, Any]) -> None:
        self._capturado = capturado
        self._contexto = _ContextoFalso()

    async def new_context(self, **kwargs: Any) -> _ContextoFalso:
        self._capturado["contexto"] = kwargs
        return self._contexto

    async def close(self) -> None:
        return None


class _ChromiumFalso:
    def __init__(self, capturado: dict[str, Any]) -> None:
        self._capturado = capturado

    async def launch(self, **kwargs: Any) -> _NavegadorFalso:
        self._capturado["launch"] = kwargs
        return _NavegadorFalso(self._capturado)


class _PlaywrightFalso:
    def __init__(self, capturado: dict[str, Any]) -> None:
        self.chromium = _ChromiumFalso(capturado)

    async def stop(self) -> None:
        return None


class _AsyncPlaywrightFalso:
    def __init__(self, capturado: dict[str, Any]) -> None:
        self._capturado = capturado

    async def start(self) -> _PlaywrightFalso:
        return _PlaywrightFalso(self._capturado)


def _playwright_falso(monkeypatch: pytest.MonkeyPatch, capturado: dict[str, Any]) -> None:
    modulo = type(
        "playwright.async_api",
        (),
        {"async_playwright": staticmethod(lambda: _AsyncPlaywrightFalso(capturado))},
    )
    monkeypatch.setitem(sys.modules, "playwright", modulo)
    monkeypatch.setitem(sys.modules, "playwright.async_api", modulo)


@pytest.mark.parametrize("stealth,canal", [(False, ""), (True, "chromium")])
def test_opciones_de_navegador_segun_manifiesto(
    monkeypatch: pytest.MonkeyPatch, stealth: bool, canal: str
) -> None:
    capturado: dict[str, Any] = {}
    _playwright_falso(monkeypatch, capturado)
    fabrica = browser_mod.PlaywrightBrowserFactory(stealth=stealth, canal=canal)

    import asyncio

    async def abrir() -> None:
        async with fabrica.new_context() as (_browser, _context):
            pass

    asyncio.run(abrir())

    launch = capturado["launch"]
    assert launch["headless"] is True  # el invariante no se negocia
    if canal:
        assert launch["channel"] == "chromium"
        # El binario completo necesita directorios XDG escribibles: con la raíz
        # de solo lectura del worker falla al lanzar si apuntan al HOME.
        import os

        entorno = launch["env"]
        for variable in ("XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
            ruta = entorno[variable]
            assert os.path.isabs(ruta) and os.path.isdir(ruta)
            assert os.access(ruta, os.W_OK), variable
    else:
        assert "channel" not in launch
        assert "env" not in launch
    if stealth:
        assert tuple(launch["args"]) == browser_mod.STEALTH_LAUNCH_ARGS
        assert capturado["contexto"]["user_agent"] == browser_mod.STEALTH_CONTEXT_DEFAULTS["user_agent"]
        assert capturado["contexto"]["locale"] == "es-AR"
        assert capturado["contexto"]["accept_downloads"] is True
    else:
        assert "args" not in launch
        assert set(capturado["contexto"]) == {"accept_downloads"}


def test_la_fabrica_arma_el_init_script_solo_con_stealth(monkeypatch: pytest.MonkeyPatch) -> None:
    capturado: dict[str, Any] = {}
    _playwright_falso(monkeypatch, capturado)
    fabrica = browser_mod.PlaywrightBrowserFactory(stealth=True, canal="chromium")

    import asyncio

    visto: list[Any] = []

    async def abrir() -> None:
        async with fabrica.new_context() as (_browser, context):
            visto.append(context)

    asyncio.run(abrir())
    assert visto and visto[0].init_scripts == [browser_mod.STEALTH_INIT_SCRIPT]


def test_el_manifiesto_de_agip_declara_stealth_y_canal() -> None:
    from bot_worker.bots.retper_iibb_agip.plugin import RetperIibbAgipPlugin

    manifiesto = RetperIibbAgipPlugin.manifest
    assert manifiesto.stealth is True
    assert manifiesto.canal_navegador == "chromium"
    # El resto de los bots conserva el comportamiento por defecto.
    from bot_worker.bots.liquidacion_granos.plugin import LiquidacionGranosPlugin

    normal = LiquidacionGranosPlugin.manifest
    assert normal.stealth is False and normal.canal_navegador == ""
