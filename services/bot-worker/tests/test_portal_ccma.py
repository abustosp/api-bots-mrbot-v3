"""Portal CCMA contra V1 ``ccma_bot.py``: orden de acciones y selectores.

Se usa una pagina falsa que registra cada llamada. Las pruebas fijan los
detalles que importan para la paridad: el ``ArrowRight`` previo al periodo, la
secuencia de clic en ``#mas1`` (normal, forzado, ``element_handle`` y JS), los
XPath del resumen y las opciones de impresion del PDF.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals.ccma import (
    CAMPOS_RESUMEN,
    MOVIMIENTOS_FILA,
    MOVIMIENTOS_SIGUIENTE,
    MOVIMIENTOS_TABLA,
    RESUMEN_CELDA,
    CcmaPortal,
)

CUIT = "20123456786"


class _Locador:
    def __init__(self, page: "_PaginaFalsa", selector: str) -> None:
        self._page = page
        self._selector = selector

    @property
    def first(self) -> "_Locador":
        return self

    async def count(self) -> int:
        self._page.acciones.append(("count", self._selector))
        return self._page.conteo.get(self._selector, 1)

    async def click(self, **kwargs: Any) -> None:
        self._page.acciones.append(("click", self._selector, kwargs))
        if self._selector in self._page.clics_fallan:
            raise RuntimeError("click bloqueado")

    async def press(self, key: str) -> None:
        self._page.acciones.append(("press", self._selector, key))

    async def fill(self, value: str) -> None:
        self._page.acciones.append(("fill", self._selector, value))

    async def select_option(self, valor: str, **kwargs: Any) -> None:
        self._page.acciones.append(("select_option", self._selector, valor))

    async def scroll_into_view_if_needed(self, **kwargs: Any) -> None:
        self._page.acciones.append(("scroll", self._selector))

    async def element_handle(self) -> Any:
        self._page.acciones.append(("element_handle", self._selector))
        if self._page.handle_falla:
            raise RuntimeError("sin handle")
        return _Handle(self._page)


class _Handle:
    def __init__(self, page: "_PaginaFalsa") -> None:
        self._page = page

    async def evaluate(self, _js: str) -> None:
        self._page.acciones.append(("handle_click", MOVIMIENTOS_SIGUIENTE))


class _RolFalso:
    def __init__(self, page: "_PaginaFalsa", role: str, name: Any) -> None:
        self._page = page
        self._role = role
        self._name = name

    async def click(self, **kwargs: Any) -> None:
        self._page.acciones.append(("click_rol", self._role, str(self._name)))


class _PaginaFalsa:
    def __init__(self) -> None:
        self.acciones: list[tuple[Any, ...]] = []
        self.clics_fallan: set[str] = set()
        self.handle_falla = False
        self.conteo: dict[str, int] = {}
        self.evaluate_resultado: Any = {}
        self.js: list[str] = []
        self.args_js: list[Any] = []
        self.args_evaluate: list[Any] = []
        self.esperas_load_state: list[tuple[Any, ...]] = []
        self.pdf_kwargs: dict[str, Any] = {}
        self.goto_urls: list[str] = []

    def locator(self, selector: str) -> _Locador:
        return _Locador(self, selector)

    def get_by_role(self, role: str, *, name: Any = None, **_: Any) -> _RolFalso:
        return _RolFalso(self, role, name)

    async def goto(self, url: str) -> None:
        self.goto_urls.append(url)

    async def wait_for_load_state(self, estado: str, **kwargs: Any) -> None:
        self.esperas_load_state.append((estado, kwargs))

    async def wait_for_function(self, js: str, **kwargs: Any) -> None:
        self.js.append(js)
        self.args_js.append(kwargs.get("arg"))

    async def evaluate(self, js: str, arg: Any = None) -> Any:
        self.js.append(js)
        self.args_evaluate.append(arg)
        if callable(self.evaluate_resultado):
            return self.evaluate_resultado(js, arg)
        return self.evaluate_resultado

    async def pdf(self, **kwargs: Any) -> None:
        self.pdf_kwargs = kwargs

    async def set_content(self, html: str, **kwargs: Any) -> None:
        self.contenido = (html, kwargs)

    async def emulate_media(self, **kwargs: Any) -> None:
        self.media = kwargs

    async def close(self) -> None:
        self.cerrada = True


class _ContextoFalso:
    def __init__(self) -> None:
        self.paginas: list[_PaginaFalsa] = []

    async def new_page(self) -> _PaginaFalsa:
        pagina = _PaginaFalsa()
        self.paginas.append(pagina)
        return pagina


def _portal(pagina: _PaginaFalsa | None = None) -> tuple[CcmaPortal, _PaginaFalsa, _ContextoFalso]:
    pagina = pagina or _PaginaFalsa()
    contexto = _ContextoFalso()
    return CcmaPortal(pagina, context=contexto), pagina, contexto


def test_cargar_periodo_respeta_el_orden_de_v1_con_arrow_right() -> None:
    portal, pagina, _ = _portal()

    asyncio.run(portal.calcular_deuda("04/2000"))

    assert pagina.acciones == [
        ("click_rol", "cell", "PERÍODO DESDE (MM/AAAA)"),
        ("click", "input[name='perdesde2']", {}),
        ("press", "input[name='perdesde2']", "ArrowRight"),
        ("fill", "input[name='perdesde2']", "04/2000"),
        ("click_rol", "button", "CALCULO DE DEUDA"),
    ]


def test_ir_a_cuenta_corriente_navega_y_espera_el_resumen() -> None:
    portal, pagina, _ = _portal()

    asyncio.run(portal.ir_a_cuenta_corriente("https://servicios2.afip.gob.ar/x"))

    assert pagina.goto_urls == ["https://servicios2.afip.gob.ar/x"]
    assert pagina.args_js == [{"summarySelector": RESUMEN_CELDA}]


def test_seleccionar_rango_es_tolerante_si_no_existe_el_selector() -> None:
    portal, pagina, _ = _portal()
    pagina.clics_fallan.add("select[name='rango']")

    asyncio.run(portal.seleccionar_rango("48"))

    assert ("select_option", "select[name='rango']", "48") in pagina.acciones


def test_avanzar_usa_clic_normal_y_espera_domcontentloaded() -> None:
    portal, pagina, _ = _portal()

    assert asyncio.run(portal.avanzar()) is True

    assert ("scroll", MOVIMIENTOS_SIGUIENTE) in pagina.acciones
    assert ("click", MOVIMIENTOS_SIGUIENTE, {"timeout": 3_000}) in pagina.acciones
    assert pagina.esperas_load_state == [("domcontentloaded", {"timeout": 1_500})]


def test_avanzar_reintenta_forzado_y_luego_element_handle() -> None:
    portal, pagina, _ = _portal()
    pagina.clics_fallan.add(MOVIMIENTOS_SIGUIENTE)

    assert asyncio.run(portal.avanzar()) is True

    clics = [a for a in pagina.acciones if a[0] == "click" and a[1] == MOVIMIENTOS_SIGUIENTE]
    assert clics == [
        ("click", MOVIMIENTOS_SIGUIENTE, {"timeout": 3_000}),
        ("click", MOVIMIENTOS_SIGUIENTE, {"timeout": 3_000, "force": True}),
    ]
    assert ("element_handle", MOVIMIENTOS_SIGUIENTE) in pagina.acciones
    assert ("handle_click", MOVIMIENTOS_SIGUIENTE) in pagina.acciones
    assert pagina.esperas_load_state == [("domcontentloaded", {"timeout": 1_500})]


def test_avanzar_cae_al_clic_por_js_y_respeta_el_resultado() -> None:
    portal, pagina, _ = _portal()
    pagina.clics_fallan.add(MOVIMIENTOS_SIGUIENTE)
    pagina.handle_falla = True
    pagina.evaluate_resultado = True

    assert asyncio.run(portal.avanzar()) is True
    assert any("querySelector" in js for js in pagina.js)

    # Si el elemento no existe, V1 devuelve False en lugar de dar exito.
    pagina.js.clear()
    pagina.evaluate_resultado = False
    assert asyncio.run(portal.avanzar()) is False


def test_resumen_arma_los_xpath_de_v1_y_falla_si_falta_un_campo() -> None:
    portal, pagina, _ = _portal()
    datos = {
        "cuit": CUIT,
        "periodo": "04/2000",
        "deuda_capital": "$ 1,00",
        "deuda_accesorios": "$ 0,50",
        "total_deuda": "$ 1,50",
        "credito_capital": "$ 0,00",
        "credito_accesorios": "$ 0,00",
        "total_a_favor": "$ 0,00",
    }
    pagina.evaluate_resultado = datos

    resultado = asyncio.run(portal.resumen(CUIT, "04/2000"))

    assert resultado == {**datos, "periodo_desde": "04/2000"}
    js = pagina.js[-1]
    assert pagina.args_evaluate[-1] == {"cuit": CUIT, "summarySelector": RESUMEN_CELDA}
    base = (
        "/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/table/"
        "tbody/tr[2]/td/table/tbody/tr"
    )
    assert base in js
    for sufijo in (
        "[2]/td[2]/table/tbody/tr/td[1]",
        "[3]/td[2]/table/tbody/tr/td[1]",
        "[2]/td[4]/table/tbody/tr/td[1]",
        "[3]/td[4]/table/tbody/tr/td[1]",
    ):
        assert sufijo in js

    # Un campo vacio invalida el resumen completo (V1).
    pagina.evaluate_resultado = {**datos, "total_deuda": ""}
    with pytest.raises(TargetUnavailableError) as excinfo:
        asyncio.run(portal.resumen(CUIT, "04/2000"))
    for campo in CAMPOS_RESUMEN:
        if campo != "total_deuda":
            assert campo not in str(excinfo.value)
    assert "total_deuda" in str(excinfo.value)


def test_estado_y_filas_usan_los_selectores_de_v1() -> None:
    portal, pagina, _ = _portal()

    asyncio.run(portal.estado())
    asyncio.run(portal.filas())

    assert pagina.args_evaluate[0] == {
        "tableSelector": MOVIMIENTOS_TABLA,
        "rowSelector": MOVIMIENTOS_FILA,
        "nextSelector": MOVIMIENTOS_SIGUIENTE,
    }
    assert pagina.args_evaluate[1] == {
        "tableSelector": MOVIMIENTOS_TABLA,
        "rowSelector": MOVIMIENTOS_FILA,
    }


def test_imprimir_pdf_usa_las_opciones_de_v1_en_una_pestana_auxiliar(tmp_path: Path) -> None:
    portal, _, contexto = _portal()
    destino = tmp_path / "ccma.pdf"

    asyncio.run(portal.imprimir_pdf("<html>ok</html>", destino))

    assert len(contexto.paginas) == 1
    auxiliar = contexto.paginas[0]
    assert auxiliar.pdf_kwargs["format"] == "A4"
    assert auxiliar.pdf_kwargs["landscape"] is True
    assert auxiliar.pdf_kwargs["print_background"] is True
    assert auxiliar.pdf_kwargs["margin"] == {
        "top": "10mm",
        "bottom": "10mm",
        "left": "8mm",
        "right": "8mm",
    }
    assert auxiliar.pdf_kwargs["path"] == str(destino)
