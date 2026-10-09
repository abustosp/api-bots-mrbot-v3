"""Puntos de venta y ``timeout_mc`` en la pantalla de Mis Comprobantes (V1).

V1 llena el campo ``Punto de Venta`` con la lista unida por comas y espera el
botón ``CSV`` ``timeout_mc`` segundos (30 por defecto). El orden importa: en
``descargar_mis_comprobantes`` el filtro se aplica antes de ``Aplicar`` y en
``solicitar_consulta_mis_comprobantes`` después, tal como lo hace el bot V1.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from bot_worker.runtime.arca_login import ArcaServicePage


class _Accion:
    def __init__(self, page: "_Pagina", nombre: str) -> None:
        self._page = page
        self._nombre = nombre

    async def click(self, **_kwargs: Any) -> None:
        self._page.eventos.append(("click", self._nombre, _kwargs.get("timeout")))

    async def fill(self, valor: str, **_kwargs: Any) -> None:
        self._page.eventos.append(("fill", self._nombre, valor))

    async def count(self) -> int:
        return 1

    @property
    def first(self) -> "_Accion":
        return self


class _Descarga:
    def __init__(self, page: "_Pagina") -> None:
        self._page = page

    async def save_as(self, path: str) -> None:
        self._page.eventos.append(("guardar", path, None))
        Path(path).write_bytes(b"Fecha;Tipo\n2026-01-10;1\n")


class _EsperaDescarga:
    def __init__(self, page: "_Pagina") -> None:
        self._page = page

    def __init_subclass__(cls) -> None:  # pragma: no cover - solo tipado
        return None

    async def __aenter__(self) -> "_EsperaDescarga":
        self._page.eventos.append(("esperar_descarga", None, None))
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    @property
    async def value(self) -> _Descarga:
        return _Descarga(self._page)


class _Respuesta:
    status = 200
    url = "https://fes.afip.gob.ar/mis-comprobantes/ajax.do?f=generarConsulta&t=E"

    async def text(self) -> str:
        return json.dumps({"datos": {"idConsulta": "150229278"}})


class _EsperaRespuesta:
    def __init__(self, page: "_Pagina") -> None:
        self._page = page

    async def __aenter__(self) -> "_EsperaRespuesta":
        self._page.eventos.append(("esperar_respuesta", None, None))
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    @property
    async def value(self) -> _Respuesta:
        return _Respuesta()


class _Pagina:
    """Página mínima con la interfaz que usa ``ArcaServicePage``."""

    def __init__(self) -> None:
        self.eventos: list[tuple[str, Any, Any]] = []

    def get_by_label(self, nombre: str) -> _Accion:
        return _Accion(self, f"label:{nombre}")

    def get_by_role(self, rol: str, name: str = "") -> _Accion:
        return _Accion(self, f"{rol}:{name}")

    def get_by_text(self, texto: str, exact: bool = False) -> _Accion:
        return _Accion(self, f"texto:{texto}")

    def locator(self, selector: str) -> _Accion:
        return _Accion(self, selector)

    def expect_download(self, timeout: int = 0) -> _EsperaDescarga:
        self.eventos.append(("timeout_descarga", None, timeout))
        return _EsperaDescarga(self)

    def expect_response(self, _predicado: Any, timeout: int = 0) -> _EsperaRespuesta:
        self.eventos.append(("timeout_respuesta", None, timeout))
        return _EsperaRespuesta(self)

    async def wait_for_load_state(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _valor(eventos: list[tuple[str, Any, Any]], nombre: str) -> Any:
    for evento in eventos:
        if evento[0] == "fill" and evento[1] == nombre:
            return evento[2]
    return None


def _posicion(eventos: list[tuple[str, Any, Any]], nombre: str) -> int:
    """Posición del primer evento (click o fill) sobre ``nombre``."""
    for indice, evento in enumerate(eventos):
        if evento[1] == nombre:
            return indice
    raise AssertionError(f"no se interactuó con {nombre}: {eventos}")


def test_descargar_csv_llena_puntos_venta_antes_de_aplicar_y_usa_timeout_mc(tmp_path: Path) -> None:
    pagina = _Pagina()
    servicio = ArcaServicePage(pagina)
    destino = tmp_path / "crudo.csv"

    asyncio.run(
        servicio.descargar_csv(
            "emitidos",
            destino,
            "01/01/2026",
            "31/01/2026",
            ["1", "002"],
            timeout_ms=45_000,
        )
    )

    assert _valor(pagina.eventos, "textbox:Punto de Venta") == "1,002"
    assert _posicion(pagina.eventos, "textbox:Punto de Venta") < _posicion(
        pagina.eventos, "button:Aplicar"
    )
    timeout = next(t for e, _, t in pagina.eventos if e == "timeout_descarga")
    assert timeout == 45_000
    assert ("click", "button:CSV", 45_000) in pagina.eventos
    assert destino.read_bytes().startswith(b"Fecha;Tipo")
    # Vuelve al menú principal después de descargar, como V1.
    assert _posicion(pagina.eventos, "texto:Menú Principal") > _posicion(
        pagina.eventos, "button:CSV"
    )


def test_descargar_csv_sin_puntos_venta_usa_el_default_de_v1(tmp_path: Path) -> None:
    pagina = _Pagina()

    asyncio.run(
        ArcaServicePage(pagina).descargar_csv(
            "recibidos", tmp_path / "crudo.csv", "01/01/2026", "31/01/2026"
        )
    )

    assert _valor(pagina.eventos, "textbox:Punto de Venta") is None
    timeout = next(t for e, _, t in pagina.eventos if e == "timeout_descarga")
    assert timeout == 30_000


def test_solicitar_consulta_llena_puntos_venta_despues_de_aplicar() -> None:
    pagina = _Pagina()

    identificador = asyncio.run(
        ArcaServicePage(pagina).solicitar_consulta(
            "emitidos", "01/01/2026", "31/01/2026", ["00003"]
        )
    )

    assert identificador == "150229278"
    assert _valor(pagina.eventos, "textbox:Punto de Venta") == "00003"
    assert _posicion(pagina.eventos, "button:Aplicar") < _posicion(
        pagina.eventos, "textbox:Punto de Venta"
    )
    assert _posicion(pagina.eventos, "textbox:Punto de Venta") < _posicion(
        pagina.eventos, "button:Buscar"
    )
    timeout = next(t for e, _, t in pagina.eventos if e == "timeout_respuesta")
    assert timeout == 30_000
