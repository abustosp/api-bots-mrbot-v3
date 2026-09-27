"""Pruebas unitarias del portal Mis Facilidades (sólo lectura)."""
from __future__ import annotations

import asyncio
import zipfile
from xml.etree import ElementTree as ET

from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.mis_facilidades import MisFacilidadesPortal


class _Locator:
    def __init__(self, cantidad: int = 0) -> None:
        self._cantidad = cantidad

    @property
    def first(self) -> "_Locator":
        return self

    async def count(self) -> int:
        return self._cantidad

    async def wait_for(self, **_: object) -> None:
        return None

    async def is_visible(self, **_: object) -> bool:
        return self._cantidad > 0


class _Page:
    def __init__(self, filas: list[dict[str, str]]) -> None:
        self.filas = filas

    def locator(self, selector: str) -> _Locator:
        return _Locator(1 if selector == "table.searchTable" else 0)

    async def evaluate(self, script: str) -> list[dict[str, str]]:
        assert "table.searchTable tbody tr" in script
        return self.filas


class _Opcion:
    def __init__(self, valor: str, etiqueta: str) -> None:
        self.valor = valor
        self.etiqueta = etiqueta

    async def get_attribute(self, nombre: str) -> str:
        return self.valor if nombre == "value" else ""

    async def text_content(self) -> str:
        return self.etiqueta


class _Opciones:
    def __init__(self, opciones: list[_Opcion]) -> None:
        self.opciones = opciones

    async def count(self) -> int:
        return len(self.opciones)

    def nth(self, indice: int) -> _Opcion:
        return self.opciones[indice]


class _Combo(_Locator):
    def __init__(self, opciones: list[_Opcion]) -> None:
        super().__init__(1)
        self.opciones = _Opciones(opciones)
        self.seleccion: tuple[str, str] | None = None

    def locator(self, _: str) -> _Opciones:
        return self.opciones

    async def select_option(self, *, value: str = "", label: str = "") -> None:
        self.seleccion = (value, label)


class _PaginaSeleccion:
    def __init__(self, combo: _Combo) -> None:
        self.combo = combo

    async def wait_for_load_state(self, *_: object, **__: object) -> None:
        return None

    def locator(self, selector: str) -> _Combo:
        assert selector == "#ContentPlaceHolder1_ddlCUIT"
        return self.combo

    def get_by_role(self, *_: object, **__: object) -> _Locator:
        return _Locator(0)


def test_registro_y_listado_de_planes() -> None:
    assert portal_para("mis_facilidades") is MisFacilidadesPortal
    page = _Page([
        {
            "numero": "P-001",
            "denominacion": "Contribuyente de prueba",
            "situacion": "Plan Vigente",
            "detalle_id": "ContentPlaceHolder1_rpt_detallePlan_0",
        },
        {
            "numero": "P-002",
            "denominacion": "Contribuyente de prueba",
            "situacion": "Plan Cancelado",
            "detalle_id": "ContentPlaceHolder1_rpt_detallePlan_1",
        },
        {
            "numero": "P-003",
            "denominacion": "Contribuyente de prueba",
            "situacion": "Plan sin detalle",
            "detalle_id": "",
        },
    ])

    planes = asyncio.run(MisFacilidadesPortal(page).listar_planes())

    assert [plan["numero"] for plan in planes] == ["P-001", "P-002"]
    assert planes[0]["situacion"] == "Plan Vigente"


def test_selecciona_representado_en_combo_especifico() -> None:
    combo = _Combo([_Opcion("42", "Empresa ficticia 20-12345678-9")])
    portal = MisFacilidadesPortal(_PaginaSeleccion(combo))

    asyncio.run(portal.seleccionar_representado("20-12345678-9"))

    assert combo.seleccion == ("42", "")


def test_xlsx_es_openxml_valido_y_con_varias_hojas(tmp_path) -> None:
    destino = tmp_path / "planes.xlsx"
    secciones = [
        {"titulo": "Pagos", "headers": ["Fecha", "Importe"], "rows": [["2026-01-01", "100"]]},
        {"titulo": "Cuotas", "headers": ["Nro", "Estado"], "rows": [["1", "Pagada"]]},
    ]

    MisFacilidadesPortal._escribir_xlsx(destino, secciones, "P-001")
    MisFacilidadesPortal._validar_archivo(
        destino, b"PK\x03\x04", "mis_facilidades_xlsx_empty"
    )

    with zipfile.ZipFile(destino) as libro:
        assert libro.testzip() is None
        raiz = ET.fromstring(libro.read("xl/workbook.xml"))
        hojas = raiz.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets/{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")
        assert [hoja.attrib["name"] for hoja in hojas] == ["Pagos", "Cuotas"]
        hoja_pagos = ET.fromstring(libro.read("xl/worksheets/sheet1.xml"))
        textos = [n.text for n in hoja_pagos.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
        assert textos == ["Fecha", "Importe", "2026-01-01", "100"]


def test_pdf_html_escapa_datos_del_plan() -> None:
    html = MisFacilidadesPortal._html_reporte(
        "<plan>",
        [{"titulo": "Pagos", "headers": ["Importe"], "rows": [["<script>"]]}],
    )

    assert "&lt;plan&gt;" in html
    assert "&lt;script&gt;" in html
