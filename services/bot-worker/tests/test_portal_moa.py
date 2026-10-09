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


def test_volver_espera_a_que_reaparezca_el_formulario() -> None:
    class _Campo:
        def __init__(self) -> None:
            self.llamadas = 0

        async def count(self) -> int:
            self.llamadas += 1
            return int(self.llamadas > 1)

        @property
        def first(self) -> "_Campo":
            return self

        async def is_visible(self, **_: Any) -> bool:
            return True

    class _Pagina:
        def __init__(self) -> None:
            self.campo = _Campo()

        def locator(self, _: str) -> _Campo:
            return self.campo

        def get_by_role(self, *_: Any, **__: Any) -> object:
            return object()

    portal = MoaPortal(_Pagina())
    portal._clickear = AsyncMock(return_value=True)  # type: ignore[method-assign]
    portal._esperar = AsyncMock()  # type: ignore[method-assign]

    asyncio.run(portal._volver_al_formulario())

    assert portal._clickear.await_count == 2


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


# --- Paridad V1: aplanado de caratula/estado y despachos con error -------


def test_csv_aplana_caratula_y_estado_en_columnas_separadas(tmp_path) -> None:
    import csv

    from bot_worker.bots.moa.plugin import MoaPlugin, _fila_despacho

    registro = {
        "despacho": "TEST-1",
        "metodo": "url",
        "timestamp": "2026-10-08T00:00:00+00:00",
        "datos_caratula": {"DUA": "dato ficticio", "Aduana": "001"},
        "estado_presentacion": {"Estado": "presentado"},
        "error": None,
    }
    destino = tmp_path / "moa.csv"
    MoaPlugin._escribir_csv(destino, [_fila_despacho(registro)])

    with destino.open(encoding="utf-8", newline="") as fh:
        cabecera = next(csv.reader(fh))
    with destino.open(encoding="utf-8", newline="") as fh:
        filas = list(csv.DictReader(fh))

    assert filas[0]["caratula_DUA"] == "dato ficticio"
    assert filas[0]["caratula_Aduana"] == "001"
    assert filas[0]["estado_Estado"] == "presentado"
    assert filas[0]["error"] == ""
    assert "datos_caratula" not in cabecera
    assert "estado_presentacion" not in cabecera
    assert all("{" not in valor for valor in filas[0].values())


def test_csv_incluye_columna_error_para_despacho_fallido(tmp_path) -> None:
    import csv

    from bot_worker.bots.errors import TargetUnavailableError
    from bot_worker.bots.moa.plugin import (
        MoaPlugin,
        _fila_despacho,
        _registro_despacho_con_error,
    )

    registro = _registro_despacho_con_error(
        "TEST-9", "url", TargetUnavailableError("no apareció en resultados")
    )
    destino = tmp_path / "moa.csv"
    MoaPlugin._escribir_csv(destino, [_fila_despacho(registro)])

    with destino.open(encoding="utf-8", newline="") as fh:
        filas = list(csv.DictReader(fh))

    assert filas[0]["despacho"] == "TEST-9"
    assert filas[0]["error"] == "no apareció en resultados"
    assert filas[0]["timestamp"]
    assert "caratula_DUA" not in filas[0]


def _runtime_moa(tmp_path):
    from bot_worker.runtime.context import ArtifactStore

    class _Servicio:
        def __init__(self, respuestas: dict[str, Any]) -> None:
            self.respuestas = respuestas

        async def navegar_arbol_empresa(self, **_: Any) -> None:
            return None

        async def scrapear_despacho(self, despacho: str, metodo: str) -> dict[str, Any]:
            valor = self.respuestas[despacho]
            if isinstance(valor, Exception):
                raise valor
            return valor

    async def no_op(*_: Any, **__: Any) -> None:
        return None

    runtime = SimpleNamespace(
        cancellation=SimpleNamespace(raise_if_cancelled=no_op),
        event_sink=SimpleNamespace(progress=no_op),
        artifact_store=ArtifactStore(tmp_path, {}),
    )
    return runtime, _Servicio


def test_despacho_con_error_no_falla_el_job_y_se_reporta(tmp_path) -> None:
    from bot_worker.bots.errors import TargetUnavailableError

    runtime, Servicio = _runtime_moa(tmp_path)
    servicio = Servicio({
        "OK-1": {
            "despacho": "OK-1",
            "metodo": "url",
            "timestamp": "t",
            "datos_caratula": {"DUA": "x"},
            "estado_presentacion": {},
        },
        "MAL-2": TargetUnavailableError("no apareció en resultados MOA"),
    })
    plugin = MoaPlugin()
    entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
        "despachos": ["OK-1", "MAL-2"],
        "subir_csv": False,
    }))[1]

    datos, artefactos = asyncio.run(plugin._consultar(servicio, entrada, runtime))

    assert datos["despachos_procesados"] == 2
    assert datos["despachos_exitosos"] == 1
    assert datos["despachos_con_error"] == 1
    assert datos["errores"][0]["despacho"] == "MAL-2"
    # Mismo shape que V1: cada despacho aparece en ``datos`` con ``error``.
    assert [r["despacho"] for r in datos["datos"]] == ["OK-1", "MAL-2"]
    assert datos["datos"][0]["error"] is None
    assert datos["datos"][1]["error"] == "no apareció en resultados MOA"
    assert artefactos == []


def test_todos_los_despachos_con_error_siguen_siendo_exito_del_job(tmp_path) -> None:
    from bot_worker.bots.errors import TargetUnavailableError

    runtime, Servicio = _runtime_moa(tmp_path)
    servicio = Servicio({
        "MAL-1": TargetUnavailableError("detalle vacio"),
        "MAL-2": TargetUnavailableError("detalle vacio"),
    })
    plugin = MoaPlugin()
    entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
        "despachos": ["MAL-1", "MAL-2"],
        "subir_csv": False,
    }))[1]

    datos, _ = asyncio.run(plugin._consultar(servicio, entrada, runtime))

    assert datos["despachos_exitosos"] == 0
    assert datos["despachos_con_error"] == 2
    assert len(datos["errores"]) == 2
    assert len(datos["datos"]) == 2
    # El CSV conserva una fila por despacho fallido, con su error.
    csv_path = tmp_path / "MOA - 20123456789.csv"
    assert csv_path.is_file()
    assert "detalle vacio" in csv_path.read_text(encoding="utf-8")
