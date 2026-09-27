"""Portales portados de V1/V2: selección de representado y contratos de bot."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals.base import PortalArca, solo_digitos
from bot_worker.runtime.portals.sct import FORMATOS, PESTANAS, SctPortal
from bot_worker.runtime.portals.consulta_pagos_vep import ConsultaPagosVepPortal
from bot_worker.runtime.portals.siper import SiperPortal
from bot_worker.runtime.portals.srt import ALICUOTAS_URL, SrtPortal

CUIT = "20123456789"


class _Opcion:
    def __init__(self, texto: str, valor: str = "") -> None:
        self._texto = texto
        self._valor = valor

    async def text_content(self) -> str:
        return self._texto

    async def get_attribute(self, nombre: str) -> str:
        return self._valor if nombre == "value" else ""


class _Combo:
    def __init__(self, opciones: list[_Opcion]) -> None:
        self._opciones = opciones
        self.seleccionado: str | None = None

    async def count(self) -> int:
        return len(self._opciones)

    @property
    def first(self) -> "_Combo":
        return self

    def nth(self, indice: int) -> "_Opcion":
        return self._opciones[indice]

    async def wait_for(self, **_: Any) -> None:
        return None

    def locator(self, _: str) -> "_Combo":
        return self

    async def select_option(self, *, value: str = "", label: str = "") -> None:
        self.seleccionado = value or label


class _Lista:
    def __init__(self, visible: bool) -> None:
        self._visible = visible

    async def count(self) -> int:
        return 1 if self._visible else 0

    @property
    def first(self) -> "_Lista":
        return self

    async def is_visible(self, **_: Any) -> bool:
        return self._visible

    async def scroll_into_view_if_needed(self, **_: Any) -> None:
        return None

    async def click(self, **_: Any) -> None:
        return None

    async def element_handle(self) -> None:
        return None

    async def inner_text(self, **_: Any) -> str:
        return ""


class _PaginaFake:
    """Página mínima: un combo cuando se pide, y opcionalmente una lista."""

    def __init__(self, combo: _Combo | None = None, lista: bool = False) -> None:
        self._combo = combo
        self._lista = _Lista(lista)
        self.url = "https://portal.invalid/x"

    def locator(self, selector: str) -> Any:
        if selector.startswith("select") and self._combo is not None:
            return self._combo
        if "nombre-propio" in selector:
            return _Lista(False)
        return self._lista

    def get_by_role(self, *_: Any, **__: Any) -> Any:
        return self._lista

    async def wait_for_timeout(self, *_: Any) -> None:
        return None

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None


def test_registro_de_portales() -> None:
    assert portal_para("sct") is SctPortal
    assert portal_para("SRT") is SrtPortal
    assert portal_para("desconocido") is None
    assert set(PORTALES) == {"consulta_pagos_vep", "sct", "sifere", "siper", "srt"}
    assert portal_para("consulta_pagos_vep") is ConsultaPagosVepPortal
    assert portal_para("siper") is SiperPortal


def test_solo_digitos_y_seleccion_de_representado_por_combo() -> None:
    combo = _Combo([_Opcion("OTRO", "1"), _Opcion("PEREZ 20-12345678-9", "2")])
    portal = PortalArca(_PaginaFake(combo=combo), service_name="sct")

    asyncio.run(portal.seleccionar_representado(CUIT))

    assert combo.seleccionado == "2"
    assert solo_digitos("20-12345678-9") == "20123456789"


def test_representado_invalido_y_no_seleccionable() -> None:
    portal = PortalArca(_PaginaFake(), service_name="sct")
    with pytest.raises(TargetUnavailableError) as invalido:
        asyncio.run(portal.seleccionar_representado("123"))
    assert invalido.value.diagnostic_code == "represented_cuit_invalid"

    with pytest.raises(TargetUnavailableError) as ausente:
        asyncio.run(portal.seleccionar_representado(CUIT))
    assert ausente.value.diagnostic_code == "represented_cuit_not_selectable"


def test_seleccion_por_lista_visible() -> None:
    portal = PortalArca(_PaginaFake(lista=True), service_name="portal")
    asyncio.run(portal.seleccionar_representado(CUIT))


def test_sct_valida_seccion_y_formato() -> None:
    assert set(PESTANAS) == {"vencimientos", "deudas", "ddjj_pendientes"}
    assert FORMATOS["xlsx"][0] == "XLS" and FORMATOS["csv"] == ("CSV",)
    portal = SctPortal(_PaginaFake(), service_name="sct")
    with pytest.raises(TargetUnavailableError) as seccion:
        asyncio.run(portal.descargar_reporte("otra", "csv", "x.csv"))
    assert seccion.value.diagnostic_code == "sct_section_unknown"
    with pytest.raises(TargetUnavailableError) as formato:
        asyncio.run(portal.descargar_reporte("vencimientos", "docx", "x.docx"))
    assert formato.value.diagnostic_code == "sct_format_unknown"


def test_srt_valida_cuit_y_url_del_portal() -> None:
    portal = SrtPortal(_PaginaFake(), service_name="srt")
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.consultar_cuit("123"))
    assert error.value.diagnostic_code == "srt_cuit_invalid"
    assert ALICUOTAS_URL.startswith("https://eservicios.srt.gob.ar/")
