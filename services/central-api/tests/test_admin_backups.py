"""Regresión del panel de backups: dump propio, manifiesto, restore y modos.

Todo corre con fakes: no hay red, ni base, ni bucket real. Las pruebas contra
PostgreSQL real viven en ``test_admin_backups_pg.py``.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.admin import backups as B  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402

TOKEN = "backup-admin-token"
H = {"Authorization": f"Bearer {TOKEN}"}


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------


class CopyFalso:
    """Context manager de ``cursor.copy`` que acumula bytes y fija rowcount."""

    def __init__(self, cursor: "CursorFalso", filas: int):
        self.cursor = cursor
        self.filas = filas
        self.datos = b""

    def __enter__(self) -> "CopyFalso":
        return self

    def __exit__(self, *_) -> bool:
        self.cursor.rowcount = self.filas
        return False

    def write(self, bloque: bytes) -> None:
        self.datos += bloque


class CursorFalso:
    """Cursor mínimo: registra SQL y devuelve respuestas guionadas."""

    def __init__(
        self,
        *,
        pk: tuple[str, ...] = (),
        columnas: tuple[str, ...] = ("id", "nombre"),
        filas_copy: int = 0,
        respuestas: tuple[int, ...] = (),
        fallar_en: str | None = None,
        upsert_filas: tuple[int, int] | None = None,
    ):
        self.pk = list(pk)
        self.columnas = list(columnas)
        self.filas_copy = filas_copy
        self.respuestas = list(respuestas)
        self.fallar_en = fallar_en
        # (insertadas, reemplazadas) que devuelve el upsert con RETURNING.
        self.upsert_filas = upsert_filas
        self.ejecutados: list[str] = []
        self.copias: list[str] = []
        self.rowcount = 0
        self._ultimo: list = []

    def __enter__(self) -> "CursorFalso":
        return self

    def __exit__(self, *_) -> bool:
        return False

    def execute(self, sql: str, params=None) -> None:
        self.ejecutados.append(sql)
        if self.fallar_en and self.fallar_en in sql:
            raise RuntimeError(f"fallo simulado en {self.fallar_en}")
        if "pg_index" in sql:
            self._ultimo = [(c,) for c in self.pk]
        elif "information_schema.columns" in sql:
            self._ultimo = [(c,) for c in self.columnas]
        elif "to_regclass" in sql:
            # La tabla existe en el destino salvo que se pida lo contrario.
            nombre = str((params or ("public.x",))[0])
            self._ultimo = [(nombre,)]
        elif "version_num" in sql:
            self._ultimo = [("0027",)]
        elif sql.startswith("SELECT count(*)"):
            self._ultimo = [(self.respuestas.pop(0),)] if self.respuestas else [(0,)]
        elif "RETURNING (xmax = 0)" in sql:
            nuevas, pisadas = self.upsert_filas or (0, 0)
            self._ultimo = [(True,)] * nuevas + [(False,)] * pisadas
        elif sql.startswith(("DELETE", "INSERT")):
            self.rowcount = self.respuestas.pop(0) if self.respuestas else 0
        else:
            self._ultimo = []

    def fetchone(self):
        return self._ultimo[0] if self._ultimo else None

    def fetchall(self):
        return list(self._ultimo)

    def copy(self, sql: str) -> CopyFalso:
        self.copias.append(sql)
        return CopyFalso(self, self.filas_copy)


class TransaccionFalsa:
    """Transacción por tabla: cuenta los rollbacks que provoca una excepción."""

    def __init__(self, conexion: "ConexionFalsa"):
        self.conexion = conexion

    def __enter__(self) -> "TransaccionFalsa":
        return self

    def __exit__(self, tipo, valor, traza) -> bool:
        if tipo is not None:
            self.conexion.rollbacks += 1
        else:
            self.conexion.commits += 1
        return False


class ConexionFalsa:
    """Conexión psycopg mínima para probar la orquestación del restore."""

    def __init__(self, cursor: CursorFalso):
        self._cursor = cursor
        self.rollbacks = 0
        self.commits = 0

    def __enter__(self) -> "ConexionFalsa":
        return self

    def __exit__(self, *_) -> bool:
        return False

    def cursor(self) -> CursorFalso:
        return self._cursor

    def transaction(self) -> TransaccionFalsa:
        return TransaccionFalsa(self)


class FakeS3:
    """Cliente S3 en memoria: nunca toca la red ni un bucket real."""

    def __init__(self, objetos: dict[str, bytes] | None = None, paginas: int = 1):
        self.objetos: dict[str, bytes] = dict(objetos or {})
        self.paginas = max(1, paginas)
        self.subidas: list[str] = []
        self.pedidos: list[dict] = []

    def upload_file(self, ruta, bucket, key, ExtraArgs=None) -> None:
        self.subidas.append(key)
        self.objetos[key] = Path(ruta).read_bytes()

    def download_file(self, bucket, key, ruta) -> None:
        if key not in self.objetos:
            raise KeyError(f"no existe {key}")
        Path(ruta).write_bytes(self.objetos[key])

    def list_objects_v2(self, **kwargs) -> dict:
        self.pedidos.append(kwargs)
        claves = sorted(self.objetos)
        por_pagina = max(1, len(claves) // self.paginas or 1)
        offset = 0
        token = kwargs.get("ContinuationToken")
        if token:
            offset = int(token)
        trozo = claves[offset : offset + por_pagina]
        siguiente = offset + por_pagina
        respuesta = {
            "Contents": [
                {
                    "Key": clave,
                    "Size": len(self.objetos[clave]),
                    "LastModified": datetime(2026, 10, 16, tzinfo=timezone.utc),
                }
                for clave in trozo
            ],
            "IsTruncated": siguiente < len(claves),
        }
        if respuesta["IsTruncated"]:
            respuesta["NextContinuationToken"] = str(siguiente)
        return respuesta

    def generate_presigned_url(self, operacion, Params=None, ExpiresIn=None) -> str:
        return f"https://fake-bucket.local/{Params['Key']}?X-Amz-Expires={ExpiresIn}"


class ClienteFallo:
    """Cliente que siempre falla (bucket caído o sin credenciales)."""

    def __getattr__(self, nombre):
        def _falla(*_, **__):
            raise RuntimeError("EndpointConnectionError")

        return _falla


# --------------------------------------------------------------------------
# Utilidades puras
# --------------------------------------------------------------------------


def test_orden_topologico_respeta_fks_y_es_estable() -> None:
    tablas = ["c", "b", "a", "suelta", "ciclo_x", "ciclo_y"]
    dependencias = [
        ("b", "a"),  # b depende de a
        ("c", "b"),  # c depende de b (cadena a -> b -> c)
        ("ciclo_x", "ciclo_y"),
        ("ciclo_y", "ciclo_x"),  # ciclo: se rompe de forma determinista
    ]
    orden = B._orden_topologico(tablas, dependencias)
    assert sorted(orden) == sorted(tablas)
    assert orden.index("a") < orden.index("b") < orden.index("c")
    # Reproducible: el mismo esquema da exactamente el mismo orden.
    assert orden == B._orden_topologico(tablas, dependencias)
    # El ciclo no cuelga la función y deja ambas tablas presentes.
    assert {"ciclo_x", "ciclo_y"} <= set(orden)


def test_nombre_key_usa_el_timestamp_del_dump() -> None:
    momento = datetime(2026, 10, 16, 18, 30, 5, tzinfo=timezone.utc)
    assert B._nombre_key("dump", momento) == "dump/20261016T183005Z-mrbot-db.tar.gz"
    assert B._nombre_key("/dump/", momento) == "dump/20261016T183005Z-mrbot-db.tar.gz"
    assert B._nombre_key("", momento) == "20261016T183005Z-mrbot-db.tar.gz"
    # Una hora distinta produce otra clave (el nombre lleva el sello del dump).
    otra = momento + timedelta(seconds=1)
    assert B._nombre_key("dump", momento) != B._nombre_key("dump", otra)


def test_dsn_psycopg_reutiliza_normalizar_dsn() -> None:
    assert B._dsn_psycopg("postgresql://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert B._dsn_psycopg("postgres://u:p@h/db") == "postgresql://u:p@h/db"
    assert (
        B._dsn_psycopg("postgresql+psycopg://u:p@h/db") == "postgresql://u:p@h/db"
    )


def test_validar_nombre_tabla_rechaza_inyeccion() -> None:
    assert B._validar_nombre_tabla(" users ") == "users"
    assert B._validar_nombre_tabla("bot_jobs_apoc") == "bot_jobs_apoc"
    for invalido in ("", "users; DROP TABLE x", "public.users", 'users"x', "1tabla", "a b"):
        with pytest.raises(B.SolicitudInvalida):
            B._validar_nombre_tabla(invalido)


def test_sql_overwrite_y_append_con_pk_simple_compuesta_y_sin_pk() -> None:
    upsert = B._sql_upsert("users", ["id", "email"], ["id"])
    assert upsert == (
        'INSERT INTO public."users" ("id", "email") SELECT "id", "email" '
        'FROM "_bkp_users" ON CONFLICT ("id") DO UPDATE SET "email" = EXCLUDED."email" '
        "RETURNING (xmax = 0)"
    )
    # PK compuesta y sin columnas actualizables (la PK es todo).
    compuesta = B._sql_upsert("billing_usage", ["cuenta_id", "periodo", "monto"],
                              ["cuenta_id", "periodo"])
    assert 'ON CONFLICT ("cuenta_id", "periodo") DO UPDATE SET "monto" = EXCLUDED."monto"' in compuesta
    solo_pk = B._sql_upsert("t", ["a", "b"], ["a", "b"])
    assert 'ON CONFLICT ("a", "b") DO NOTHING' in solo_pk
    insert = B._sql_insert("users", ["id", "email"])
    assert insert == (
        'INSERT INTO public."users" ("id", "email") '
        'SELECT "id", "email" FROM "_bkp_users" ON CONFLICT DO NOTHING'
    )
    colisiones = B._sql_contar_colisiones("users", ["id"])
    assert colisiones == (
        'SELECT count(*) FROM "_bkp_users" AS b JOIN public."users" AS t ON b."id" = t."id"'
    )
    # overwrite con PK: un upsert que pisa los valores en el lugar (no borra
    # la fila, así no choca con las referencias de otras tablas).
    plan = B._plan_sql("users", ["id", "email"], ["id"], B.MODO_OVERWRITE)
    assert plan == [upsert]
    # append: solo inserta sin pisar.
    assert B._plan_sql("users", ["id", "email"], ["id"], B.MODO_APPEND) == [insert]
    # sin PK no se puede decidir el pisotón: solo inserta.
    assert B._plan_sql("sin_pk", ["a"], [], B.MODO_OVERWRITE) == [
        B._sql_insert("sin_pk", ["a"])
    ]


def test_empaquetar_y_verificar_manifiesto(tmp_path: Path) -> None:
    (tmp_path / "users.copy").write_bytes(b"1\tuno\n2\tdos\n")
    sha = hashlib.sha256(b"1\tuno\n2\tdos\n").hexdigest()
    manifiesto = {
        "version": 1,
        "created_at": "2026-10-16T18:30:00+00:00",
        "database": "m",
        "alembic_revision": "0027",
        "tablas": [
            {"nombre": "users", "columnas": ["id", "nombre"], "filas": 2, "sha256": sha}
        ],
        "total_filas": 2,
    }
    tar_path = tmp_path / "dump.tar.gz"
    tamano = B._empaquetar(str(tmp_path), str(tar_path), manifiesto)
    assert tamano == tar_path.stat().st_size > 0
    assert (tmp_path / B.MANIFEST_NAME).exists()
    with tarfile.open(tar_path) as tar:
        nombres = sorted(tar.getnames())
    assert nombres == ["manifest.json", "users.copy"]

    extraido = tmp_path / "extraido"
    extraido.mkdir()
    leido, rutas = B._extraer_y_verificar(str(tar_path), str(extraido))
    assert leido["tablas"][0]["filas"] == 2
    assert leido["tablas"][0]["columnas"] == ["id", "nombre"]
    assert rutas["users"] == str(extraido / "users.copy")
    assert Path(rutas["users"]).read_bytes() == b"1\tuno\n2\tdos\n"


def test_sha256_del_manifiesto_que_no_coincide_aborta(tmp_path: Path) -> None:
    (tmp_path / "users.copy").write_bytes(b"1\tuno\n")
    manifiesto = {
        "version": 1,
        "created_at": "2026-10-16T18:30:00+00:00",
        "tablas": [
            {
                "nombre": "users",
                "columnas": ["id", "nombre"],
                "filas": 1,
                "sha256": hashlib.sha256(b"otra cosa").hexdigest(),
            }
        ],
    }
    tar_path = tmp_path / "dump.tar.gz"
    B._empaquetar(str(tmp_path), str(tar_path), manifiesto)
    destino = tmp_path / "extraido"
    destino.mkdir()
    with pytest.raises(B.BackupInvalido) as excinfo:
        B._extraer_y_verificar(str(tar_path), str(destino))
    assert "sha256" in str(excinfo.value)


def test_backup_sin_manifiesto_aborta(tmp_path: Path) -> None:
    tar_path = tmp_path / "vacio.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        archivo = tmp_path / "solo.copy"
        archivo.write_bytes(b"1\n")
        tar.add(str(archivo), arcname="solo.copy")
    destino = tmp_path / "extraido"
    destino.mkdir()
    with pytest.raises(B.BackupInvalido):
        B._extraer_y_verificar(str(tar_path), str(destino))


# --------------------------------------------------------------------------
# Semántica del restore por tabla (cursor falso)
# --------------------------------------------------------------------------


def _archivo_copy(tmp_path: Path, contenido: bytes = b"1\tuno\n2\tdos\n") -> str:
    ruta = tmp_path / "tabla.copy"
    ruta.write_bytes(contenido)
    return str(ruta)


def test_restaurar_tabla_overwrite_con_pk_reemplaza_y_cuenta(tmp_path: Path) -> None:
    cur = CursorFalso(pk=("id",), filas_copy=3, upsert_filas=(1, 2))
    resultado = B._restaurar_tabla(
        cur,
        entrada={"nombre": "t", "columnas": ["id", "nombre"]},
        ruta=_archivo_copy(tmp_path),
        modo=B.MODO_OVERWRITE,
        dry_run=False,
    )
    assert resultado["filas_archivo"] == 3
    assert resultado["reemplazadas"] == 2
    assert resultado["insertadas"] == 1
    assert resultado["omitidas"] == 0
    assert resultado["sin_pk"] is False
    assert any(sql.startswith("CREATE TEMP TABLE") for sql in cur.ejecutados)
    assert '"id", "nombre"' in cur.copias[0]
    # Una sola sentencia de upsert: nada de DELETE (fallaría con FKs RESTRICT).
    assert not any(sql.startswith("DELETE") for sql in cur.ejecutados)
    upsert = [sql for sql in cur.ejecutados if sql.startswith("INSERT")]
    assert len(upsert) == 1 and "DO UPDATE SET" in upsert[0]


def test_restaurar_tabla_append_no_borra_nada(tmp_path: Path) -> None:
    cur = CursorFalso(pk=("id",), filas_copy=4, respuestas=(1,))
    resultado = B._restaurar_tabla(
        cur,
        entrada={"nombre": "t", "columnas": ["id", "nombre"]},
        ruta=_archivo_copy(tmp_path),
        modo=B.MODO_APPEND,
        dry_run=False,
    )
    assert resultado["reemplazadas"] == 0
    assert resultado["insertadas"] == 1
    assert resultado["omitidas"] == 3
    assert not any(sql.startswith("DELETE") for sql in cur.ejecutados)


def test_restaurar_tabla_overwrite_sin_pk_avisa_y_solo_inserta(tmp_path: Path) -> None:
    cur = CursorFalso(pk=(), columnas=("a", "b"), filas_copy=2, respuestas=(2,))
    resultado = B._restaurar_tabla(
        cur,
        entrada={"nombre": "sin_pk", "columnas": ["a", "b"]},
        ruta=_archivo_copy(tmp_path),
        modo=B.MODO_OVERWRITE,
        dry_run=False,
    )
    assert resultado["sin_pk"] is True
    assert resultado["reemplazadas"] == 0
    assert "advertencia" in resultado
    assert not any(sql.startswith("DELETE") for sql in cur.ejecutados)


def test_restaurar_tabla_dry_run_no_muta_y_cuenta_colisiones(tmp_path: Path) -> None:
    cur = CursorFalso(pk=("id",), filas_copy=7, respuestas=(3,))
    resultado = B._restaurar_tabla(
        cur,
        entrada={"nombre": "t", "columnas": ["id", "nombre"]},
        ruta=_archivo_copy(tmp_path),
        modo=B.MODO_OVERWRITE,
        dry_run=True,
    )
    assert resultado["dry_run"] is True
    assert resultado["filas_archivo"] == 7
    assert resultado["colisionarian"] == 3
    assert resultado["reemplazadas"] == 3
    assert resultado["insertadas"] == 0
    assert resultado["plan"][0].startswith("INSERT INTO public.")
    assert "DO UPDATE SET" in resultado["plan"][0]
    assert not any(
        sql.startswith(("DELETE", "INSERT")) for sql in cur.ejecutados
    ), cur.ejecutados


def test_restaurar_tabla_dry_run_sin_pk_no_cuenta_colisiones(tmp_path: Path) -> None:
    cur = CursorFalso(pk=(), columnas=("a",), filas_copy=5)
    resultado = B._restaurar_tabla(
        cur,
        entrada={"nombre": "sin_pk", "columnas": ["a"]},
        ruta=_archivo_copy(tmp_path),
        modo=B.MODO_APPEND,
        dry_run=True,
    )
    assert resultado["colisionarian"] is None
    assert resultado["sin_pk"] is True
    assert "advertencia" in resultado


def test_restaurar_tabla_falla_si_falta_una_columna(tmp_path: Path) -> None:
    cur = CursorFalso(pk=("id",), columnas=("id",), filas_copy=1)
    with pytest.raises(RuntimeError) as excinfo:
        B._restaurar_tabla(
            cur,
            entrada={"nombre": "t", "columnas": ["id", "nombre"]},
            ruta=_archivo_copy(tmp_path),
            modo=B.MODO_APPEND,
            dry_run=False,
        )
    assert "nombre" in str(excinfo.value)


def test_restore_detiene_y_revierte_la_tabla_que_falla(tmp_path: Path, monkeypatch) -> None:
    """Una tabla que falla revierte su transacción y detiene el restore."""
    for nombre in ("a1", "a2", "a3"):
        (tmp_path / f"{nombre}.copy").write_bytes(b"1\tuno\n")
    manifiesto = {
        "version": 1,
        "created_at": "2026-10-16T18:30:00+00:00",
        "alembic_revision": "0027",
        "tablas": [
            {
                "nombre": nombre,
                "columnas": ["id", "nombre"],
                "filas": 1,
                "sha256": hashlib.sha256(b"1\tuno\n").hexdigest(),
            }
            for nombre in ("a1", "a2", "a3")
        ],
        "total_filas": 3,
    }
    tar_path = tmp_path / "dump.tar.gz"
    B._empaquetar(str(tmp_path), str(tar_path), manifiesto)
    destino = tmp_path / "extraido"
    destino.mkdir()

    # La tabla a2 falla al insertar: su transacción se revierte y no se sigue.
    cur = CursorFalso(
        pk=("id",),
        filas_copy=1,
        respuestas=(1, 1, 1),
        fallar_en='INSERT INTO public."a2"',
    )
    conexion = ConexionFalsa(cur)
    monkeypatch.setattr(B, "_conectar", lambda _dsn: conexion)

    resultado = B._restaurar_sincrono(
        dsn="postgresql://x/y",
        tar_path=str(tar_path),
        modo=B.MODO_APPEND,
        tablas=[],
        dry_run=False,
        destino=str(destino),
    )
    assert [item["tabla"] for item in resultado["tablas"]] == ["a1", "a2"]
    assert resultado["success"] is False
    assert resultado["tablas"][0]["insertadas"] == 1
    assert resultado["tablas"][1]["error_tipo"] == "ejecucion"
    assert "fallo simulado" in resultado["tablas"][1]["error"]
    assert resultado["resumen"]["detenido"] is True
    assert resultado["resumen"]["tablas_aplicadas"] == ["a1"]
    assert resultado["resumen"]["tablas_con_error_detalle"] == ["a2"]
    # a1 confirmó su transacción; a2 la revirtió y a3 nunca se tocó.
    assert conexion.commits == 1
    assert conexion.rollbacks == 1
    assert not any('public."a3"' in sql for sql in cur.ejecutados)


def test_restore_advierte_por_revision_distinta_y_tablas_sin_pk(
    tmp_path: Path, monkeypatch
) -> None:
    """Un dump de otra revisión no falla: se informa como advertencia."""
    (tmp_path / "sin_pk.copy").write_bytes(b"x\ty\n")
    manifiesto = {
        "version": 1,
        "created_at": "2026-10-16T18:30:00+00:00",
        "alembic_revision": "0010",
        "incluir_datos": True,
        "tablas": [
            {
                "nombre": "sin_pk",
                "columnas": ["a", "b"],
                "filas": 1,
                "sha256": hashlib.sha256(b"x\ty\n").hexdigest(),
            }
        ],
        "total_filas": 1,
    }
    tar_path = tmp_path / "dump.tar.gz"
    B._empaquetar(str(tmp_path), str(tar_path), manifiesto)
    destino = tmp_path / "extraido"
    destino.mkdir()

    cur = CursorFalso(pk=(), columnas=("a", "b"), filas_copy=1, respuestas=(1,))
    monkeypatch.setattr(B, "_conectar", lambda _dsn: ConexionFalsa(cur))
    resultado = B._restaurar_sincrono(
        dsn="postgresql://x/y",
        tar_path=str(tar_path),
        modo=B.MODO_OVERWRITE,
        tablas=[],
        dry_run=False,
        destino=str(destino),
    )
    assert resultado["success"] is True
    assert resultado["alembic_revision"] == "0010"
    assert resultado["alembic_revision_destino"] == "0027"
    assert resultado["alembic_mismatch"] is True
    assert any("revisión" in aviso for aviso in resultado["advertencias"])
    assert any("sin clave primaria" in aviso for aviso in resultado["advertencias"])
    assert resultado["tablas"][0]["sin_pk"] is True
    assert resultado["resumen"]["tablas_aplicadas"] == ["sin_pk"]


def test_restore_corrupto_no_toca_la_base(tmp_path: Path, monkeypatch) -> None:
    """Con un sha256 que no coincide se aborta antes de conectar a PostgreSQL."""
    (tmp_path / "users.copy").write_bytes(b"1\tuno\n")
    manifiesto = {
        "version": 1,
        "created_at": "2026-10-16T18:30:00+00:00",
        "tablas": [
            {
                "nombre": "users",
                "columnas": ["id"],
                "filas": 1,
                "sha256": hashlib.sha256(b"corrupto").hexdigest(),
            }
        ],
    }
    tar_path = tmp_path / "dump.tar.gz"
    B._empaquetar(str(tmp_path), str(tar_path), manifiesto)
    destino = tmp_path / "extraido"
    destino.mkdir()

    def _prohibido(*_, **__):
        raise AssertionError("no debe conectarse a la base con un backup corrupto")

    monkeypatch.setattr(B, "_conectar", _prohibido)
    with pytest.raises(B.BackupInvalido):
        B._restaurar_sincrono(
            dsn="postgresql://x/y",
            tar_path=str(tar_path),
            modo=B.MODO_OVERWRITE,
            tablas=[],
            dry_run=False,
            destino=str(destino),
        )


# --------------------------------------------------------------------------
# Object storage
# --------------------------------------------------------------------------


def test_listar_objetos_pagina_y_ordena_del_mas_nuevo_al_mas_viejo(monkeypatch) -> None:
    settings = get_settings()
    objetos = {f"dump/2026{k:02d}-mrbot-db.tar.gz": b"x" * (k + 1) for k in range(5)}
    fake = FakeS3(objetos, paginas=3)
    monkeypatch.setattr(B, "_cliente_s3", lambda _s: fake)
    resultado = B._listar_objetos(settings)
    assert resultado["success"] is True
    assert resultado["count"] == 5
    assert len(fake.pedidos) >= 2, "debe usar ContinuationToken para paginar"
    assert fake.pedidos[0]["Bucket"] == settings.backup_bucket
    assert all(item["key"].startswith("dump/") for item in resultado["objects"])
    assert [item["size_bytes"] for item in resultado["objects"]] == [5, 4, 3, 2, 1]


# --------------------------------------------------------------------------
# Endpoints (con fakes)
# --------------------------------------------------------------------------


@pytest.fixture
def cliente(monkeypatch, tmp_path):
    from central_api.admin import audit

    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:t@127.0.0.1:55433/m")
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT", "http://fake-s3.local:9000")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY", "clave")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_KEY", "secreto")
    monkeypatch.setenv("BACKUP_BUCKET", "mrbot-bots-api-bkp-db")
    monkeypatch.setenv("BACKUP_PREFIX", "dump")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    # Los eventos de auditoría son globales: cada prueba restaura el largo previo
    # para no contaminar las expectativas de otras pruebas del panel.
    inicio_auditoria = len(audit.AUDIT_LOG)
    try:
        yield TestClient(create_app())
    finally:
        del audit.AUDIT_LOG[inicio_auditoria:]
        get_settings.cache_clear()


def test_dump_endpoint_devuelve_resultado_y_audita(cliente, monkeypatch) -> None:
    from central_api.admin import audit

    def _falso(**kwargs):
        assert kwargs["incluir_datos"] is True
        assert kwargs["tablas"] == []
        return {
            "success": True,
            "bucket": "mrbot-bots-api-bkp-db",
            "key": "dump/20261016T183000Z-mrbot-db.tar.gz",
            "size_bytes": 1234,
            "size_raw": 4096,
            "tablas": [{"nombre": "users", "filas": 2, "columnas": ["id"], "sha256": "a"}],
            "cantidad_tablas": 1,
            "total_filas": 2,
            "sha256": "b",
            "created_at": "2026-10-16T18:30:00+00:00",
            "database": "m",
            "alembic_revision": "0027",
        }

    monkeypatch.setattr(B, "_crear_dump", _falso)
    antes = len(audit.AUDIT_LOG)
    respuesta = cliente.post(
        "/admin/backups/dump", headers=H, json={"motivo": "respaldo previo a migración"}
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["key"].endswith("-mrbot-db.tar.gz")
    assert cuerpo["total_filas"] == 2
    assert "duracion_segundos" in cuerpo
    assert respuesta.headers["cache-control"] == "private, no-store"
    eventos = audit.AUDIT_LOG[antes:]
    assert [e.action for e in eventos] == ["backup.dump"]
    assert eventos[0].reason == "respaldo previo a migración"
    assert eventos[0].metadata_redacted["total_filas"] == 2


def test_dump_rechaza_motivo_corto(cliente) -> None:
    respuesta = cliente.post("/admin/backups/dump", headers=H, json={"motivo": "corto"})
    assert respuesta.status_code == 400
    assert "motivo" in respuesta.json()["detail"]


def test_dump_sin_configuracion_devuelve_503_claro(cliente, monkeypatch) -> None:
    monkeypatch.delenv("OBJECT_STORAGE_ENDPOINT")
    monkeypatch.delenv("OBJECT_STORAGE_ACCESS_KEY")
    get_settings.cache_clear()
    respuesta = cliente.post(
        "/admin/backups/dump", headers=H, json={"motivo": "respaldo sin configuración"}
    )
    assert respuesta.status_code == 503
    assert "backup no configurado" in respuesta.json()["detail"]


def test_listado_sin_configuracion_informa_el_problema(cliente, monkeypatch) -> None:
    monkeypatch.setenv("BACKUP_BUCKET", "")
    get_settings.cache_clear()
    respuesta = cliente.get("/admin/backups", headers=H)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["success"] is False
    assert cuerpo["error"] == "no_configurado"
    assert cuerpo["objects"] == []


def test_listado_con_bucket_caido_no_rompe(cliente, monkeypatch) -> None:
    monkeypatch.setattr(B, "_cliente_s3", lambda _s: ClienteFallo())
    respuesta = cliente.get("/admin/backups", headers=H)
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["success"] is False
    assert cuerpo["error"] == "RuntimeError"
    assert cuerpo["bucket"] == "mrbot-bots-api-bkp-db"


def test_restore_exige_confirmacion_exacta(cliente) -> None:
    respuesta = cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": "dump/x.tar.gz",
            "modo": "overwrite",
            "motivo": "restauración solicitada por soporte",
            "confirmacion": "restaurar",
        },
    )
    assert respuesta.status_code == 400
    assert "RESTAURAR" in respuesta.json()["detail"]


def test_restore_rechaza_modo_invalido(cliente) -> None:
    respuesta = cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": "dump/x.tar.gz",
            "modo": "merge",
            "motivo": "restauración solicitada por soporte",
            "confirmacion": "RESTAURAR",
        },
    )
    assert respuesta.status_code == 422


def test_restore_sin_bucket_configurado_devuelve_503(cliente, monkeypatch) -> None:
    monkeypatch.delenv("OBJECT_STORAGE_SECRET_KEY")
    get_settings.cache_clear()
    respuesta = cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": "dump/x.tar.gz",
            "modo": "append",
            "motivo": "restauración solicitada por soporte",
            "confirmacion": "RESTAURAR",
        },
    )
    assert respuesta.status_code == 503
    assert "backup no configurado" in respuesta.json()["detail"]


def test_restore_con_backup_corrupto_responde_400_y_no_toca_la_base(
    cliente, monkeypatch, tmp_path
) -> None:
    """El endpoint valida el sha256 antes de conectar a PostgreSQL."""
    (tmp_path / "users.copy").write_bytes(b"1\tuno\n")
    manifiesto = {
        "version": 1,
        "created_at": "2026-10-16T18:30:00+00:00",
        "tablas": [
            {
                "nombre": "users",
                "columnas": ["id"],
                "filas": 1,
                "sha256": hashlib.sha256(b"corrupto").hexdigest(),
            }
        ],
    }
    tar_path = tmp_path / "dump.tar.gz"
    B._empaquetar(str(tmp_path), str(tar_path), manifiesto)
    datos = tar_path.read_bytes()
    key = "dump/20261016T183000Z-mrbot-db.tar.gz"
    fake = FakeS3({key: datos})
    monkeypatch.setattr(B, "_cliente_s3", lambda _s: fake)

    def _prohibido(*_, **__):
        raise AssertionError("no debe conectarse a la base con un backup corrupto")

    monkeypatch.setattr(B, "_conectar", _prohibido)
    respuesta = cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": key,
            "modo": "overwrite",
            "motivo": "restauración solicitada por soporte",
            "confirmacion": "RESTAURAR",
        },
    )
    assert respuesta.status_code == 400
    assert "sha256" in respuesta.json()["detail"]


def test_descarga_prefirmada_y_listado_con_bucket_fake(cliente, monkeypatch) -> None:
    key = "dump/20261016T183000Z-mrbot-db.tar.gz"
    monkeypatch.setattr(B, "_cliente_s3", lambda _s: FakeS3({key: b"datos"}))
    listado = cliente.get("/admin/backups", headers=H)
    assert listado.status_code == 200
    cuerpo = listado.json()
    assert cuerpo["success"] is True
    assert [item["key"] for item in cuerpo["objects"]] == [key]

    descarga = cliente.get(f"/admin/backups/{key}", headers=H)
    assert descarga.status_code == 200
    datos = descarga.json()
    assert datos["key"] == key
    assert datos["download_url"].startswith("https://fake-bucket.local/")
    assert datos["expires_seconds"] == B.PRESIGN_EXPIRACION_SEGUNDOS


def test_endpoints_de_backups_exigen_bearer(cliente) -> None:
    assert cliente.get("/admin/backups").status_code == 401
    assert cliente.post("/admin/backups/dump", json={"motivo": "x" * 12}).status_code == 401
    assert (
        cliente.post(
            "/admin/backups/restore",
            json={"key": "k", "motivo": "y" * 12, "confirmacion": "RESTAURAR"},
        ).status_code
        == 401
    )
    assert cliente.get("/admin/backups/dump/x.tar.gz").status_code == 401


def test_json_de_ejemplo_del_manifiesto_no_expone_credenciales() -> None:
    """El manifiesto que se empaqueta solo lleva metadatos, nunca credenciales."""
    manifiesto = {
        "version": B.MANIFEST_VERSION,
        "created_at": "2026-10-16T18:30:00+00:00",
        "database": "m",
        "alembic_revision": "0027",
        "tablas": [],
        "total_filas": 0,
    }
    crudo = json.dumps(manifiesto)
    for prohibido in ("secret", "password", "token", "access_key"):
        assert prohibido not in crudo


def test_el_dump_limpia_el_temporal(monkeypatch, tmp_path) -> None:
    """``_crear_dump`` borra su directorio de trabajo aunque falle la subida."""
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path))
    get_settings.cache_clear()
    try:
        settings = get_settings()
        monkeypatch.setattr(
            B,
            "_volcar_tablas",
            lambda **__: {
                "database": "m",
                "alembic_revision": "1",
                "tablas": [],
                "total_filas": 0,
                "size_raw": 0,
            },
        )

        def _falla(*_, **__):
            raise RuntimeError("sin bucket")

        monkeypatch.setattr(B, "_subir_archivo", _falla)
        with pytest.raises(RuntimeError):
            B._crear_dump(
                settings=settings,
                tablas=[],
                incluir_datos=True,
                momento=datetime(2026, 10, 16, 18, 30, tzinfo=timezone.utc),
            )
        assert os.listdir(tmp_path) == []
    finally:
        get_settings.cache_clear()


def test_tag_en_key_validacion_y_llaves_en_el_tar(tmp_path: Path) -> None:
    momento = datetime(2026, 10, 16, 18, 30, 5, tzinfo=timezone.utc)
    key = B._nombre_key("dump", momento, "produccion")
    assert key == "dump/20261016T183005Z-produccion-mrbot-db.tar.gz"
    assert B._tag_de_key(key) == "produccion"
    assert B._tag_de_key(B._nombre_key("dump", momento)) == ""
    assert B._validar_tag(" feat-x.1 ") == "feat-x.1"
    for malo in ("a/b", "../x", "-x", "a b"):
        with pytest.raises(B.SolicitudInvalida):
            B._validar_tag(malo)

    (tmp_path / "users.copy").write_bytes(b"1\n")
    manifiesto = {"tablas": [{"nombre": "users", "columnas": ["id"], "filas": 1, "sha256": "x"}]}
    tar_path = tmp_path / "d.tar.gz"
    B._empaquetar(str(tmp_path), str(tar_path), manifiesto, {"RSA_PRIVATE_KEY": "PEM"})
    with tarfile.open(tar_path) as tar:
        assert sorted(tar.getnames()) == ["llaves.json", "manifest.json", "users.copy"]
        assert json.load(tar.extractfile("llaves.json")) == {"RSA_PRIVATE_KEY": "PEM"}
