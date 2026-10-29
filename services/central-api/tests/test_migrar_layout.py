"""Migracion del layout viejo de S4 (jobs/{job}/{intento}/{artefacto}/{sufijo}) al nuevo (jobs/{job}/{archivo})."""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from central_api.settings import get_settings
from central_api.tools import migrar_layout as ml

J = "01a187fe-0000-0000-0000-000000000001"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Mundo:
    """Almacen + tabla job_artifacts en memoria."""

    def __init__(self, filas, objetos):
        self.filas = {f["id"]: dict(f) for f in filas}
        self.obj = dict(objetos)
        self.borrados = []
        self.updates = 0
        self.fallar_put = False

    def exists(self, **kw):
        return (kw["bucket"], kw["object_key"]) in self.obj

    def get(self, **kw):
        try:
            return self.obj[(kw["bucket"], kw["object_key"])]
        except KeyError:
            raise RuntimeError("no se pudo leer")

    def put(self, **kw):
        self.obj[(kw["bucket"], kw["object_key"])] = kw["content"]

    def delete(self, **kw):
        self.borrados.append((kw["bucket"], kw["object_key"]))
        self.obj.pop((kw["bucket"], kw["object_key"]), None)

    # repo
    async def filas_(self, bot):
        return sorted((dict(f) for f in self.filas.values() if not bot or f["bot"] == bot),
                      key=lambda f: (f["job"], f["created_at"], f["id"]))

    async def actualizar(self, lote):
        n = 0
        for it in lote:
            f = self.filas[it["id"]]
            if f["key"] == it["key"]:
                f["key"], f["filename"] = it["nueva"], it["nombre"]
                n += 1
                self.updates += 1
        return n

    async def fila(self, id_):
        f = self.filas.get(id_)
        return {"key": f["key"], "sha256": f["sha256"]} if f else None


class Repo:
    def __init__(self, m):
        self.m = m
        self.filas = m.filas_
        self.actualizar = m.actualizar
        self.fila = m.fila


@pytest.fixture
def s(monkeypatch, tmp_path):
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT", "http://s3.local")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "general")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY", "a")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_KEY", "b")
    get_settings.cache_clear()
    monkeypatch.setattr(ml, "MANIFIESTO_LOCAL", str(tmp_path / "man.json"))
    yield get_settings()
    get_settings.cache_clear()


def instalar(monkeypatch, m):
    monkeypatch.setattr(ml, "object_exists", m.exists)
    monkeypatch.setattr(ml, "get_object_bytes", m.get)
    monkeypatch.setattr(ml, "put_object_bytes", m.put)
    monkeypatch.setattr(ml, "delete_object_bytes", m.delete)


def fila(i, bot, clave, nombre, cuit=None, cuerpo=b"x", con_sha=True, n=0, job=J):
    return {"id": i, "job": job, "bot": bot, "rq": {"representado_cuit": cuit} if cuit else {}, "key": clave,
            "filename": nombre, "sha256": sha(cuerpo) if con_sha else None, "content_type": "text/csv",
            "created_at": f"2026-10-29T10:00:0{n}"}


def correr(s, m, **kw):
    kw = {"aplicar": False, "limite": None, "bot": None, **kw}
    return asyncio.run(ml.migrar(Repo(m), s, **kw))


def mundo_ccma():
    filas = [
        fila("1", "ccma", f"jobs/{J}/1/ccma_saldos.csv/aa", f"CCMA_saldos_20_{J}.csv", "20", b"s", n=1),
        fila("2", "ccma", f"jobs/{J}/1/ccma_movimientos.csv/bb", f"CCMA_movimientos_20_{J}.csv", "20", b"m", n=2),
        fila("3", "ccma", f"jobs/{J}/1/ccma_resumen.pdf/cc", "ccma_20_20261029_140935.pdf", "20", b"p", n=3),
        fila("4", "rcel", f"jobs/{J}/1/rcel_facturas.csv/dd", f"RCEL_facturas_20_{J}.csv", "20", b"r", n=4),
        fila("5", "ccma_compensacion", f"jobs/{J}/1/ccma_compensacion.pdf/ee", "ccma_compensacion_20_2026.pdf", "20", b"c", n=5),
    ]
    obj = {(f["bot"], f["key"]): b for f, b in zip(filas, [b"s", b"m", b"p", b"r", b"c"])}
    return Mundo(filas, obj)


