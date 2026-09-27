"""Pruebas del port DDJJ en línea sin credenciales ni navegador real."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals.declaracion_en_linea import DeclaracionEnLineaPortal


class _Locator:
    def __init__(self, onclick: str = "") -> None:
        self.onclick = onclick

    @property
    def first(self) -> _Locator:
        return self

    async def count(self) -> int:
        return int(bool(self.onclick))

    async def get_attribute(self, nombre: str) -> str:
        return self.onclick if nombre == "onclick" else ""


class _Fila:
    def locator(self, selector: str) -> _Locator:
        if "dvVerF931" in selector:
            return _Locator("AbrirPopupF931('202503','17')")
        if "dvVerVep" in selector:
            return _Locator("AbrirPopupVep('202503','21')")
        return _Locator()


class _Pagina:
    url = "https://serviciossegsoc.afip.gob.ar/djproforma/app/consultar/dj_generadas.aspx"


def test_portal_ddjj_se_registra_y_es_de_solo_lectura() -> None:
    assert portal_para("declaracion_en_linea") is DeclaracionEnLineaPortal
    assert "declaracion_en_linea" in PORTALES
    assert hasattr(DeclaracionEnLineaPortal, "consultar_periodo")
    assert not hasattr(DeclaracionEnLineaPortal, "presentar")
    assert not hasattr(DeclaracionEnLineaPortal, "generar")


def test_normaliza_periodo_y_rechaza_mes_invalido() -> None:
    assert DeclaracionEnLineaPortal._normalizar_periodo("202503") == "202503"
    with pytest.raises(TargetUnavailableError) as error:
        DeclaracionEnLineaPortal._normalizar_periodo("202513")
    assert error.value.diagnostic_code == "declaracion_en_linea_period_invalid"


def test_resuelve_representado_solo_por_digitos() -> None:
    opciones = [
        {"value": "", "label": "Seleccione"},
        {"value": "00000000000", "label": "00000000000 - Empresa ficticia"},
    ]
    assert DeclaracionEnLineaPortal._resolver_opcion_representado(opciones, "00000000000") == "00000000000"
    assert DeclaracionEnLineaPortal._resolver_opcion_representado(opciones, "00999999999") is None


def test_construye_urls_desde_los_handlers_de_la_fila() -> None:
    portal = DeclaracionEnLineaPortal(_Pagina())
    ddjj, vep = asyncio.run(portal._urls_periodo({"row": _Fila()}, "202503"))
    assert ddjj.endswith("ver_formulario.aspx?Periodo=202503&SecDJVig=17")
    assert vep.endswith("ver_Vep.aspx?Periodo=202503&SecDJVig=21")


def test_consulta_periodo_guarda_ddjj_y_admite_vep_ausente(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    portal = DeclaracionEnLineaPortal(_Pagina())
    llamada: dict[str, Any] = {}

    async def preparar() -> None:
        return None

    async def filas() -> list[dict[str, Any]]:
        return [{"periodo": "202503", "row": object()}]

    async def urls(_fila: dict[str, Any], periodo: str) -> tuple[str, str]:
        llamada["periodo"] = periodo
        return "https://ejemplo.test/ddjj", "https://ejemplo.test/vep"

    async def guardar_ddjj(_url: str, destino: Path) -> Path:
        destino.write_bytes(b"%PDF-1.7 ficticio")
        return destino

    async def guardar_vep(_url: str, _periodo: str, _destino: Path) -> tuple[None, dict[str, Any]]:
        return None, {"estado_vep": "", "pagado": False}

    monkeypatch.setattr(portal, "preparar", preparar)
    monkeypatch.setattr(portal, "_filas_ddjj", filas)
    monkeypatch.setattr(portal, "_urls_periodo", urls)
    monkeypatch.setattr(portal, "_guardar_ddjj", guardar_ddjj)
    monkeypatch.setattr(portal, "_guardar_vep", guardar_vep)

    destino = tmp_path / "ddjj.pdf"
    resultado = asyncio.run(portal.consultar_periodo("202503", destino, tmp_path / "vep.pdf"))

    assert llamada["periodo"] == "202503"
    assert destino.read_bytes().startswith(b"%PDF-")
    assert resultado == {"estado_vep": "", "pagado": False}
