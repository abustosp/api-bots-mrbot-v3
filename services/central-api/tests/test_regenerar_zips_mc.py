"""Regeneracion de ZIP de Mis Comprobantes desde el CSV completo guardado en S4."""

from __future__ import annotations

import io
import zipfile

import pytest

from central_api.settings import get_settings
from central_api.tools import regenerar_zips_mc as rz
from mrbot_contracts.planillas import COLUMNAS_MCE, reconstruir_filas_desde_zip


def _csv(filas: int = 3) -> bytes:
    lineas = [";".join(COLUMNAS_MCE)]
    for n in range(filas):
        f = [""] * len(COLUMNAS_MCE)
        f[0], f[1], f[2], f[3], f[4] = "2026-01-1%d" % n, "11", "1", str(n + 1), str(n + 1)
        f = [x if x else "0" if "Imp" in c else "" for x, c in zip(f, COLUMNAS_MCE)]
        lineas.append(";".join(f))
    return ("\n".join(lineas) + "\n").encode()


def _fila(jid, pl, arts, rq=None, attempts=1):
    return {"id": jid, "attempts": attempts, "rq": rq or {"representado_cuit": "20320160856"}, "pl": pl, "arts": arts}


CSV_E = {"k": "jobs/j1/1/emitidos_csv/aaa", "n": "9 - MCE - 01012026 - 31012026 - 20320160856 - Empresa.csv"}


def test_plan_regenera_solo_lo_que_tiene_csv_completo_y_sin_zip() -> None:
    filas = [
        _fila("a", {"emitidos": {"filas": 3, "muestra": [{"x": 1}]}}, [CSV_E]),                       # CSV completo -> regenerable
        _fila("b", {"emitidos": {"filas": 3}}, [CSV_E, {"k": "jobs/b/1/emitidos_zip/z", "n": "x.zip"}]),  # ya tiene ZIP
        _fila("c", {"emitidos": {"filas": 80, "muestra": [{"x": 1}] * 5}}, []),                       # solo muestra de 5 de 80
        _fila("d", {"emitidos": {"filas": 0}}, []),                                                    # sin comprobantes
        _fila("e", {"response_header": {}}, []),                                                       # legacy, sin datos
    ]
    trabajos, faltan = rz.plan(filas)
    assert [t["id"] for t in trabajos] == ["a"]
    assert [(f["id"], f["motivo"], f["filas"]) for f in faltan] == [("c", "solo_muestra", 80)]


def test_nombre_del_zip_sigue_el_patron_del_csv() -> None:
    assert rz._nombre_zip(CSV_E["n"], "emitidos", "j1") == "9 - MCE - 01012026 - 31012026 - 20320160856 - Empresa.zip"
    assert rz._nombre_zip(None, "recibidos", "abcdef123") == "recibidos-abcdef12.zip"


def test_el_zip_devuelve_exactamente_el_csv_y_la_central_lo_vuelve_a_leer(tmp_path) -> None:
    csv = _csv(3)
    z = rz._zip_de(csv, "datos.csv")
    with zipfile.ZipFile(io.BytesIO(z)) as arch:
        assert arch.namelist() == ["datos.csv"] and arch.read("datos.csv") == csv
    (tmp_path / "d.zip").write_bytes(z)
    filas, total, trunc = reconstruir_filas_desde_zip(
        tmp_path / "d.zip", tipo="emitidos", cuit="20320160856", desde="01/01/2026", hasta="31/01/2026", trabajo=tmp_path
    )
    assert total == 3 and len(filas) == 3 and not trunc and filas[0]["MC"] == "MCE"


class Almacen:
    def __init__(self, obj):
        self.obj = dict(obj)

    def get(self, **kw):
        try:
            return self.obj[(kw["bucket"], kw["object_key"])]
        except KeyError:
            raise RuntimeError("no se pudo leer")

    def put(self, **kw):
        self.obj[(kw["bucket"], kw["object_key"])] = kw["content"]


@pytest.fixture
def s(monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT", "http://s3.local")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "general")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY", "a")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_KEY", "b")
    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()


def _trabajo():
    return {"id": "j1", "tipo": "emitidos", "intento": 1, "csv_key": CSV_E["k"], "csv_nombre": CSV_E["n"], "filas": 3}


def test_simulacion_no_escribe(s, monkeypatch) -> None:
    al = Almacen({("miscomprobantes", CSV_E["k"]): _csv()})
    monkeypatch.setattr(rz, "get_object_bytes", al.get)
    monkeypatch.setattr(rz, "put_object_bytes", al.put)
    est, t = rz._hacer(s, False, _trabajo())
    assert est == "se_generaria" and len(al.obj) == 1 and t["zip_nombre"].endswith("Empresa.zip")


def test_aplicar_sube_con_el_patron_del_worker_y_verifica(s, monkeypatch) -> None:
    csv = _csv()
    al = Almacen({("miscomprobantes", CSV_E["k"]): csv})
    monkeypatch.setattr(rz, "get_object_bytes", al.get)
    monkeypatch.setattr(rz, "put_object_bytes", al.put)
    est, t = rz._hacer(s, True, _trabajo())
    assert est == "subido"
    bucket, clave = next((b, k) for (b, k) in al.obj if k != CSV_E["k"])
    assert bucket == "miscomprobantes" and clave.startswith("jobs/j1/1/emitidos_zip/")
    with zipfile.ZipFile(io.BytesIO(al.obj[(bucket, clave)])) as z:
        assert z.read(z.namelist()[0]) == csv


def test_csv_no_legible_no_genera_nada(s, monkeypatch) -> None:
    al = Almacen({})
    monkeypatch.setattr(rz, "get_object_bytes", al.get)
    monkeypatch.setattr(rz, "put_object_bytes", al.put)
    assert rz._hacer(s, True, _trabajo())[0] == "CSV_NO_LEGIBLE" and al.obj == {}
