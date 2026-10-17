"""Pruebas del portal SCT: insignia de pestana (port de V1 tab_badge_count).

Sin navegador: el marco y las pestanas son falsos.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.sct.plugin import SctPlugin
from bot_worker.runtime.portals.sct import PESTANAS, SctPortal


class _Nodo:
    """Elemento con texto e insignia opcional; ``count`` es 0 si no existe."""

    def __init__(self, texto: str = "", *, presente: bool = True, insignia: _Nodo | None = None) -> None:
        self.texto = texto
        self.presente = presente
        self._insignia = insignia

    @property
    def first(self) -> _Nodo:
        return self

    async def count(self) -> int:
        return 1 if self.presente else 0

    async def is_visible(self, **_: Any) -> bool:
        return self.presente

    async def inner_text(self, **_: Any) -> str:
        return self.texto

    def locator(self, _: str) -> _Nodo:
        if self._insignia is not None:
            return self._insignia
        return _Nodo(presente=False)


class _Marco:
    """Marco con pestanas indexadas por su rotulo visible."""

    def __init__(self, pestanas: dict[str, _Nodo]) -> None:
        self.pestanas = pestanas

    def get_by_role(self, _: str, *, name: Any = None, **__: Any) -> _Nodo:
        for rotulo, pestana in self.pestanas.items():
            if isinstance(name, re.Pattern) and name.search(rotulo):
                return pestana
        return _Nodo(presente=False)


class _PaginaSct:
    def __init__(self, pestanas: dict[str, _Nodo]) -> None:
        self.marco = _Marco(pestanas)

    def frame_locator(self, _: str) -> _Marco:
        return self.marco

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


class _Iframe:
    def __init__(self, visible: bool) -> None:
        self.visible = visible

    @property
    def first(self) -> _Iframe:
        return self

    async def count(self) -> int:
        return int(self.visible)

    async def wait_for(self, *, state: str, timeout: int) -> None:
        if state == "visible" and not self.visible:
            raise TimeoutError("iframe not visible")


class _PaginaServicio:
    def __init__(self, url: str, *, iframe_visible: bool) -> None:
        self.url = url
        self.iframe = _Iframe(iframe_visible)

    def locator(self, selector: str) -> _Iframe:
        assert selector == "iframe"
        return self.iframe

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


def test_sct_rechaza_pagina_login_antes_de_seleccionar_representado() -> None:
    portal = SctPortal(
        _PaginaServicio(
            "https://auth.afip.gob.ar/contribuyente_/login.xhtml?action=SYSTEM",
            iframe_visible=False,
        )
    )

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.seleccionar_representado("20123456789"))
    assert error.value.diagnostic_code == "sct_frame_missing"


def test_preparar_acepta_iframe_visible_en_el_host_de_sct() -> None:
    portal = SctPortal(
        _PaginaServicio(
            "https://ctacte.cloud.afip.gob.ar/contribuyente/externo",
            iframe_visible=True,
        )
    )

    asyncio.run(portal.preparar())


def _insignia(valor: str) -> _Nodo:
    return _Nodo(valor)


def test_contar_pestana_lee_la_insignia() -> None:
    pestana = _Nodo("Vencimientos", insignia=_insignia("7"))
    portal = SctPortal(_PaginaSct({"Vencimientos": pestana}))

    assert asyncio.run(portal.contar_pestana("vencimientos")) == 7


def test_contar_pestana_sin_insignia_toma_el_numero_del_texto() -> None:
    portal = SctPortal(_PaginaSct({"Deudas": _Nodo("Deudas (3)")}))

    assert asyncio.run(portal.contar_pestana("deudas")) == 3


def test_contar_pestana_con_insignia_cero_devuelve_cero() -> None:
    portal = SctPortal(_PaginaSct({"Deudas": _Nodo("Deudas", insignia=_insignia("0"))}))

    assert asyncio.run(portal.contar_pestana("deudas")) == 0


def test_contar_pestana_sin_numero_devuelve_none() -> None:
    portal = SctPortal(_PaginaSct({"DDJJ pendientes": _Nodo("DDJJ pendientes")}))

    assert asyncio.run(portal.contar_pestana("ddjj_pendientes")) is None


def test_contar_pestana_sin_pestana_visible_devuelve_none() -> None:
    portal = SctPortal(_PaginaSct({}))

    assert asyncio.run(portal.contar_pestana("vencimientos")) is None


def test_contar_pestana_seccion_desconocida_devuelve_none() -> None:
    portal = SctPortal(_PaginaSct({}))

    assert asyncio.run(portal.contar_pestana("inexistente")) is None
    assert set(PESTANAS) == {"vencimientos", "deudas", "ddjj_pendientes"}


class _ServicioSct:
    def __init__(self, badges: dict[str, int | None]) -> None:
        self.badges = badges
        self.descargas: list[tuple[str, str]] = []

    async def contar_pestana(self, seccion: str) -> int | None:
        return self.badges.get(seccion)

    async def descargar_reporte(self, *, seccion: str, formato: str, destino: Path) -> Path:
        destino.write_bytes(b"reporte")
        self.descargas.append((seccion, formato))
        return destino


class _Cancelacion:
    async def raise_if_cancelled(self) -> None:
        return None


class _Eventos:
    async def progress(self, **_: Any) -> None:
        return None


def _ejecutar(servicio: _ServicioSct, tmp_path: Path, **extra: Any) -> dict[str, Any]:
    async def _correr() -> dict[str, Any]:
        _, entrada = await SctPlugin().validate(
            {
                "operacion": "consultar",
                "representado_cuit": "20123456789",
                "secciones": ["vencimientos", "deudas"],
                "formatos": ["csv"],
                "subir": False,
                **extra,
            }
        )
        runtime = SimpleNamespace(
            cancellation=_Cancelacion(),
            event_sink=_Eventos(),
            artifact_store=SimpleNamespace(resolve=lambda nombre: tmp_path / nombre),
        )
        datos, _ = await SctPlugin()._descargar(servicio, entrada, runtime)
        return datos

    return asyncio.run(_correr())


def test_plugin_omite_seccion_con_insignia_cero(tmp_path: Path) -> None:
    servicio = _ServicioSct({"vencimientos": 4, "deudas": 0})

    datos = _ejecutar(servicio, tmp_path)

    assert servicio.descargas == [("vencimientos", "csv")]
    assert {"seccion": "deudas", "omitido": True, "motivo": "sin_registros"} in datos["reportes"]


def test_plugin_descarga_si_la_insignia_no_se_puede_leer(tmp_path: Path) -> None:
    servicio = _ServicioSct({"vencimientos": None, "deudas": None})

    _ejecutar(servicio, tmp_path)

    # El schema ordena las secciones alfabeticamente: deudas antes que vencimientos.
    assert sorted(servicio.descargas) == [("deudas", "csv"), ("vencimientos", "csv")]


def test_plugin_sin_omitir_vacias_descarga_todo(tmp_path: Path) -> None:
    servicio = _ServicioSct({"vencimientos": 0, "deudas": 0})

    _ejecutar(servicio, tmp_path, omitir_vacias=False)

    assert sorted(servicio.descargas) == [("deudas", "csv"), ("vencimientos", "csv")]


def test_nombre_de_reporte_sigue_el_patron_de_v1() -> None:
    from bot_worker.bots.sct.plugin import nombre_reporte_sct

    # V1: sct_<seccion>_<cuit>_<fecha-hora>.<ext>; el XLS nativo es .xls.
    assert nombre_reporte_sct("20-12345678-9", "vencimientos", "xlsx", marca="20260101_120000") == (
        "sct_vencimientos_20123456789_20260101_120000.xls"
    )
    assert nombre_reporte_sct("20123456789", "deudas", "csv", marca="X") == "sct_deudas_20123456789_X.csv"
    assert nombre_reporte_sct("20123456789", "ddjj_pendientes", "pdf", marca="X") == (
        "sct_ddjj_pendientes_20123456789_X.pdf"
    )


def test_xlsx_y_csv_de_una_seccion_no_se_pisan(tmp_path: Path) -> None:
    servicio = _ServicioSct({"vencimientos": 5})

    async def _correr() -> list[str]:
        _, entrada = await SctPlugin().validate({
            "operacion": "consultar",
            "representado_cuit": "20123456789",
            "secciones": ["vencimientos"],
            "formatos": ["xlsx", "csv"],
            "subir": False,
        })
        destinos: list[str] = []

        class _Store:
            def resolve(self, nombre: str) -> Path:
                destinos.append(nombre)
                return tmp_path / nombre

        runtime = SimpleNamespace(
            cancellation=_Cancelacion(),
            event_sink=_Eventos(),
            artifact_store=_Store(),
        )
        await SctPlugin()._descargar(servicio, entrada, runtime)
        return destinos

    destinos = asyncio.run(_correr())
    assert len(destinos) == 2
    assert len(set(destinos)) == 2
    assert all(d.startswith("sct_vencimientos_20123456789_") for d in destinos)
