"""Datos scrapeados como CSV con ``|``: ida y vuelta exacta (RCEL y CCMA)."""

from __future__ import annotations

import pytest

from mrbot_contracts import datos_csv as d


def test_nombres_de_archivo_siguen_el_patron_pedido() -> None:
    assert d.nombre_ccma_movimientos("20326167399", "job-1") == "CCMA_movimientos_20326167399_job-1.csv"
    assert d.nombre_ccma_saldos("20326167399", "job-1") == "CCMA_saldos_20326167399_job-1.csv"
    assert d.nombre_rcel_facturas("20326167399", "job-1") == "RCEL_facturas_20326167399_job-1.csv"


def test_nombres_no_dejan_pasar_rutas_ni_caracteres_raros() -> None:
    nombre = d.nombre_ccma_saldos("../etc/pas wd", "a/b\\c")
    assert "/" not in nombre and "\\" not in nombre and " " not in nombre
    assert d.nombre_ccma_saldos("", "").startswith("CCMA_saldos_sin_dato_sin_dato")


def test_csv_usa_pipe_y_cita_solo_lo_necesario() -> None:
    texto = d.a_csv([{"a": "x|y", "b": 'di"jo', "c": "linea1\nlinea2", "d": "normal"}], ["a", "b", "c", "d"])
    assert texto.splitlines()[0] == "a|b|c|d"
    assert d.de_csv(texto) == [{"a": "x|y", "b": 'di"jo', "c": "linea1\nlinea2", "d": "normal"}]


SALDOS = {
    "cuit": "20326167399", "periodo": "04/2000 - 10/2026", "deuda_capital": "885,620.70",
    "deuda_accesorios": "89,450.39", "total_deuda": "975,071.09", "credito_capital": "2,262.05",
    "credito_accesorios": "0.00", "total_a_favor": "2,262.05",
}
MOVS = [
    {"periodo": "10/2026", "impuesto": "AUT", "concepto": "019", "subconcepto": "019",
     "descripcion": "Obligacion Mensual Autonomo.", "fecha_movimiento": "12/11/2026", "debe": "109,578.51", "haber": ""},
    {"periodo": "09/2026", "impuesto": "AUT", "concepto": "019", "subconcepto": "051",
     "descripcion": "Intereses | Resarcitorios", "fecha_movimiento": "28/10/2026", "debe": "636.65", "haber": ""},
]


def _ccma(extra=None, movimientos=None, saldos=None):
    p = {"operacion": "consultar", "representado_cuit": "20326167399", "periodo_desde": "04/2000",
         "movimientos_corte": "sin_progreso", "movimientos_completos": False,
         "sha256": "a" * 64, "size_bytes": 48601}
    p.update(SALDOS if saldos is None else saldos)
    p["movimientos"] = MOVS if movimientos is None else movimientos
    p.update(extra or {})
    return p


def test_ccma_ida_y_vuelta_exacta_y_el_json_de_la_base_queda_sin_datos() -> None:
    original = _ccma()
    reducido, archivos = d.separar_ccma(original)
    assert set(archivos) == {"saldos", "movimientos"}
    for clave in ("movimientos", "total_deuda", "deuda_capital", "total_a_favor"):
        assert clave not in reducido
    assert reducido["representado_cuit"] == "20326167399" and reducido["sha256"] == "a" * 64
    assert d.reconstruir_ccma(reducido, archivos) == original


def test_ccma_conserva_la_fila_cruda_del_fallback_de_v1() -> None:
    movs = [{"fila": "Detalle | Período | Impuesto | Saldo"}, *MOVS, {"fila": "09/2026 | 020 | 019"}]
    original = _ccma(movimientos=movs)
    reducido, archivos = d.separar_ccma(original)
    assert d.reconstruir_ccma(reducido, archivos)["movimientos"] == movs


def test_ccma_clave_de_saldo_ausente_no_reaparece_vacia() -> None:
    parcial = {"cuit": "1", "deuda_capital": "0.00"}
    original = {"operacion": "consultar", "periodo_desde": "04/2000", "movimientos": [], **parcial}
    reducido, archivos = d.separar_ccma(original)
    assert d.reconstruir_ccma(reducido, archivos) == original


def test_ccma_sin_movimientos_ni_saldos_no_genera_archivos() -> None:
    original = {"operacion": "consultar", "error_message": "x"}
    reducido, archivos = d.separar_ccma(original)
    assert archivos == {} and reducido == original
    assert d.reconstruir_ccma(reducido, archivos) == original


FACTURAS = [
    {"archivo": "f1.pdf", "sha256": "b" * 64, "size_bytes": 1234,
     "campos": {"CAE": "86406670102479", "Importe Total": "200000,00", "Nombre del Receptor": "CUTULI HERNAN JAVIER\n",
                "Domicilio Cliente": "Peña 2580 | Piso:PB", "IVA 21%": ""}},
    {"archivo": "f2.pdf", "sha256": "c" * 64, "size_bytes": 99, "campos": {"CAE": "1", "COD": "011"}},
]


@pytest.mark.parametrize("facturas", [FACTURAS[:1], FACTURAS, FACTURAS[::-1]])
def test_rcel_ida_y_vuelta_exacta_incluso_con_campos_distintos_por_factura(facturas) -> None:
    original = {"operacion": "descargar", "representado_cuit": "20263080921", "cantidad": len(facturas), "facturas": facturas}
    reducido, archivos = d.separar_rcel(original)
    assert list(archivos) == ["facturas"] and "facturas" not in reducido
    assert reducido["cantidad"] == len(facturas)
    assert d.reconstruir_rcel(reducido, archivos) == original


def test_rcel_sin_facturas_queda_igual() -> None:
    original = {"operacion": "descargar", "cantidad": 0, "facturas": []}
    reducido, archivos = d.separar_rcel(original)
    assert archivos == {} and reducido == original
