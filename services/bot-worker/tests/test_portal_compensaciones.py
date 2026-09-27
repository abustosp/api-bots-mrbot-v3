from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.compensaciones.plugin import CompensacionesPlugin
from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.compensaciones import (
    URL_COMPENSACIONES,
    CompensacionesPortal,
)


class _Elemento:
    def __init__(
        self,
        nombre: str,
        visible: bool,
        eventos: list[str],
        accion: Any = None,
    ) -> None:
        self.nombre = nombre
        self.visible = visible
        self.eventos = eventos
        self.accion = accion
        self.valor = ""

    async def is_visible(self, **_: Any) -> bool:
        return self.visible

    async def click(self, **_: Any) -> None:
        if not self.visible:
            raise TimeoutError(self.nombre)
        self.eventos.append(self.nombre)
        if self.accion:
            self.accion()

    async def fill(self, valor: str, **_: Any) -> None:
        self.valor = valor
        self.eventos.append(f"{self.nombre}={valor}")


class _Grupo:
    def __init__(self, elementos: list[_Elemento] | None = None) -> None:
        self.elementos = elementos or []

    @property
    def first(self) -> _Elemento:
        return self.elementos[0] if self.elementos else _Elemento("vacío", False, [])

    def nth(self, indice: int) -> _Elemento:
        return self.elementos[indice]

    async def count(self) -> int:
        return len(self.elementos)


class _Descarga:
    suggested_filename = "compensaciones.csv"

    async def save_as(self, destino: str) -> None:
        Path(destino).write_bytes(b"fecha,importe\n01/09/2026,10\n")


class _EventoDescarga:
    async def __aenter__(self) -> "_EventoDescarga":
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    @property
    def value(self) -> Any:
        async def obtener() -> _Descarga:
            return _Descarga()

        return obtener()


class _Pagina:
    def __init__(self, *, sin_resultados: bool = False, exportar: bool = True) -> None:
        self.eventos: list[str] = []
        self.sin_resultados = sin_resultados
        self.hay_exportar = exportar
        self.menu_abierto = False
        self.url = "https://ctacte.cloud.afip.gob.ar/"
        self.fechas = [
            _Elemento("desde", True, self.eventos),
            _Elemento("hasta", True, self.eventos),
        ]

    def _elemento(self, nombre: str, visible: bool) -> _Grupo:
        accion = None
        if nombre == "CONSULTAR":
            accion = lambda: self.eventos.append("consulta_enviada")
        elif nombre == "Exportar":
            accion = lambda: setattr(self, "menu_abierto", True)
        elemento = _Elemento(nombre, visible, self.eventos, accion)
        return _Grupo([elemento] if visible else [])

    def get_by_role(self, rol: str, *, name: Any, **_: Any) -> _Grupo:
        patron = name.pattern if isinstance(name, re.Pattern) else str(name)
        if re.search("CONSULTAR", patron, re.I):
            return self._elemento("CONSULTAR", rol in {"button", "link"})
        if re.search("Exportar", patron, re.I):
            return self._elemento("Exportar", self.hay_exportar)
        if re.search("No\\s+se\\s+encontraron", patron, re.I):
            return self._elemento("sin_resultados", self.sin_resultados)
        formato = next((f for f in ("XLS", "CSV", "PDF") if f in patron.upper()), None)
        if formato:
            return self._elemento(formato, self.menu_abierto)
        return _Grupo()

    def get_by_text(self, patron: Any, **_: Any) -> _Grupo:
        texto = patron.pattern if isinstance(patron, re.Pattern) else str(patron)
        if re.search("No\\s+se\\s+encontraron", texto, re.I):
            return self._elemento("sin_resultados", self.sin_resultados)
        formato = next((f for f in ("XLS", "CSV", "PDF") if f in texto.upper()), None)
        if formato:
            return self._elemento(formato, self.menu_abierto)
        return _Grupo()

    def locator(self, selector: str, *, has_text: Any = None) -> _Grupo:
        if selector == "input[name='fecha']":
            return _Grupo(self.fechas)
        if "d-empty" in selector:
            return self._elemento("sin_resultados", self.sin_resultados)
        if "CONSULTAR" in selector:
            return self._elemento("CONSULTAR", True)
        if "Exportar" in selector or "bt-imp" in selector:
            return self._elemento("Exportar", self.hay_exportar)
        for formato in ("XLS", "CSV", "PDF"):
            if formato in selector:
                return self._elemento(formato, self.menu_abierto)
        return _Grupo()

    async def goto(self, url: str, **_: Any) -> None:
        self.url = url
        self.eventos.append("goto")

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_timeout(self, *_: Any, **__: Any) -> None:
        return None

    def expect_download(self, **_: Any) -> _EventoDescarga:
        return _EventoDescarga()


