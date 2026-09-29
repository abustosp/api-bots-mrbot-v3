"""Pruebas del port FACTUROMETRO sin navegador ni credenciales reales.

Cubren el defecto que dejaba la operación ``facturometro.consultar``
caída con ``plugin_service_api_mismatch``: el plugin llamaba
``servicio.leer_facturometro`` pero ningún portal lo implementaba.
"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.facturometro.plugin import FacturometroPlugin
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals.facturometro import (
    JS_CALCULAR_FACTURACION,
    JS_LECTURA_FACTUROMETRO,
    FacturometroPortal,
)


class _Localizador:
    def __init__(self, pagina: "_Pagina", selector: str) -> None:
        self._pagina = pagina
        self._selector = selector

    async def count(self) -> int:
        if self._selector == "#divFacturometro":
            return self._pagina.div_facturometro
        if "hidCUITContribuyente" in self._selector:
            return 1 if self._pagina.cuit else 0
        return 0

    async def input_value(self, **_: Any) -> str:
        return self._pagina.cuit or ""

    @property
    def first(self) -> "_Localizador":
        return self


class _Pagina:
    """Página falsa con facturómetro en DOM y/o respuesta AJAX."""

    def __init__(
        self,
        *,
        dom: dict[str, Any] | None = None,
        ajax: dict[str, Any] | None = None,
        div_facturometro: int = 1,
        cuit: str | None = None,
        html: str = "",
    ) -> None:
        self.dom = dom
        self.ajax = ajax
        self.div_facturometro = div_facturometro
        self.cuit = cuit
        self.html = html
        self.evaluados: list[str] = []

    async def content(self) -> str:
        return self.html

    def locator(self, selector: str) -> _Localizador:
        return _Localizador(self, selector)

    def get_by_role(self, *_: Any, **__: Any) -> _Localizador:
        return _Localizador(self, "")

    async def wait_for_function(self, *_: Any, **__: Any) -> None:
        if not self.dom:
            raise TimeoutError("facturometro no hidratado")

    async def evaluate(self, script: str, *_: Any, **__: Any) -> Any:
        self.evaluados.append(script)
        if script == JS_LECTURA_FACTUROMETRO:
            return dict(self.dom or {})
        if script == JS_CALCULAR_FACTURACION:
            return dict(self.ajax or {})
        return {}


def _portal(pagina: _Pagina) -> FacturometroPortal:
    portal = FacturometroPortal(pagina)
    portal._clickear = AsyncMock(return_value=False)  # type: ignore[method-assign]
    portal._esperar = AsyncMock()  # type: ignore[method-assign]
    portal.presupuesto_lectura_ms = 200
    return portal


def test_portal_facturometro_se_registra_por_descubrimiento() -> None:
    assert portal_para("facturometro") is FacturometroPortal
    assert PORTALES["facturometro"] is FacturometroPortal


def test_leer_facturometro_desde_el_dom() -> None:
    pagina = _Pagina(
        dom={
            "monto": "$ 1.234.567,89",
            "tope": "$ 68.000.000,00",
            "categoria": "C",
        }
    )

    lectura = asyncio.run(_portal(pagina).leer_facturometro(""))

    assert lectura == {
        "monto": "$ 1.234.567,89",
        "tope": "$ 68.000.000,00",
        "categoria": "C",
    }


def test_leer_facturometro_cae_al_ajax_de_v2() -> None:
    pagina = _Pagina(
        div_facturometro=0,
        ajax={
            "visible": True,
            "pendiente": False,
            "valor": "$ 10,00",
            "valorTope": "$ 20,00",
            "categoria": "A",
            "alertaDetalle": None,
        },
    )
    portal = _portal(pagina)
    portal.presupuesto_lectura_ms = 0  # type: ignore[assignment]

    lectura = asyncio.run(portal.leer_facturometro(""))

    assert lectura == {"monto": "$ 10,00", "tope": "$ 20,00", "categoria": "A"}


def test_leer_facturometro_diagnostica_no_disponible() -> None:
    pagina = _Pagina(dom={}, ajax={"visible": False, "alertaDetalle": "x"})
    portal = _portal(pagina)
    portal.presupuesto_lectura_ms = 0  # type: ignore[assignment]

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(portal.leer_facturometro(""))

    assert exc.value.diagnostic_code == "facturometro_no_disponible"


def test_seleccionar_representado_usa_selectores_de_v2() -> None:
    pagina = _Pagina(
        dom={"monto": "$ 1,00", "tope": "$ 2,00", "categoria": "A"},
    )
    portal = _portal(pagina)
    portal._clickear = AsyncMock(return_value=True)  # type: ignore[method-assign]

    asyncio.run(portal.leer_facturometro("20123456789"))

    portal._clickear.assert_awaited()  # type: ignore[attr-defined]


def test_sin_selector_tolera_al_titular_de_la_sesion() -> None:
    """El portal no ofrece selector cuando el representado es el titular."""
    pagina = _Pagina(
        dom={"monto": "$ 1,00", "tope": "$ 2,00", "categoria": "A"},
        cuit="20123456789",
    )

    lectura = asyncio.run(_portal(pagina).leer_facturometro("20123456789"))

    assert lectura["monto"] == "$ 1,00"


def test_representado_distinto_corta_la_lectura() -> None:
    pagina = _Pagina(
        dom={"monto": "$ 1,00", "tope": "$ 2,00", "categoria": "A"},
        cuit="27999999999",
    )

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(_portal(pagina).leer_facturometro("20123456789"))

    assert exc.value.diagnostic_code == "facturometro_representado_distinto"


def test_cuit_visible_por_html_tambien_valida() -> None:
    pagina = _Pagina(
        dom={"monto": "$ 1,00", "tope": "$ 2,00", "categoria": "A"},
        html='<input id="hidCUITContribuyente" value="20123456789" />',
    )

    lectura = asyncio.run(_portal(pagina).leer_facturometro("20123456789"))

    assert lectura["monto"] == "$ 1,00"


def test_portal_no_listo_diagnostica_reintentable() -> None:
    portal = _portal(_Pagina(dom={"monto": "$ 1,00"}))
    portal._esperar_portal_listo = AsyncMock(  # type: ignore[method-assign]
        return_value=False
    )

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(portal.leer_facturometro("20123456789"))

    assert exc.value.diagnostic_code == "represented_cuit_not_selectable"


def test_lectura_espera_la_hidratacion_del_facturometro() -> None:
    """Tras navegar, el div aparece recién en el segundo sondeo."""

    class _PaginaTardia(_Pagina):
        def __init__(self) -> None:
            super().__init__(dom={"monto": "$ 5,00", "tope": "$ 9,00", "categoria": "B"})
            self.sondeos = 0

        def locator(self, selector: str) -> _Localizador:
            if selector == "#divFacturometro":
                self.sondeos += 1
                self.div_facturometro = int(self.sondeos > 1)
            return super().locator(selector)

    pagina = _PaginaTardia()

    lectura = asyncio.run(_portal(pagina).leer_facturometro(""))

    assert lectura["monto"] == "$ 5,00"
    assert pagina.sondeos >= 2


def test_plugin_abre_servicio_con_portal_facturometro(tmp_path: Path) -> None:
    class _Servicio:
        representado: str | None = None

        async def leer_facturometro(self, representado_cuit: str) -> dict[str, str]:
            self.representado = representado_cuit
            return {"monto": "$ 1,00", "tope": "$ 2,00", "categoria": "A"}

    servicio = _Servicio()

    class _Sesion:
        llamada: tuple[str, dict[str, Any]] | None = None

        async def login(self) -> None:
            return None

        async def open_service(self, nombre: str, **kwargs: Any) -> _Servicio:
            self.llamada = (nombre, kwargs)
            return servicio

    sesion = _Sesion()

    class _Factory:
        @asynccontextmanager
        async def arca_session(self, **_: Any):
            yield sesion

    async def no_op(*_: Any, **__: Any) -> None:
        return None

    runtime = SimpleNamespace(
        credentials=SimpleNamespace(clave="clave-ficticia"),
        cancellation=SimpleNamespace(raise_if_cancelled=no_op),
        event_sink=SimpleNamespace(progress=no_op),
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        browser_factory=_Factory(),
        proxy=None,
        work_dir=tmp_path,
    )
    plugin = FacturometroPlugin()
    entrada = asyncio.run(
        plugin.validate(
            {"operacion": "consultar", "representado_cuit": "20123456789"}
        )
    )

    resultado = asyncio.run(plugin.execute(entrada, runtime))

    assert sesion.llamada == ("MONOTRIBUTO", {"portal": "facturometro"})
    assert servicio.representado == "20123456789"
    assert resultado.result == "OK"
    assert resultado.data["monto"] == "$ 1,00"
    assert resultado.data["tope"] == "$ 2,00"
    assert resultado.data["categoria"] == "A"
    assert resultado.artifacts == []
    assert json.loads((tmp_path / "resultado.json").read_text()) == resultado.data
