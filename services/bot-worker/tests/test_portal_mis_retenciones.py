from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.mis_retenciones import MisRetencionesPortal, _IMPUESTOS

CUIT_FICTICIO = "20123456789"


class _Loc:
    def __init__(self, texto: str = "", *, visible: bool = True, checked: bool = False) -> None:
        self.texto = texto
        self.visible = visible
        self.checked = checked

    @property
    def first(self) -> _Loc:
        return self

    async def count(self) -> int:
        return int(self.visible)

    async def text_content(self) -> str:
        return self.texto

    async def is_visible(self, **_: Any) -> bool:
        return self.visible

    async def wait_for(self, **_: Any) -> None:
        return None

    async def click(self, **_: Any) -> None:
        return None

    async def is_checked(self) -> bool:
        return self.checked

    async def check(self, **_: Any) -> None:
        self.checked = True

    async def uncheck(self, **_: Any) -> None:
        self.checked = False


class _Page:
    url = "https://portal.invalid/"

    def __init__(self) -> None:
        self.nav = _Loc(CUIT_FICTICIO)

    def locator(self, selector: str, **_: Any) -> _Loc:
        if selector in {
            "#navBarMisRetenciones-relationCuil",
            "#navBarMisRetenciones",
        }:
            return self.nav
        return _Loc(visible=False)

    def get_by_text(self, *_: Any, **__: Any) -> _Loc:
        return _Loc(visible=False)

    def get_by_role(self, *_: Any, **__: Any) -> _Loc:
        return _Loc(visible=False)

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


def test_portal_registrado_y_seleccion_de_representado_activo() -> None:
    assert portal_para("mis_retenciones") is MisRetencionesPortal
    portal = MisRetencionesPortal(_Page())
    asyncio.run(portal.seleccionar_representado(CUIT_FICTICIO))
    assert portal._representado_cuit == CUIT_FICTICIO


def test_cuit_invalido_no_intenta_navegar() -> None:
    portal = MisRetencionesPortal(_Page())
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.seleccionar_representado("123"))
    assert error.value.diagnostic_code == "represented_cuit_invalid"


def test_representado_sin_confirmar_no_corta_el_job() -> None:
    """V1/V2 no cortaban: `_ensure_relation` devolvía bool y el bot seguía."""
    class _PageSinRelacion(_Page):
        def locator(self, selector: str, **_: Any) -> _Loc:
            return _Loc(visible=False)

    portal = MisRetencionesPortal(_PageSinRelacion())

    assert asyncio.run(portal.seleccionar_representado(CUIT_FICTICIO)) is False


def test_nombre_impuesto_v2_se_resuelve_por_codigo() -> None:
    assert _IMPUESTOS["217"] == "217 - SICORE-IMPTO.A LAS GANANCIAS"


def test_recortar_hasta_futuro_a_hoy() -> None:
    from datetime import date

    from bot_worker.runtime.portals.mis_retenciones import _recortar_a_hoy

    hoy = date(2026, 9, 27)
    assert _recortar_a_hoy("30/09/2026", hoy) == "27/09/2026"
    assert _recortar_a_hoy("31/08/2026", hoy) == "31/08/2026"


def test_xlsx_de_arca_con_nombre_csv_se_normaliza_a_csv(tmp_path: Any) -> None:
    import csv
    import zipfile

    hoja = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="inlineStr"><is><t>CUIT</t></is></c>'
        '<c r="C1" t="inlineStr"><is><t>Importe</t></is></c></row>'
        '<row r="2"><c r="A2" t="inlineStr"><is><t>30541583420</t></is></c>'
        '<c r="C2"><v>332196.97</v></c></row></sheetData></worksheet>'
    )
    archivo = tmp_path / "retenciones.csv"
    with zipfile.ZipFile(archivo, "w") as libro:
        libro.writestr("xl/worksheets/sheet1.xml", hoja)

    MisRetencionesPortal._normalizar_exportacion_csv(archivo)

    with archivo.open(encoding="utf-8-sig", newline="") as stream:
        assert list(csv.reader(stream)) == [["CUIT", "", "Importe"], ["30541583420", "", "332196.97"]]
