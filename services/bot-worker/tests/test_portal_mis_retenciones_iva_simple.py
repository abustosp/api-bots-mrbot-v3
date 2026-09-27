from __future__ import annotations

import asyncio
import csv
from typing import Any
from unittest.mock import AsyncMock, patch

from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.mis_retenciones import MisRetencionesPortal
from bot_worker.runtime.portals.mis_retenciones_iva_simple import (
    MisRetencionesIvaSimplePortal,
    _rango_iva_simple_valido,
)

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

    async def get_attribute(self, *_: Any, **__: Any) -> str:
        return "true"

    async def is_checked(self) -> bool:
        return self.checked

    async def check(self, **_: Any) -> None:
        self.checked = True

    async def uncheck(self, **_: Any) -> None:
        self.checked = False


class _Page:
    url = "https://portal.invalid/"

    def __init__(self) -> None:
        self._controls = {
            "#selectImpuestos": _Loc(),
            "input[type='checkbox'][value='siap']": _Loc(checked=True),
            "input[type='checkbox'][value='ivaSimple']": _Loc(checked=False),
            "#navBarMisRetenciones-relationCuil": _Loc(CUIT_FICTICIO),
            "#navBarMisRetenciones": _Loc(CUIT_FICTICIO),
        }

    def locator(self, selector: str, **_: Any) -> _Loc:
        return self._controls.get(selector, _Loc(visible=False))

    def get_by_text(self, *_: Any, **__: Any) -> _Loc:
        return _Loc(visible=False)

    def get_by_role(self, *_: Any, **__: Any) -> _Loc:
        return _Loc(visible=False)

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


class _DateField:
    def __init__(self) -> None:
        self.value = ""
        self.entered = False

    async def fill(self, value: str) -> None:
        self.value = value

    async def press(self, key: str) -> None:
        self.entered = key == "Enter"


class _DateFields:
    def __init__(self) -> None:
        self.fields = [_DateField(), _DateField()]
        self.first = self

    def nth(self, index: int) -> _DateField:
        return self.fields[index]

    async def count(self) -> int:
        return len(self.fields)

    async def wait_for(self, **_: Any) -> None:
        return None


class _DatePage(_Page):
    def __init__(self) -> None:
        super().__init__()
        self.date_fields = _DateFields()

    def locator(self, selector: str, **_: Any) -> Any:
        if selector == "input[placeholder='dd/mm/aaaa']":
            return self.date_fields
        return super().locator(selector)


def test_portal_iva_simple_registrado_y_hereda_seleccion_representado() -> None:
    assert portal_para("mis_retenciones_iva_simple") is MisRetencionesIvaSimplePortal
    assert issubclass(MisRetencionesIvaSimplePortal, MisRetencionesPortal)
    portal = MisRetencionesIvaSimplePortal(_Page())
    asyncio.run(portal.seleccionar_representado(CUIT_FICTICIO))
    assert portal._representado_cuit == CUIT_FICTICIO


def test_activar_modo_iva_simple_cambia_los_checks_como_v2() -> None:
    page = _Page()
    portal = MisRetencionesIvaSimplePortal(page)
    asyncio.run(portal.activar_modo_iva_simple())
    assert page._controls["input[type='checkbox'][value='ivaSimple']"].checked
    assert not page._controls["input[type='checkbox'][value='siap']"].checked
    assert portal._iva_simple_activo


def test_rango_iva_simple_respeta_el_ultimo_dia_del_mes_siguiente() -> None:
    assert _rango_iva_simple_valido("31/01/2026", "28/02/2026")
    assert not _rango_iva_simple_valido("31/01/2026", "01/03/2026")
    assert not _rango_iva_simple_valido("02/02/2026", "01/02/2026")
    assert not _rango_iva_simple_valido("fecha inválida", "28/02/2026")


def test_fechas_iva_simple_usa_fill_y_enter_en_ambos_datepickers() -> None:
    page = _DatePage()
    portal = MisRetencionesIvaSimplePortal(page)

    asyncio.run(portal._seleccionar_fechas_iva_simple("31/01/2026", "28/02/2026"))

    assert [field.value for field in page.date_fields.fields] == ["31/01/2026", "28/02/2026"]
    assert all(field.entered for field in page.date_fields.fields)


def test_formato_iva_simple_reintenta_con_excel_como_en_v2() -> None:
    portal = MisRetencionesIvaSimplePortal(_Page())
    with patch.object(
        MisRetencionesPortal,
        "_seleccionar_formato",
        new=AsyncMock(side_effect=[False, True]),
    ) as seleccionar:
        assert asyncio.run(portal._seleccionar_formato("iva_simple"))

    assert [call.args[0] for call in seleccionar.await_args_list] == ["iva_simple", "xls"]


def test_spreadsheetml_de_arca_se_normaliza_a_csv(tmp_path: Any) -> None:
    archivo = tmp_path / "retenciones.csv"
    archivo.write_text(
        """<?xml version='1.0'?>
<Workbook xmlns:ss='urn:schemas-microsoft-com:office:spreadsheet'>
  <Worksheet><Table>
    <Row><Cell><Data>Columna A</Data></Cell><Cell ss:Index='3'><Data>Columna C</Data></Cell></Row>
    <Row><Cell><Data>valor</Data></Cell><Cell ss:Index='3'><Data>dato</Data></Cell></Row>
  </Table></Worksheet>
</Workbook>""",
        encoding="utf-8",
    )

    MisRetencionesIvaSimplePortal._normalizar_exportacion_csv(archivo)

    with archivo.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    assert rows == [["Columna A", "", "Columna C"], ["valor", "", "dato"]]
