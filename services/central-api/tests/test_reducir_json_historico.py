"""Reduccion del JSON historico de CCMA/RCEL: solo si el CSV de S4 reconstruye el payload exacto."""

from __future__ import annotations

import pytest

from central_api import datos_archivo
from central_api.settings import get_settings
from central_api.tools import reducir_json_historico as rj
from mrbot_contracts import datos_csv as d

J = "01a1311e-5fcb-7e52-822e-01840c992f2e"
PAYLOAD_CCMA = {
    "representado_cuit": "20374730429",
    "total_deuda": "10,00", "credito_capital": "0,00", "credito_accesorios": "0,00", "total_a_favor": "0,00",
    "movimientos": [{"periodo": "", "impuesto": "", "concepto": "A|B", "subconcepto": "", "descripcion": "", "fecha_movimiento": "", "debe": "1,00", "haber": "0,00", "fecha": "2026-01-01"}],
}


@pytest.fixture
def s(monkeypatch):
    for k, v in (("OBJECT_STORAGE_ENDPOINT", "http://s3.local"), ("OBJECT_STORAGE_BUCKET", "general"),
                 ("OBJECT_STORAGE_ACCESS_KEY", "a"), ("OBJECT_STORAGE_SECRET_KEY", "b")):
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _almacen(monkeypatch, objs):
    def get(**kw):
        try:
            return objs[(kw["bucket"], kw["object_key"])]
        except KeyError:
            raise RuntimeError("no se pudo leer")
    monkeypatch.setattr(rj, "get_object_bytes", get)


def _csvs(payload):
    reducido, archivos = d.separar_ccma(payload)
    return reducido, {k: v.encode() for k, v in archivos.items()}


def _fila(payload, claves):
    return {"job_id": J, "payload": payload, "arts": [{"object_key": k} for k in claves]}


def test_se_reduce_cuando_el_csv_de_s4_reconstruye_el_payload_exacto(s, monkeypatch) -> None:
    reducido, csv = _csvs(PAYLOAD_CCMA)
    ks, km = f"jobs/{J}/ccma_saldos_20374730429.csv", f"jobs/{J}/ccma_movimientos_20374730429.csv"
    _almacen(monkeypatch, {("ccma", ks): csv["saldos"], ("ccma", km): csv["movimientos"]})
    est, t = rj.evaluar(s, "ccma", _fila(PAYLOAD_CCMA, [ks, km]))
    assert est == "listo" and t["reducido"] == reducido and t["original"] == PAYLOAD_CCMA
    assert datos_archivo.BOTS["ccma"][0](t["reducido"], {"saldos": csv["saldos"].decode(), "movimientos": csv["movimientos"].decode()}) == PAYLOAD_CCMA


def test_csv_distinto_no_reduce(s, monkeypatch) -> None:
    _, csv = _csvs(PAYLOAD_CCMA)
    ks, km = f"jobs/{J}/ccma_saldos_20374730429.csv", f"jobs/{J}/ccma_movimientos_20374730429.csv"
    _almacen(monkeypatch, {("ccma", ks): csv["saldos"], ("ccma", km): b"fecha|concepto|debe|haber\n2000-01-01|X|9|9\n"})
    assert rj.evaluar(s, "ccma", _fila(PAYLOAD_CCMA, [ks, km]))[0] in ("NO_COINCIDE", "ERROR_LECTURA")


def test_sin_csv_registrado_no_reduce(s, monkeypatch) -> None:
    _almacen(monkeypatch, {})
    assert rj.evaluar(s, "ccma", _fila(PAYLOAD_CCMA, []))[0] == "SIN_CSV_EN_S4"


def test_csv_ilegible_no_reduce(s, monkeypatch) -> None:
    _almacen(monkeypatch, {})
    k = f"jobs/{J}/ccma_saldos_20374730429.csv"
    assert rj.evaluar(s, "ccma", _fila(PAYLOAD_CCMA, [k, f"jobs/{J}/ccma_movimientos_20374730429.csv"]))[0] == "ERROR_LECTURA"


def test_ya_reducido_y_sin_dato_se_saltean(s, monkeypatch) -> None:
    _almacen(monkeypatch, {})
    reducido, _ = _csvs(PAYLOAD_CCMA)
    assert rj.evaluar(s, "ccma", _fila(reducido, []))[0] == "ya_reducido"
    assert rj.evaluar(s, "ccma", _fila({"response_ccma": {"saldo": "1"}}, []))[0] == "sin_dato_que_archivar"
