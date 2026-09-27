"""Pruebas del port MOA sin navegador ni credenciales reales."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.moa.plugin import MoaPlugin
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals.moa import MoaPortal


def test_portal_moa_se_registra_por_descubrimiento() -> None:
    assert portal_para("moa") is MoaPortal
    assert PORTALES["moa"] is MoaPortal


def test_navegar_arbol_completa_seleccion_y_abre_declaracion() -> None:
    portal = MoaPortal(object())
    portal._esperar = AsyncMock()  # type: ignore[method-assign]
    portal._seleccionar_opcion_arbol = AsyncMock()  # type: ignore[method-assign]
    portal._ingresar_arbol = AsyncMock()  # type: ignore[method-assign]
    portal._abrir_declaracion_detallada = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(
        portal.navegar_arbol_empresa(
            "20123456789", "AGENTE DE PRUEBA", "ROL DE PRUEBA"
        )
    )

    assert portal._seleccionar_opcion_arbol.await_args_list[0].args == (
        0,
        "20123456789",
    )
    assert portal._seleccionar_opcion_arbol.await_args_list[1].args == (
        1,
        "AGENTE DE PRUEBA",
    )
    assert portal._seleccionar_opcion_arbol.await_args_list[2].args == (
        2,
        "ROL DE PRUEBA",
    )
    portal._ingresar_arbol.assert_awaited_once()
    portal._abrir_declaracion_detallada.assert_awaited_once()


@pytest.mark.parametrize("metodo", ["url", "form"])
def test_scrapear_despacho_retorna_las_dos_secciones(metodo: str) -> None:
    portal = MoaPortal(object())
    portal._buscar_despacho = AsyncMock()  # type: ignore[method-assign]
    portal._abrir_seccion = AsyncMock()  # type: ignore[method-assign]
    portal._extraer_datos_tablas = AsyncMock(  # type: ignore[method-assign]
        side_effect=[{"DUA": "dato ficticio"}, {"Estado": "presentado"}]
    )
    portal._volver_al_formulario = AsyncMock()  # type: ignore[method-assign]

    resultado = asyncio.run(portal.scrapear_despacho("TEST-123", metodo))

    assert resultado["despacho"] == "TEST-123"
    assert resultado["metodo"] == metodo
    assert resultado["datos_caratula"] == {"DUA": "dato ficticio"}
    assert resultado["estado_presentacion"] == {"Estado": "presentado"}
    portal._buscar_despacho.assert_awaited_once_with("TEST-123", metodo)
    assert portal._abrir_seccion.await_count == 2
    assert portal._volver_al_formulario.await_count == (1 if metodo == "form" else 0)


def test_scrapear_despacho_rechaza_metodo_invalido() -> None:
    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(MoaPortal(object()).scrapear_despacho("TEST-123", "otro"))
    assert exc.value.diagnostic_code == "moa_method_invalid"


def test_scrapear_despacho_rechaza_detalle_vacio() -> None:
    portal = MoaPortal(object())
    portal._buscar_despacho = AsyncMock()  # type: ignore[method-assign]
    portal._abrir_seccion = AsyncMock()  # type: ignore[method-assign]
    portal._extraer_datos_tablas = AsyncMock(return_value={})  # type: ignore[method-assign]

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(portal.scrapear_despacho("TEST-123"))

    assert exc.value.diagnostic_code == "moa_dispatch_details_empty"


def test_busqueda_diagnostica_formulario_ausente() -> None:
    class _CampoAusente:
        async def count(self) -> int:
            return 0

    class _Pagina:
        async def goto(self, *_: Any, **__: Any) -> None:
            return None

        async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
            return None

        def locator(self, _: str) -> _CampoAusente:
            return _CampoAusente()

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(MoaPortal(_Pagina())._buscar_despacho("TEST-123", "url"))

    assert exc.value.diagnostic_code == "moa_search_form_missing"


def test_plugin_abre_servicio_con_portal_moa() -> None:
    class _Servicio:
        pass

    class _Sesion:
        llamada_open_service: tuple[str, dict[str, Any]] | None = None

        async def login(self) -> None:
            return None

        async def open_service(self, nombre: str, **kwargs: Any) -> _Servicio:
            self.llamada_open_service = (nombre, kwargs)
            return _Servicio()

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
    )
    plugin = MoaPlugin()

    async def consultar_falso(*_: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        return {"datos": []}, []

    plugin._consultar = consultar_falso  # type: ignore[method-assign]
    entrada = asyncio.run(
        plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": "20123456789",
                "despachos": ["TEST-123"],
                "subir_csv": False,
            }
        )
    )
    asyncio.run(plugin.execute(entrada, runtime))

    assert sesion.llamada_open_service == (
        "MOA – REINGENIERIA",
        {"portal": "moa"},
    )