def test_portal_compensaciones_se_registra_por_descubrimiento() -> None:
    assert portal_para("compensaciones") is CompensacionesPortal


def test_consultar_aplica_fechas_y_devuelve_resultados() -> None:
    pagina = _Pagina()
    portal = CompensacionesPortal(pagina)

    resultado = asyncio.run(portal.consultar("01/09/2026", "30/09/2026"))

    assert resultado == "con_resultados"
    assert pagina.url == URL_COMPENSACIONES
    assert "desde=01/09/2026" in pagina.eventos
    assert "hasta=30/09/2026" in pagina.eventos
    assert "consulta_enviada" in pagina.eventos


def test_consultar_reconoce_mensaje_de_sin_resultados() -> None:
    portal = CompensacionesPortal(_Pagina(sin_resultados=True))

    assert asyncio.run(portal.consultar("01/09/2026", "30/09/2026")) == "sin_resultados"


def test_exportar_guarda_descarga_no_vacia(tmp_path: Path) -> None:
    pagina = _Pagina()
    portal = CompensacionesPortal(pagina)
    destino = tmp_path / "compensaciones.csv"

    resultado = asyncio.run(portal.exportar("CSV", destino))

    assert resultado == destino
    assert destino.stat().st_size > 0
    assert "CSV" in pagina.eventos


def test_exportar_rechaza_formato_no_soportado(tmp_path: Path) -> None:
    portal = CompensacionesPortal(_Pagina())

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.exportar("JSON", tmp_path / "salida.json"))

    assert error.value.diagnostic_code == "compensaciones_format_unknown"


def test_plugin_abre_portal_y_delega_seleccion_consulta_y_exportacion(
    tmp_path: Path,
) -> None:
    llamadas: list[Any] = []

    class _Servicio:
        async def seleccionar_representado(self, cuit: str) -> None:
            llamadas.append(("representado", cuit))

        async def consultar(self, desde: str, hasta: str) -> str:
            llamadas.append(("consultar", desde, hasta))
            return "con_resultados"

        async def exportar(self, formato: str, destino: Path) -> Path:
            llamadas.append(("exportar", formato))
            destino.write_bytes(b"xls")
            return destino

    servicio = _Servicio()

    class _Sesion:
        async def login(self) -> None:
            return None

        async def open_service(self, nombre: str, **kwargs: Any) -> _Servicio:
            llamadas.append(("open_service", nombre, kwargs))
            return servicio

    @asynccontextmanager
    async def arca_session(**_: Any):
        yield _Sesion()

    async def no_op(*_: Any, **__: Any) -> None:
        return None

    runtime = SimpleNamespace(
        credentials=SimpleNamespace(
            cuit_representante="20111222333", clave="clave-ficticia"
        ),
        cancellation=SimpleNamespace(raise_if_cancelled=no_op),
        event_sink=SimpleNamespace(progress=no_op),
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        browser_factory=SimpleNamespace(arca_session=arca_session),
        artifact_store=SimpleNamespace(resolve=lambda nombre: tmp_path / nombre),
        proxy=None,
    )
    plugin = CompensacionesPlugin()
    entrada = asyncio.run(
        plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": "20123456789",
                "fecha_desde": "01/09/2026",
                "fecha_hasta": "30/09/2026",
                "excel": True,
                "subir": False,
            }
        )
    )

    resultado = asyncio.run(plugin.execute(entrada, runtime))

    assert llamadas[0] == (
        "open_service",
        "SISTEMA DE CUENTAS",
        {"portal": "compensaciones"},
    )
    assert ("representado", "20123456789") in llamadas
    assert ("consultar", "01/09/2026", "30/09/2026") in llamadas
    assert ("exportar", "XLS") in llamadas
    assert resultado.result == "OK"
    assert resultado.data["xls"]["archivo"].endswith("compensaciones.xls")
