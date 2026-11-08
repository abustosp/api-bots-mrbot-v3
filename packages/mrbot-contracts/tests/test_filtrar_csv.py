import csv

import pytest

from mrbot_contracts.planillas import filtrar_csv_por_rango


def test_filtra_en_la_misma_ruta_y_descarta_campos_sobrantes(tmp_path):
    archivo = tmp_path / "planilla.csv"
    archivo.write_bytes(
        'Fecha;Detalle\n31/12/2025;anterior\n2026-01-01;Peña;extra\n'
        '31/01/2026;último\nfecha inválida;ignorar\n01/02/2026;posterior\n'.encode("cp1252")
    )
    assert filtrar_csv_por_rango(archivo, archivo, "2026-01-01", "31/01/2026") == 2
    with archivo.open(encoding="utf-8", newline="") as entrada:
        assert list(csv.reader(entrada, delimiter=";")) == [
            ["Fecha", "Detalle"], ["2026-01-01", "Peña"], ["31/01/2026", "último"]
        ]


def test_sin_encabezado_de_fecha_no_modifica_destino(tmp_path):
    origen, destino = tmp_path / "origen", tmp_path / "destino"
    origen.write_text("Otra;Columna\na;b\n")
    destino.write_text("anterior")
    with pytest.raises(ValueError, match="columna de fecha"):
        filtrar_csv_por_rango(origen, destino, "01/01/2026", "31/01/2026")
    assert destino.read_text() == "anterior"
