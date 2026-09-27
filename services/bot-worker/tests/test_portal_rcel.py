"""Pruebas unitarias del port RCEL sin navegador ni credenciales."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.registry import get_plugin
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals.rcel import RcelPortal, _similitud_nombre


class _Elemento:
    def __init__(self, texto: str = "") -> None:
        self.texto = texto
        self.clicks = 0
        self.valor: str | None = None

    async def count(self) -> int:
        return 1

    @property
    def first(self) -> "_Elemento":
        return self

    def nth(self, indice: int) -> "_Elemento":
        raise IndexError(indice)

    async def is_visible(self, **_: Any) -> bool:
        return True

    async def inner_text(self, **_: Any) -> str:
        return self.texto

    async def text_content(self, **_: Any) -> str:
        return self.texto

    async def get_attribute(self, _: str) -> str:
        return ""

    async def scroll_into_view_if_needed(self, **_: Any) -> None:
        return None

    async def click(self, **_: Any) -> None:
        self.clicks += 1

    async def fill(self, valor: str) -> None:
        self.valor = valor


class _Grupo:
    def __init__(self, elementos: list[_Elemento] | None = None) -> None:
        self.elementos = elementos or []

    async def count(self) -> int:
        return len(self.elementos)

    @property
    def first(self) -> _Elemento:
        return self.elementos[0]

    def nth(self, indice: int) -> _Elemento:
        return self.elementos[indice]


class _Pagina:
    def __init__(self, botones: list[_Elemento] | None = None) -> None:
        self.botones = botones or []
        self.frames: list[Any] = []
        self.main_frame = None
        self.url = "https://fe.afip.gob.ar/rcel/"

    def get_by_role(self, _: str, name: Any = None, **__: Any) -> _Grupo:
        if isinstance(name, re.Pattern):
            return _Grupo([e for e in self.botones if name.search(e.texto)])
        return _Grupo(self.botones)

    def locator(self, _: str) -> _Grupo:
        return _Grupo()

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


def test_portal_rcel_se_registra_por_descubrimiento() -> None:
    assert portal_para("rcel") is RcelPortal
    assert "rcel" in PORTALES
    familia = get_plugin("rcel").manifest.artefactos_produce[0]
    assert familia.nombre == "rcel"
    assert familia.content_types == ("application/pdf",)


def test_match_de_denominacion_normaliza_acentos_y_texto_extra() -> None:
    assert _similitud_nombre("Compañía del Sur S.A.", "COMPANIA DEL SUR SA") == 1.0
    assert _similitud_nombre("GERMAN BOLZAN", "GERMAN BOLZAN 20-12345678-9") == 1.0
    assert _similitud_nombre("GERMAN BOLZAN", "OTRA EMPRESA") < 0.9


def test_selecciona_empresa_por_nombre_y_rechaza_cuit_distinto() -> None:
    correcta = _Elemento("Compañía del Sur S.A.")
    pagina = _Pagina([_Elemento("Otra Empresa"), correcta])
    portal = RcelPortal(pagina)

    assert asyncio.run(portal._seleccionar_por_nombre("COMPANIA DEL SUR SA"))
    assert correcta.clicks == 1

    async def encabezado() -> tuple[str, str]:
        return "20987654321", "Compañía del Sur S.A."

    portal._leer_empresa_encabezado = encabezado  # type: ignore[method-assign]
    assert not asyncio.run(portal._verificar_empresa("20123456789", "Compañía del Sur S.A."))


def test_nombre_representado_es_requerido() -> None:
    portal = RcelPortal(_Pagina())
    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(portal.seleccionar_representado("20123456789"))
    assert exc.value.diagnostic_code == "rcel_company_name_missing"


def test_descargar_facturas_sin_resultados_devuelve_lista_vacia(tmp_path: Path) -> None:
    consultas = _Elemento("Consultas")
    buscar = _Elemento("Buscar")
    desde = _Elemento()
    hasta = _Elemento()

    class PaginaFormulario(_Pagina):
        def get_by_role(self, role: str, name: Any = None, **kwargs: Any) -> _Grupo:
            if role == "button" and isinstance(name, re.Pattern):
                candidatos = [consultas, buscar]
                return _Grupo([e for e in candidatos if name.search(e.texto)])
            return _Grupo()

        def get_by_label(self, nombre: str, **_: Any) -> _Elemento:
            return desde if nombre == "Desde" else hasta

    portal = RcelPortal(PaginaFormulario())
    resultado = asyncio.run(
        portal.descargar_facturas("01/01/2026", "31/01/2026", tmp_path)
    )

    assert resultado == []
    assert consultas.clicks == 1 and buscar.clicks == 1
    assert desde.valor == "01/01/2026" and hasta.valor == "31/01/2026"
