"""Migracion de objetos a su bucket: copia, verificacion y borrado solo de lo verificado."""

from __future__ import annotations

import asyncio
import hashlib

import pytest

from central_api.settings import get_settings
from central_api.tools import migrar_buckets as mb


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


class Almacen:
    """Buckets en memoria con las 4 operaciones que usa la herramienta."""

    def __init__(self, inicial: dict[tuple[str, str], bytes]):
        self.obj = dict(inicial)
        self.borrados: list[tuple[str, str]] = []

    def exists(self, **kw):
        return (kw["bucket"], kw["object_key"]) in self.obj

    def get(self, **kw):
        try:
            return self.obj[(kw["bucket"], kw["object_key"])]
        except KeyError:
            raise RuntimeError("no se pudo leer el archivo del almacenamiento")

    def put(self, **kw):
        self.obj[(kw["bucket"], kw["object_key"])] = kw["content"]

    def delete(self, **kw):
        self.borrados.append((kw["bucket"], kw["object_key"]))
        self.obj.pop((kw["bucket"], kw["object_key"]), None)


@pytest.fixture
def entorno(monkeypatch):
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT", "http://s3.local")
    monkeypatch.setenv("OBJECT_STORAGE_BUCKET", "general")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY", "a")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_KEY", "b")
    get_settings.cache_clear()

    def armar(filas, objetos):
        al = Almacen(objetos)
        monkeypatch.setattr(mb, "object_exists", al.exists)
        monkeypatch.setattr(mb, "get_object_bytes", al.get)
        monkeypatch.setattr(mb, "put_object_bytes", al.put)
        monkeypatch.setattr(mb, "delete_object_bytes", al.delete)

        async def _filas(bot):
            return [f for f in filas if bot is None or f["bot"] == bot]

        monkeypatch.setattr(mb, "_filas", _filas)
        monkeypatch.setattr(mb, "_registro", lambda e, a: (open("/dev/null", "w"), "/dev/null"))
        return al

    yield armar
    get_settings.cache_clear()


def _fila(key, cuerpo, bot="rcel", sha="auto", size="auto"):
    return {"id": key, "object_key": key, "bot": bot, "content_type": "application/pdf",
            "sha256": _sha(cuerpo) if sha == "auto" else sha,
            "size_bytes": len(cuerpo) if size == "auto" else size}


def _correr(etapa, aplicar=True, bot=None, limite=None):
    return asyncio.run(mb.correr(etapa, bot, limite, aplicar))


def test_copiar_verifica_y_deja_el_original(entorno) -> None:
    f = _fila("jobs/j1/1/rcel_000/a", b"PDF-A")
    al = entorno([f], {("general", f["object_key"]): b"PDF-A"})
    c = _correr("copiar")
    assert c["copiado_y_verificado"] == 1
    assert al.obj[("rcel", f["object_key"])] == b"PDF-A"
    assert ("general", f["object_key"]) in al.obj and al.borrados == []


def test_simulacion_no_escribe_ni_borra(entorno) -> None:
    f = _fila("jobs/j1/1/rcel_000/a", b"PDF-A")
    al = entorno([f], {("general", f["object_key"]): b"PDF-A"})
    assert _correr("copiar", aplicar=False)["se_copiaria"] == 1
    assert ("rcel", f["object_key"]) not in al.obj
    al.obj[("rcel", f["object_key"])] = b"PDF-A"
    assert _correr("borrar", aplicar=False)["se_borraria_(verificado)"] == 1
    assert al.borrados == []


def test_no_copia_si_el_origen_no_coincide_con_su_hash(entorno) -> None:
    f = _fila("jobs/j1/1/rcel_000/a", b"PDF-A", sha=_sha(b"OTRO"))
    al = entorno([f], {("general", f["object_key"]): b"PDF-A"})
    assert _correr("copiar")["ORIGEN_HASH_DISTINTO_no_se_copia"] == 1
    assert ("rcel", f["object_key"]) not in al.obj


def test_borrar_solo_si_el_destino_esta_verificado(entorno) -> None:
    ok = _fila("jobs/j1/1/rcel_000/ok", b"BUENO")
    corrupto = _fila("jobs/j2/1/rcel_000/mal", b"ORIGINAL")
    ausente = _fila("jobs/j3/1/rcel_000/falta", b"X")
    al = entorno([ok, corrupto, ausente], {
        ("general", ok["object_key"]): b"BUENO", ("rcel", ok["object_key"]): b"BUENO",
        ("general", corrupto["object_key"]): b"ORIGINAL", ("rcel", corrupto["object_key"]): b"TRUNCADO",
        ("general", ausente["object_key"]): b"X",
    })
    c = _correr("borrar")
    assert c["borrado_del_general"] == 1
    assert c["NO_VERIFICADO"] == 1 and c["NO_ESTA_EN_DESTINO"] == 1
    assert al.borrados == [("general", ok["object_key"])]
    # Los dos no verificados conservan su original: no se pierde nada.
    assert ("general", corrupto["object_key"]) in al.obj and ("general", ausente["object_key"]) in al.obj


def test_borrar_exige_tamano_igual_al_registrado(entorno) -> None:
    f = _fila("jobs/j1/1/rcel_000/a", b"PDF-A", size=999)
    al = entorno([f], {("general", f["object_key"]): b"PDF-A", ("rcel", f["object_key"]): b"PDF-A"})
    assert _correr("borrar")["NO_VERIFICADO"] == 1 and al.borrados == []


def test_sin_sha_registrado_verifica_contra_el_original(entorno) -> None:
    f = _fila("jobs/j1/1/rcel_000/a", b"PDF-A", sha=None, size=None)
    al = entorno([f], {("general", f["object_key"]): b"PDF-A", ("rcel", f["object_key"]): b"PDF-A"})
    assert _correr("borrar")["borrado_del_general"] == 1
    f2 = _fila("jobs/j2/1/rcel_000/b", b"PDF-B", sha=None, size=None)
    al = entorno([f2], {("general", f2["object_key"]): b"PDF-B", ("rcel", f2["object_key"]): b"DISTINTO"})
    assert _correr("borrar")["NO_VERIFICADO"] == 1 and al.borrados == []


def test_modulos_en_el_bucket_general_no_se_tocan(entorno) -> None:
    f = _fila("jobs/j1/1/x/a", b"Z", bot="apoc")
    al = entorno([f], {("general", f["object_key"]): b"Z"})
    clave = "sin_cambio_(modulo_en_bucket_general)"
    assert _correr("copiar")[clave] == 1
    assert _correr("borrar")[clave] == 1
    assert al.borrados == [] and ("general", f["object_key"]) in al.obj
    assert all(b != "apoc" for b, _ in al.obj)


def test_es_idempotente(entorno) -> None:
    f = _fila("jobs/j1/1/rcel_000/a", b"PDF-A")
    entorno([f], {("general", f["object_key"]): b"PDF-A"})
    assert _correr("copiar")["copiado_y_verificado"] == 1
    assert _correr("copiar")["ya_en_destino"] == 1
    assert _correr("borrar")["borrado_del_general"] == 1
    assert _correr("borrar")["ya_borrado_del_general"] == 1


def test_el_limite_acota_cuantos_se_procesan(entorno) -> None:
    filas = [_fila(f"jobs/j{i}/1/rcel_000/a", b"P") for i in range(5)]
    entorno(filas, {("general", f["object_key"]): b"P" for f in filas})
    assert _correr("copiar", limite=2)["copiado_y_verificado"] == 2