def test_renombre_ccma_rcel(s, monkeypatch):
    m = mundo_ccma()
    instalar(monkeypatch, m)
    b = {f["bot"]: s.bucket_para_bot(f["bot"]) for f in m.filas.values()}
    m.obj = {(b[bot], k): v for (bot, k), v in m.obj.items()}
    r = correr(s, m, aplicar=True)
    assert r["migrados"] == 5 and r["errores"] == 0
    claves = {f["id"]: f["key"] for f in m.filas.values()}
    assert claves["1"] == f"jobs/{J}/ccma_saldos_20.csv"
    assert claves["2"] == f"jobs/{J}/ccma_movimientos_20.csv"
    assert claves["3"] == f"jobs/{J}/ccma_resumen_20.pdf"
    assert claves["4"] == f"jobs/{J}/rcel_facturas_20.csv"
    assert claves["5"] == f"jobs/{J}/ccma_compensacion_20.pdf"
    assert m.filas["1"]["filename"] == "ccma_saldos_20.csv"
    assert m.borrados == []
    assert m.obj[(b["rcel"], f"jobs/{J}/rcel_facturas_20.csv")] == b"r"


def mundo_dup():
    filas = [fila(str(i), "mis_retenciones", f"jobs/{J}/1/a{i}/x", "ret.pdf", None, b"c%d" % i, n=i) for i in (1, 2, 3)]
    b = get_settings().bucket_para_bot("mis_retenciones")
    return Mundo(filas, {(b, f["key"]): b"c%s" % f["id"].encode() for f in filas}), b


def test_desambiguacion_determinista(s, monkeypatch):
    m, b = mundo_dup()
    instalar(monkeypatch, m)
    r = correr(s, m, aplicar=True)
    assert r["migrados"] == 3 and r["desambiguados"] == 2
    assert [m.filas[i]["key"] for i in "123"] == [f"jobs/{J}/ret.pdf", f"jobs/{J}/ret-2.pdf", f"jobs/{J}/ret-3.pdf"]


def test_desambigua_contra_clave_nueva_ocupada(s, monkeypatch):
    m, b = mundo_dup()
    m.filas["9"] = fila("9", "mis_retenciones", f"jobs/{J}/ret.pdf", "ret.pdf", n=9)
    instalar(monkeypatch, m)
    correr(s, m, aplicar=True)
    assert m.filas["1"]["key"] == f"jobs/{J}/ret-2.pdf"


def test_idempotente(s, monkeypatch):
    m, b = mundo_dup()
    instalar(monkeypatch, m)
    correr(s, m, aplicar=True)
    antes, n = dict(m.obj), m.updates
    r = correr(s, m, aplicar=True)
    assert r["migrados"] == 0 and r["ya_nuevo"] == 3 and r["planificados"] == 0
    assert m.obj.keys() >= antes.keys() and m.updates == n


def test_simulacion_no_escribe(s, monkeypatch):
    m, b = mundo_dup()
    instalar(monkeypatch, m)
    antes = dict(m.obj)
    r = correr(s, m)
    assert r["se_migraria"] == 3 and m.obj == antes and m.updates == 0


def test_no_borra_sin_flag(s, monkeypatch):
    m, b = mundo_dup()
    instalar(monkeypatch, m)
    correr(s, m, aplicar=True)
    assert m.borrados == [] and all((b, f"jobs/{J}/1/a{i}/x") in m.obj for i in (1, 2, 3))


