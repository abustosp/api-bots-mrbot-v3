"""Operacion ``historial`` del plugin: descarga la consulta existente, nunca pide otra.

Se prueba contra un servicio falso que registra cada llamada. Los tres
resultados posibles (OK, PARCIAL y ERROR) siguen la regla de V1: si el rango no
esta en el historial, no se inventa un CSV ni se pide una consulta nueva.
"""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.comprobantes.plugin import ComprobantesPlugin
from bot_worker.bots.mis_comprobantes.plugin import MisComprobantesPlugin
from bot_worker.runtime.context import ArtifactStore, CancellationToken

CUIT = "20320160856"


def _zip_con_planilla(filas: list[tuple[str, str]]) -> bytes:
    buffer = io.StringIO(newline="")
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow(["Fecha de Emision", "Tipo"])
    for fecha, tipo in filas:
        escritor.writerow([fecha, tipo])
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w") as paquete:
        paquete.writestr("consulta.csv", buffer.getvalue())
    return salida.getvalue()


class _ServicioHistorial:
    """Falso del portal ``comprobantes``: el historial solo tiene ``disponibles``."""

    def __init__(self, disponibles: set[str], planilla: bytes) -> None:
        self.disponibles = disponibles
        self.planilla = planilla
        self.historial_llamadas: list[str] = []
        self.consultas_nuevas: list[str] = []
        self.descargas: list[str] = []
        self.timeouts: list[int | None] = []

    async def seleccionar_representado(self, cuit: str) -> None:
        return None

    async def historial(
        self,
        *,
        tipo: str,
        desde: str,
        hasta: str,
        destino: Path,
        timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        self.historial_llamadas.append(tipo)
        self.timeouts.append(timeout_ms)
        consultas = [{"indice": 0, "estado": "Finalizada", "exportable": True}]
        if tipo not in self.disponibles:
            return {"consultas": consultas, "descargado": False}
        Path(destino).write_bytes(self.planilla)
        return {"consultas": consultas, "descargado": True}

    async def solicitar_consulta(self, **kwargs: Any) -> str:  # pragma: no cover - no debe usarse
        self.consultas_nuevas.append(kwargs["tipo"])
        return "1"

    async def descargar_csv(self, **kwargs: Any) -> None:  # pragma: no cover - no debe usarse
        self.descargas.append(kwargs["tipo"])
        raise AssertionError("historial no debe descargar desde la pantalla de consulta")


class _Sesion:
    def __init__(self, servicio: _ServicioHistorial) -> None:
        self.servicio = servicio
        self.portales: list[str | None] = []

    async def login(self) -> None:
        return None

    async def open_service(self, servicio: str, **kwargs: Any) -> _ServicioHistorial:
        self.portales.append(kwargs.get("portal"))
        return self.servicio


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self.sesion = sesion

    def arca_session(self, **_: Any) -> Any:
        sesion = self.sesion

        class _Contexto:
            async def __aenter__(self) -> _Sesion:
                return sesion

            async def __aexit__(self, *_: Any) -> bool:
                return False

        return _Contexto()


class _Sink:
    async def progress(self, **_: Any) -> None:
        return None


class _Presign:
    def __init__(self) -> None:
        self.ids: list[str] = []

    async def __call__(self, artifact_id: str, content_type: str, size: int) -> dict[str, Any]:
        self.ids.append(artifact_id)
        return {"upload_url": "", "object_key": f"jobs/x/{artifact_id}"}


def _entrada(plugin: Any, **extra: Any) -> Any:
    cuerpo = {
        "operacion": "historial",
        "representado_cuit": CUIT,
        "representado_nombre": "Empresa de Prueba",
        "fecha_desde": "01/01/2026",
        "fecha_hasta": "31/01/2026",
        "emitidos": True,
        "recibidos": False,
        "incluir_json": True,
        "subir_csv": True,
        **extra,
    }
    return asyncio.run(plugin.validate(cuerpo))


def _ejecutar(plugin: Any, servicio: _ServicioHistorial, tmp_path: Path, entrada: Any) -> tuple[Any, _Sesion, _Presign]:
    presign = _Presign()
    sesion = _Sesion(servicio)
    runtime = SimpleNamespace(
        job_id="job-historial",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia"),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path,
            slots={},
            presign=presign,
            declarados={
                "emitidos_csv": (("text/csv",), 52_428_800),
                "recibidos_csv": (("text/csv",), 52_428_800),
                "emitidos_zip": (("application/zip",), 52_428_800),
                "recibidos_zip": (("application/zip",), 52_428_800),
            },
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(sesion),
        cancellation=CancellationToken(),
    )
    return asyncio.run(plugin.execute(entrada, runtime)), sesion, presign


PLUGINS = [ComprobantesPlugin, MisComprobantesPlugin]


@pytest.mark.parametrize("plugin_cls", PLUGINS)
def test_historial_descarga_la_consulta_existente_y_no_pide_otra(plugin_cls: type, tmp_path: Path) -> None:
    planilla = _zip_con_planilla([("2026-01-10", "11")])
    servicio = _ServicioHistorial({"emitidos"}, planilla)
    plugin = plugin_cls()

    resultado, sesion, presign = _ejecutar(plugin, servicio, tmp_path, _entrada(plugin))

    assert resultado.result == "OK"
    assert servicio.historial_llamadas == ["emitidos"]
    assert servicio.consultas_nuevas == []
    assert servicio.descargas == []
    assert sesion.portales == ["comprobantes"]
    assert resultado.data["emitidos"]["filas"] == 1
    assert resultado.data["consultas"]["emitidos"][0]["estado"] == "Finalizada"
    # ``mis_comprobantes`` guarda además el ZIP original de ARCA (fuente del JSON).
    esperado = ["emitidos_zip", "emitidos_csv"] if plugin_cls is MisComprobantesPlugin else ["emitidos_csv"]
    assert presign.ids == esperado
    if plugin_cls is MisComprobantesPlugin:
        assert resultado.data["emitidos"]["zip"]["archivo"].endswith(".zip")
        assert "muestra" not in resultado.data["emitidos"]


@pytest.mark.parametrize("plugin_cls", PLUGINS)
def test_historial_sin_consulta_del_rango_da_error(plugin_cls: type, tmp_path: Path) -> None:
    servicio = _ServicioHistorial(set(), _zip_con_planilla([]))
    plugin = plugin_cls()

    resultado, _, presign = _ejecutar(plugin, servicio, tmp_path, _entrada(plugin))

    assert resultado.result == "ERROR"
    assert resultado.errors[0].internal_diagnostic == "comprobantes_historial_sin_consulta"
    assert resultado.data["faltantes"] == ["emitidos"]
    assert resultado.artifacts == []
    assert presign.ids == []


@pytest.mark.parametrize("plugin_cls", PLUGINS)
def test_historial_con_un_tipo_faltante_da_parcial(plugin_cls: type, tmp_path: Path) -> None:
    servicio = _ServicioHistorial({"emitidos"}, _zip_con_planilla([("2026-01-10", "11")]))
    plugin = plugin_cls()
    entrada = _entrada(plugin, recibidos=True)

    resultado, _, _ = _ejecutar(plugin, servicio, tmp_path, entrada)

    assert resultado.result == "PARCIAL"
    assert resultado.data["faltantes"] == ["recibidos"]
    assert resultado.data["emitidos"]["filas"] == 1
    assert "recibidos" in resultado.warnings[0]
    assert servicio.historial_llamadas == ["emitidos", "recibidos"]


class _ServicioConsulta(_ServicioHistorial):
    """Falso de la consulta normal: ``descargar_csv`` entrega un ZIP real."""

    async def descargar_csv(
        self,
        *,
        tipo: str,
        destino: Path,
        desde: str,
        hasta: str,
        puntos_venta: list[str] | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        self.descargas.append(tipo)
        self.timeouts.append(timeout_ms)
        Path(destino).write_bytes(_zip_con_planilla([("2026-01-10", "11")]))


@pytest.mark.parametrize("plugin_cls", PLUGINS)
def test_consultar_sigue_usando_el_servicio_sin_portal(plugin_cls: type, tmp_path: Path) -> None:
    servicio = _ServicioConsulta({"emitidos"}, b"")
    plugin = plugin_cls()
    entrada = asyncio.run(
        plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": CUIT,
                "representado_nombre": "Empresa de Prueba",
                "fecha_desde": "01/01/2026",
                "fecha_hasta": "31/01/2026",
                "emitidos": True,
                "subir_csv": False,
                "incluir_json": True,
            }
        )
    )

    resultado, sesion, _ = _ejecutar(plugin, servicio, tmp_path, entrada)

    assert resultado.result == "OK"
    assert sesion.portales == [None]
    assert servicio.descargas == ["emitidos"]
    assert servicio.historial_llamadas == []
