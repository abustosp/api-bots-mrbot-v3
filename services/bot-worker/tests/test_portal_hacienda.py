"""Pruebas unitarias del port de Hacienda sin navegador ni credenciales."""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.hacienda.plugin import ID_ARTEFACTO_CONSOLIDADO, HaciendaPlugin
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals.hacienda import HaciendaPortal, _similitud_nombre


class _Elemento:
    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.clicks = 0

    async def count(self) -> int:
        return 1

    @property
    def first(self) -> "_Elemento":
        return self

    def nth(self, indice: int) -> "_Elemento":
        if indice:
            raise IndexError(indice)
        return self

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
    def __init__(self, elementos: list[_Elemento] | None = None) -> None:
        self.elementos = elementos or []
        self.frames: list[Any] = []
        self.main_frame = None
        self.url = "https://comprobantes.afip.gob.ar/"

    def get_by_role(self, _: str, name: Any = None, exact: bool = False, **__: Any) -> _Grupo:
        if name is None:
            return _Grupo(self.elementos)
        if isinstance(name, re.Pattern):
            return _Grupo([item for item in self.elementos if name.search(item.texto)])
        if exact:
            return _Grupo([item for item in self.elementos if item.texto == name])
        return _Grupo(
            [item for item in self.elementos if str(name).lower() in item.texto.lower()]
        )

    def locator(self, _: str) -> _Grupo:
        return _Grupo()

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None


def test_portal_hacienda_se_registra_por_descubrimiento() -> None:
    assert portal_para("hacienda") is HaciendaPortal
    assert PORTALES["hacienda"] is HaciendaPortal
    assert HaciendaPlugin.manifest.operaciones == ("consultar",)
    assert ID_ARTEFACTO_CONSOLIDADO == HaciendaPlugin.manifest.artefactos_produce[0].nombre


def test_similitud_denominacion_normaliza_acentos_y_siglas() -> None:
    assert _similitud_nombre("Compañía del Sur S.A.", "COMPANIA DEL SUR S.A.") == 1.0
    assert _similitud_nombre("Compañía del Sur S.A.", "Otra Empresa") < 0.9


def test_selecciona_denominacion_por_coincidencia_normalizada() -> None:
    correcta = _Elemento("COMPANIA DEL SUR S.A.")
    pagina = _Pagina([_Elemento("Otra Empresa"), correcta])
    portal = HaciendaPortal(pagina)

    asyncio.run(portal.seleccionar_denominacion("Compañía del Sur S.A."))

    assert correcta.clicks == 1


def test_mapea_filas_de_tabla_a_diccionarios() -> None:
    filas = HaciendaPortal._filas_de_tablas(
        [{"headers": ["Fecha", "Importe"], "rows": [["01/09/2026", "$123"]]}],
        "por_emisor",
        2,
    )

    assert filas == [
        {
            "consulta": "por_emisor",
            "pagina": 2,
            "Fecha": "01/09/2026",
            "Importe": "$123",
        }
    ]


def test_leer_resultados_usa_filas_sin_esperar_la_conversion_sincrona() -> None:
    portal = HaciendaPortal(_Pagina())

    async def tablas_actuales() -> list[dict[str, Any]]:
        return [{"headers": ["Fecha"], "rows": [["01/09/2026"]]}]

    async def no_hay_siguiente() -> bool:
        return False

    portal._tablas_actuales = tablas_actuales  # type: ignore[method-assign]
    portal._siguiente_pagina = no_hay_siguiente  # type: ignore[method-assign]

    assert asyncio.run(portal._leer_resultados("por_emisor")) == [
        {"consulta": "por_emisor", "pagina": 1, "Fecha": "01/09/2026"}
    ]


def test_consultar_rechaza_tipo_desconocido() -> None:
    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(
            HaciendaPortal(_Pagina()).consultar(
                consulta_key="otro", desde="01/01/2026", hasta="31/01/2026"
            )
        )
    assert exc.value.diagnostic_code == "hacienda_query_invalid"


def test_plugin_abre_mis_comprobantes_con_portal_hacienda() -> None:
    class _ServicioHacienda:
        async def seleccionar_denominacion(self, _: str) -> None:
            return None

        async def abrir_hacienda(self, **_: Any) -> object:
            return object()

    class _Sesion:
        def __init__(self) -> None:
            self.llamada_open_service: tuple[str, dict[str, Any]] | None = None

        async def login(self) -> None:
            return None

        async def open_service(self, servicio: str, **kwargs: Any) -> _ServicioHacienda:
            self.llamada_open_service = (servicio, kwargs)
            return _ServicioHacienda()

    sesion = _Sesion()

    class _Factory:
        @asynccontextmanager
        async def arca_session(self, **_: Any):
            yield sesion

    async def no_op(*_: Any, **__: Any) -> None:
        return None

    runtime = SimpleNamespace(
        credentials=SimpleNamespace(clave="clave-falsa"),
        cancellation=SimpleNamespace(raise_if_cancelled=no_op),
        event_sink=SimpleNamespace(progress=no_op),
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        browser_factory=_Factory(),
        proxy=None,
    )
    plugin = HaciendaPlugin()

    async def consultar_falso(*_: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return {"por_emisor": {"filas": 0}}, []

    plugin._consultar = consultar_falso  # type: ignore[method-assign]
    entrada = asyncio.run(
        plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": "20123456789",
                "denominacion": "Empresa de Prueba S.A.",
                "fecha_desde": "01/01/2026",
                "fecha_hasta": "31/01/2026",
                "por_emisor": True,
                "por_receptor": False,
            }
        )
    )
    asyncio.run(plugin.execute(entrada, runtime))

    assert sesion.llamada_open_service == (
        "COMPROBANTES EN LÍNEA",
        {"portal": "hacienda"},
    )