def borrar(s, m, **kw):
    kw = {"aplicar": True, "limite": None, "bot": None, **kw}
    return asyncio.run(ml.borrar_originales(Repo(m), s, **kw))


def test_borra_originales_en_fase_aparte(s, monkeypatch):
    m, b = mundo_dup()
    instalar(monkeypatch, m)
    correr(s, m, aplicar=True)
    r = borrar(s, m, aplicar=False)
    assert r["se_borraria"] == 3 and m.borrados == []
    r = borrar(s, m)
    assert r["borrado"] == 3 and len(m.borrados) == 3
    assert (b, f"jobs/{J}/ret.pdf") in m.obj


def test_no_borra_si_sha_no_coincide(s, monkeypatch):
    m, b = mundo_dup()
    instalar(monkeypatch, m)
    correr(s, m, aplicar=True)
    m.obj[(b, f"jobs/{J}/ret.pdf")] = b"corrupto"
    r = borrar(s, m)
    assert r["OMITIDO_sha_no_coincide"] >= 1 and (b, f"jobs/{J}/1/a1/x") in m.obj


def test_no_borra_si_fila_no_apunta_a_nuevo(s, monkeypatch):
    m, b = mundo_dup()
    instalar(monkeypatch, m)
    correr(s, m, aplicar=True)
    m.filas["1"]["key"] = f"jobs/{J}/1/a1/x"
    r = borrar(s, m)
    assert r["OMITIDO_fila_no_apunta_a_nuevo"] == 1 and (b, f"jobs/{J}/1/a1/x") in m.obj


def test_origen_ausente_no_toca_la_fila(s, monkeypatch):
    m, b = mundo_dup()
    del m.obj[(b, f"jobs/{J}/1/a2/x")]
    instalar(monkeypatch, m)
    r = correr(s, m, aplicar=True)
    assert r["origen_ausente"] == 1 and r["migrados"] == 2
    assert m.filas["2"]["key"] == f"jobs/{J}/1/a2/x" and m.filas["2"]["filename"] == "ret.pdf"


def test_fila_solo_cambia_tras_verificar(s, monkeypatch):
    m, b = mundo_dup()
    instalar(monkeypatch, m)
    # el destino se corrompe al escribir: la relectura no coincide
    put = m.put
    monkeypatch.setattr(ml, "put_object_bytes", lambda **kw: put(**{**kw, "content": kw["content"] + b"!"}))
    r = correr(s, m, aplicar=True)
    assert r["sha_no_coincide"] == 3 and r["migrados"] == 0 and m.updates == 0
    assert all(m.filas[i]["key"].startswith(f"jobs/{J}/1/") for i in "123")


def test_origen_distinto_de_sha_de_la_fila(s, monkeypatch):
    m, b = mundo_dup()
    m.filas["1"]["sha256"] = sha(b"otro")
    instalar(monkeypatch, m)
    r = correr(s, m, aplicar=True)
    assert r["sha_no_coincide"] == 1 and m.filas["1"]["key"].count("/") == 4


def test_clave_de_transicion_se_detecta_y_se_renombra_como_ccma() -> None:
    k = f"jobs/{J}/ccma_saldos.csv-ab12cd34ef56"
    assert ml.es_viejo(k) and not ml.es_viejo(f"jobs/{J}/ccma_saldos_20374730429.csv")
    assert ml._artifact_id(k) == "ccma_saldos.csv"
    assert ml.nombre_destino(k, "CCMA_saldos_x.csv", {"representado_cuit": "20374730429"}) == "ccma_saldos_20374730429.csv"
    assert ml.se_renombra(k, {"representado_cuit": "20374730429"})
    k2 = f"jobs/{J}/emitidos_zip-ab12cd34ef56"
    assert ml.nombre_destino(k2, "6 - MCE - x.zip", {}) == "6 - MCE - x.zip"
