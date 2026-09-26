from __future__ import annotations

import zipfile

from bot_worker.bots.mis_comprobantes.plugin import (
    filtrar_csv_por_rango,
    materializar_csv_descargado,
)


def test_materializar_csv_descargado_extrae_zip_de_arca(tmp_path):
    descarga = tmp_path / "comprobantes.crudo.csv"
    contenido = "Fecha;Tipo;Importe\n2025-01-01;E;10\n"
    archivo_zip = tmp_path / "origen.zip"
    with zipfile.ZipFile(archivo_zip, "w") as paquete:
        paquete.writestr("comprobantes.csv", contenido)
    archivo_zip.replace(descarga)

    materializar_csv_descargado(descarga)

    assert descarga.read_text(encoding="utf-8") == contenido


def test_filtrar_csv_por_rango_acepta_csv_arca_semicolon_y_fecha_iso(tmp_path):
    origen = tmp_path / "origen.csv"
    destino = tmp_path / "destino.csv"
    origen.write_text(
        "Fecha;Tipo;Importe\n"
        "2024-12-31;E;1\n"
        "2025-01-01;E;2\n"
        "2025-12-31;E;3\n"
        "2026-01-01;E;4\n",
        encoding="utf-8",
    )

    filas = filtrar_csv_por_rango(
        origen, destino, "01/01/2025", "31/12/2025"
    )

    assert filas == 2
    assert destino.read_text(encoding="utf-8").splitlines() == [
        "Fecha;Tipo;Importe",
        "2025-01-01;E;2",
        "2025-12-31;E;3",
    ]


def test_filtrar_csv_por_rango_conserva_csv_coma_y_fecha_local(tmp_path):
    origen = tmp_path / "origen.csv"
    destino = tmp_path / "destino.csv"
    origen.write_text(
        "Fecha,Tipo\n01/01/2025,E\n31/12/2025,E\n01/01/2026,E\n",
        encoding="utf-8",
    )

    filas = filtrar_csv_por_rango(
        origen, destino, "01/01/2025", "31/12/2025"
    )

    assert filas == 2
    assert destino.read_text(encoding="utf-8").splitlines() == [
        "Fecha,Tipo",
        "01/01/2025,E",
        "31/12/2025,E",
    ]
