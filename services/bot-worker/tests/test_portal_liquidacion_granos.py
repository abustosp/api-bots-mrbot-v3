from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree

from bot_worker.runtime.portals.liquidacion_granos import (
    LiquidacionGranosPortal,
    _guardar_xlsx,
)


def test_planilla_xlsx_es_valida_y_preserva_valores(tmp_path: Path) -> None:
    destino = tmp_path / "planilla.xlsx"

    resultado = _guardar_xlsx(
        destino,
        ["Fecha", "Producto"],
        [["27/09/2026", "Maíz & trigo"]],
    )

    assert resultado == destino
    assert destino.stat().st_size > 0
    with zipfile.ZipFile(destino) as libro:
        assert libro.testzip() is None
        xml = libro.read("xl/worksheets/sheet1.xml")
    raiz = ElementTree.fromstring(xml)
    textos = [nodo.text for nodo in raiz.iter() if nodo.tag.endswith("}t")]
    assert textos == ["Fecha", "Producto", "27/09/2026", "Maíz & trigo"]


def test_planilla_vacia_genera_un_xlsx_informativo(tmp_path: Path) -> None:
    destino = _guardar_xlsx(tmp_path / "vacio.xlsx", ["Resultado"], [])

    with zipfile.ZipFile(destino) as libro:
        assert libro.testzip() is None
        contenido = libro.read("xl/worksheets/sheet1.xml")
    assert b"Sin registros" in contenido


def test_extrae_encabezados_y_filas_de_la_tabla_mas_completa() -> None:
    encabezados, filas = LiquidacionGranosPortal._filas_de_tablas(
        [
            {"headers": ["Menú"], "rows": []},
            {"headers": ["Fecha", "COE"], "rows": [["27/09/2026", "123"]]},
        ]
    )

    assert encabezados == ["Fecha", "COE"]
    assert filas == [["27/09/2026", "123"]]


def test_pdf_solo_se_solicita_en_el_mismo_origen_https() -> None:
    misma_origen = LiquidacionGranosPortal._misma_origen

    assert misma_origen("https://servicios.afip.gob.ar/lpg/", "https://servicios.afip.gob.ar/lpg/a.pdf")
    assert not misma_origen("https://servicios.afip.gob.ar/lpg/", "https://otro.example/a.pdf")
    assert not misma_origen("https://servicios.afip.gob.ar/lpg/", "http://servicios.afip.gob.ar/a.pdf")
