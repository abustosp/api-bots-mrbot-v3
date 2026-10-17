from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.libros_portal_iva.plugin import IDS_POR_OPERACION, LibrosPortalIvaPlugin
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals.libros_portal_iva import LibrosPortalIvaPortal


class _Celda:
    def __init__(self, texto: str) -> None:
        self.texto = texto

    async def inner_text(self) -> str:
        return self.texto


class _Coleccion:
    def __init__(self, elementos: list[object]) -> None:
        self.elementos = elementos

    async def count(self) -> int:
        return len(self.elementos)

    def nth(self, indice: int) -> object:
        return self.elementos[indice]

    def locator(self, selector: str) -> "_Coleccion":
        if selector == "tbody tr":
            return self
        if selector == "td":
            return self
        raise AssertionError(selector)


class _Fila:
    def __init__(self, celdas: list[str]) -> None:
        self.celdas = _Coleccion([_Celda(texto) for texto in celdas])

    def locator(self, selector: str) -> _Coleccion:
        assert selector == "td"
        return self.celdas


class _Tabla:
    def __init__(self, filas: list[list[str]]) -> None:
        self.filas = _Coleccion([_Fila(fila) for fila in filas])

    def locator(self, selector: str) -> _Coleccion:
        assert selector == "tbody tr"
        return self.filas


class _Pagina:
    def __init__(self, filas: list[list[str]]) -> None:
        self.tablas = _Coleccion([_Tabla(filas)])

    def locator(self, selector: str) -> _Coleccion:
        assert selector == "table"
        return self.tablas


class _TextoBody:
    def __init__(self, texto: str) -> None:
        self.texto = texto

    async def inner_text(self) -> str:
        return self.texto


class _PaginaConRepresentando:
    def __init__(self, texto: str) -> None:
        self.body = _TextoBody(texto)

    def locator(self, selector: str) -> _TextoBody:
        assert selector == "body"
        return self.body


class _LocalizadorDummy:
    def filter(self, **_: object) -> "_LocalizadorDummy":
        return self


class _PaginaSinSelector:
    async def wait_for_load_state(self, **_: object) -> None:
        return None

    def get_by_title(self, *_: object, **__: object) -> _LocalizadorDummy:
        return _LocalizadorDummy()

    def get_by_role(self, *_: object, **__: object) -> _LocalizadorDummy:
        return _LocalizadorDummy()

    def get_by_text(self, *_: object, **__: object) -> _LocalizadorDummy:
        return _LocalizadorDummy()

    def locator(self, *_: object, **__: object) -> _LocalizadorDummy:
        return _LocalizadorDummy()


def test_slots_de_subida_coinciden_con_el_manifiesto() -> None:
    slots_manifestados = {
        spec.nombre.lower().replace(".", "_")
        for spec in LibrosPortalIvaPlugin.manifest.artefactos_produce
    }
    assert set(IDS_POR_OPERACION.values()) <= slots_manifestados


def test_registro_y_metodos_del_portal_libros_iva() -> None:
    assert portal_para("libros_portal_iva") is LibrosPortalIvaPortal
    assert "libros_portal_iva" in PORTALES
    assert callable(LibrosPortalIvaPortal.descargar_libros)
    assert callable(LibrosPortalIvaPortal.descargar_ddjj)
    assert callable(LibrosPortalIvaPortal.descargar_periodo)


def test_descargar_periodo_despacha_ambas_operaciones() -> None:
    portal = LibrosPortalIvaPortal(None)
    portal.descargar_libros = AsyncMock(return_value="libros.zip")
    portal.descargar_ddjj = AsyncMock(return_value="ddjj.pdf")

    assert asyncio.run(
        portal.descargar_periodo(
            operacion="descargar_libros", periodo="202501", destino="/tmp/libros.bin"
        )
    ) == "libros.zip"
    assert asyncio.run(
        portal.descargar_periodo(
            operacion="descargar_ddjj", periodo="202501", destino="/tmp/ddjj.bin"
        )
    ) == "ddjj.pdf"
    portal.descargar_libros.assert_awaited_once()
    portal.descargar_ddjj.assert_awaited_once()


def test_descargar_periodo_rechaza_mes_y_operacion_invalidos() -> None:
    portal = LibrosPortalIvaPortal(None)
    with pytest.raises(TargetUnavailableError) as periodo:
        asyncio.run(
            portal.descargar_periodo(
                operacion="descargar_libros", periodo="202513", destino="salida.bin"
            )
        )
    assert periodo.value.diagnostic_code == "libros_period_invalid"

    with pytest.raises(TargetUnavailableError) as operacion:
        asyncio.run(
            portal.descargar_periodo(
                operacion="otra", periodo="202501", destino="salida.bin"
            )
        )
    assert operacion.value.diagnostic_code == "libros_operation_unknown"


def test_lectura_filas_de_libros_y_ddjj_usa_la_columna_de_periodo_heredada() -> None:
    filas = [
        ["1", "IVA", "01/2025", "Original", "presentada"],
        ["2", "01/2025", "Libro", "Rectificativa 1", "presentada"],
        ["3", "02/2025", "Libro", "Original", "presentada"],
    ]
    portal = LibrosPortalIvaPortal(_Pagina(filas))

    libros = asyncio.run(portal._filas_periodo("202501", columna_periodo=2))
    ddjj = asyncio.run(portal._filas_periodo("202501", columna_periodo=1))

    assert [fila["fila"] for fila in libros] == [0]
    assert [fila["fila"] for fila in ddjj] == [1]
    assert libros[0]["secuencia"] == "Original"
    assert ddjj[0]["secuencia"] == "Rectificativa 1"


def test_confirmacion_de_representado_exige_el_cuit_en_la_relacion_activa() -> None:
    portal = LibrosPortalIvaPortal(
        _PaginaConRepresentando(
            "Portal IVA\nRepresentando a: 30-71816618-3\nRepresentar a:\n"
        )
    )

    assert asyncio.run(portal._representado_esta_activo("30718166183")) is True
    assert asyncio.run(portal._representado_esta_activo("27175256437")) is False


def test_no_confirma_el_representado_si_solo_esta_activo_el_representante() -> None:
    portal = LibrosPortalIvaPortal(
        _PaginaConRepresentando(
            "Portal IVA\nRepresentando a: 27-17525643-7\nRepresentar a:\n"
        )
    )

    assert asyncio.run(portal._representado_esta_activo("30718166183")) is False


def test_no_continua_si_el_selector_comun_no_deja_activo_el_representado(monkeypatch) -> None:
    from bot_worker.runtime.portals.base import PortalArca

    portal = LibrosPortalIvaPortal(_PaginaSinSelector())
    monkeypatch.setattr(
        PortalArca,
        "seleccionar_representado",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        LibrosPortalIvaPortal,
        "_representado_esta_activo",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        PortalArca,
        "_ya_esta_seleccionado",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        LibrosPortalIvaPortal,
        "_esperar",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        LibrosPortalIvaPortal,
        "_clickear",
        AsyncMock(return_value=False),
    )

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.seleccionar_representado("30718166183"))

    assert error.value.diagnostic_code == "represented_cuit_not_selectable"
