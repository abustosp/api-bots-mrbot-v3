"""Integración real: dump y restore de PostgreSQL contra un bucket S3 falso.

Requiere ``CENTRAL_API_TEST_DATABASE_URL`` (PostgreSQL migrado a head). Sin ella
se omite. Crea sus propias tablas ``bkp_test_*`` con clave primaria simple,
clave foránea y una tabla sin PK, y las borra al terminar: nunca toca el resto
del esquema. El bucket es un cliente S3 en memoria (monkeypatch de
``_cliente_s3``): no se escribe nada en AWS ni en un storage real.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

DSN = os.environ.get("CENTRAL_API_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not DSN, reason="CENTRAL_API_TEST_DATABASE_URL no configurada"
)

TOKEN = "backups-pg-token"
H = {"Authorization": f"Bearer {TOKEN}"}
PADRE = "bkp_test_padre"
HIJO = "bkp_test_hijo"
SIN_PK = "bkp_test_sinpk"
MIS_TABLAS = (PADRE, HIJO, SIN_PK)

DDL = (
    f"DROP TABLE IF EXISTS {HIJO}",
    f"DROP TABLE IF EXISTS {PADRE}",
    f"DROP TABLE IF EXISTS {SIN_PK}",
    f"CREATE TABLE {PADRE} (id text PRIMARY KEY, nombre text NOT NULL)",
    f"CREATE TABLE {HIJO} (id text PRIMARY KEY, "
    f"padre_id text REFERENCES {PADRE}(id), nota text)",
    f"CREATE TABLE {SIN_PK} (a text, b text)",
)


def _conectar(dsn: str = DSN):
    import psycopg

    return psycopg.connect(dsn)


class BucketFalso:
    """Cliente S3 en memoria (nunca sale a la red)."""

    def __init__(self) -> None:
        self.objetos: dict[str, bytes] = {}
        self.subidas: list[str] = []

    def upload_file(self, ruta, bucket, key, ExtraArgs=None) -> None:
        self.objetos[key] = Path(ruta).read_bytes()
        self.subidas.append(key)

    def download_file(self, bucket, key, ruta) -> None:
        Path(ruta).write_bytes(self.objetos[key])

    def list_objects_v2(self, **kwargs) -> dict:
        return {
            "Contents": [
                {
                    "Key": clave,
                    "Size": len(datos),
                    "LastModified": datetime(2026, 10, 16, tzinfo=timezone.utc),
                }
                for clave, datos in sorted(self.objetos.items())
            ],
            "IsTruncated": False,
        }

    def generate_presigned_url(self, operacion, Params=None, ExpiresIn=None) -> str:
        return f"https://bucket-falso.local/{Params['Key']}?firma=local"


@pytest.fixture
def pg_backups(monkeypatch, tmp_path):
    from central_api import db
    from central_api.admin import backups as B
    from central_api.main import create_app
    from central_api.settings import get_settings

    with _conectar() as conn, conn.cursor() as cur:
        for sentencia in DDL:
            cur.execute(sentencia)
        cur.execute(
            f"INSERT INTO {PADRE} (id, nombre) VALUES ('p1', 'uno'), ('p2', 'dos')"
        )
        cur.execute(
            f"INSERT INTO {HIJO} (id, padre_id, nota) VALUES ('h1', 'p1', 'nota uno')"
        )
        cur.execute(f"INSERT INTO {SIN_PK} (a, b) VALUES ('x', 'y')")

    monkeypatch.setenv("DATABASE_URL", DSN)
    monkeypatch.setenv("ADMIN_TOKEN", TOKEN)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    monkeypatch.setenv("OBJECT_STORAGE_ENDPOINT", "http://bucket-falso.local:9000")
    monkeypatch.setenv("OBJECT_STORAGE_ACCESS_KEY", "clave-falsa")
    monkeypatch.setenv("OBJECT_STORAGE_SECRET_KEY", "secreto-falso")
    monkeypatch.setenv("BACKUP_BUCKET", "mrbot-bots-api-bkp-db")
    monkeypatch.setenv("BACKUP_PREFIX", "dump")
    monkeypatch.setenv("BACKUP_DIR", str(tmp_path / "backups"))
    for cache in (get_settings, db._fabrica, db._motor):
        cache.cache_clear()

    bucket = BucketFalso()
    monkeypatch.setattr(B, "_cliente_s3", lambda _settings: bucket)

    class Contexto:
        def __init__(self, cliente: TestClient) -> None:
            self.cliente = cliente
            self.bucket = bucket
            self.backups = B

    # La auditoría es global y en memoria: cada prueba restaura el largo previo
    # para no alterar las expectativas de otras pruebas del panel.
    from central_api.admin import audit

    inicio_auditoria = len(audit.AUDIT_LOG)
    try:
        with TestClient(create_app()) as cliente:
            yield Contexto(cliente)
    finally:
        del audit.AUDIT_LOG[inicio_auditoria:]
        with _conectar() as conn, conn.cursor() as cur:
            for tabla in MIS_TABLAS:
                cur.execute(f"DROP TABLE IF EXISTS {tabla} CASCADE")
        for cache in (get_settings, db._fabrica, db._motor):
            cache.cache_clear()


def _filas(tabla: str) -> list[tuple]:
    with _conectar() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {tabla} ORDER BY 1")
        return [tuple(fila) for fila in cur.fetchall()]


def _ejecutar(sql: str) -> None:
    with _conectar() as conn, conn.cursor() as cur:
        cur.execute(sql)


def _crear_dump(pg_backups, tablas=MIS_TABLAS, incluir_datos=True, motivo="respaldo de integración"):
    respuesta = pg_backups.cliente.post(
        "/admin/backups/dump",
        headers=H,
        json={"motivo": motivo, "tablas": list(tablas), "incluir_datos": incluir_datos},
    )
    assert respuesta.status_code == 200, respuesta.text
    return respuesta.json()


def _manifest_del_bucket(pg_backups, key: str) -> tuple[dict, dict[str, bytes]]:
    with tarfile.open(fileobj=io.BytesIO(pg_backups.bucket.objetos[key])) as tar:
        manifiesto = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
        copias = {
            nombre[: -len(".copy")]: tar.extractfile(nombre).read()
            for nombre in tar.getnames()
            if nombre.endswith(".copy")
        }
    return manifiesto, copias


def test_dump_real_genera_manifiesto_con_orden_filas_y_sha256(pg_backups) -> None:
    resultado = _crear_dump(pg_backups)
    assert resultado["success"] is True
    assert resultado["bucket"] == "mrbot-bots-api-bkp-db"
    assert re.fullmatch(
        r"dump/\d{8}T\d{6}Z-mrbot-db\.tar\.gz", resultado["key"]
    ), resultado["key"]
    assert resultado["size_bytes"] > 0
    assert resultado["size_raw"] > 0
    assert resultado["cantidad_tablas"] == 3
    assert resultado["total_filas"] == 4
    assert len(resultado["sha256"]) == 64
    assert pg_backups.bucket.subidas == [resultado["key"]]

    manifiesto, copias = _manifest_del_bucket(pg_backups, resultado["key"])
    assert manifiesto["version"] == 1
    assert manifiesto["database"] == "m"
    assert manifiesto["created_at"] == resultado["created_at"]
    nombres = [entrada["nombre"] for entrada in manifiesto["tablas"]]
    assert sorted(nombres) == sorted(MIS_TABLAS)
    # La tabla referenciada (padre) va antes que la que la referencia (hijo).
    assert nombres.index(PADRE) < nombres.index(HIJO)
    for entrada in manifiesto["tablas"]:
        crudo = copias[entrada["nombre"]]
        assert entrada["sha256"] == hashlib.sha256(crudo).hexdigest()
        assert entrada["filas"] == crudo.count(b"\n")
    assert {e["nombre"]: e["filas"] for e in manifiesto["tablas"]} == {
        PADRE: 2,
        HIJO: 1,
        SIN_PK: 1,
    }
    assert manifiesto["tablas"][0]["columnas"] == ["id", "nombre"]

    listado = pg_backups.cliente.get("/admin/backups", headers=H)
    assert listado.status_code == 200
    cuerpo = listado.json()
    assert cuerpo["success"] is True
    assert [item["key"] for item in cuerpo["objects"]] == [resultado["key"]]
    assert cuerpo["objects"][0]["size_bytes"] == resultado["size_bytes"]

    descarga = pg_backups.cliente.get(f"/admin/backups/{resultado['key']}", headers=H)
    assert descarga.status_code == 200
    assert descarga.json()["download_url"].startswith("https://bucket-falso.local/")


def test_dump_completo_ordena_por_fks_y_reporta_la_revision(pg_backups) -> None:
    """El dump por defecto incluye todas las tablas base de public."""
    respuesta = pg_backups.cliente.post(
        "/admin/backups/dump",
        headers=H,
        json={"motivo": "respaldo completo de integración"},
    )
    assert respuesta.status_code == 200, respuesta.text
    resultado = respuesta.json()
    assert resultado["cantidad_tablas"] >= 60
    with _conectar() as conn, conn.cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        revision = cur.fetchone()[0]
    assert resultado["alembic_revision"] == revision
    manifiesto, _ = _manifest_del_bucket(pg_backups, resultado["key"])
    nombres = [entrada["nombre"] for entrada in manifiesto["tablas"]]
    assert "alembic_version" in nombres
    assert "users" in nombres and "jobs" in nombres
    assert nombres.index(PADRE) < nombres.index(HIJO)
    assert len(nombres) == len(set(nombres))
    assert "worker_heartbeats" not in nombres


def test_dump_rechaza_pedir_explicitamente_worker_heartbeats(pg_backups) -> None:
    respuesta = pg_backups.cliente.post(
        "/admin/backups/dump",
        headers=H,
        json={"motivo": "respaldo con tabla excluida", "tablas": ["worker_heartbeats"]},
    )
    assert respuesta.status_code == 400, respuesta.text
    assert "worker_heartbeats" in respuesta.json()["detail"]


def test_dump_rechaza_tabla_inexistente(pg_backups) -> None:
    respuesta = pg_backups.cliente.post(
        "/admin/backups/dump",
        headers=H,
        json={"motivo": "respaldo con tabla inexistente", "tablas": ["no_existe_esta_tabla"]},
    )
    assert respuesta.status_code == 400
    assert "no_existe_esta_tabla" in respuesta.json()["detail"]
    assert pg_backups.bucket.objetos == {}


def test_restore_append_no_duplica_y_reinserta_lo_borrado(pg_backups) -> None:
    resultado = _crear_dump(pg_backups)
    key = resultado["key"]
    # Cambios posteriores al dump: se modifica una fila, se borra otra y se
    # agrega una fila nueva que el backup no conoce.
    _ejecutar(f"UPDATE {PADRE} SET nombre = 'CAMBIADO' WHERE id = 'p1'")
    _ejecutar(f"DELETE FROM {HIJO} WHERE id = 'h1'")
    _ejecutar(f"INSERT INTO {PADRE} (id, nombre) VALUES ('p3', 'tres')")

    respuesta = pg_backups.cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": key,
            "modo": "append",
            "motivo": "restauración append de integración",
            "tablas": [PADRE, HIJO],
            "confirmacion": "RESTAURAR",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["success"] is True
    assert cuerpo["modo"] == "append"
    assert cuerpo["dry_run"] is False
    por_tabla = {item["tabla"]: item for item in cuerpo["tablas"]}
    # padre: las dos filas del dump ya existen -> se omiten, no se duplican.
    assert por_tabla[PADRE]["filas_archivo"] == 2
    assert por_tabla[PADRE]["insertadas"] == 0
    assert por_tabla[PADRE]["omitidas"] == 2
    assert por_tabla[PADRE]["reemplazadas"] == 0
    # hijo: la fila borrada se reinserta.
    assert por_tabla[HIJO]["insertadas"] == 1
    assert cuerpo["resumen"]["insertadas"] == 1

    assert _filas(PADRE) == [("p1", "CAMBIADO"), ("p2", "dos"), ("p3", "tres")]
    assert _filas(HIJO) == [("h1", "p1", "nota uno")]


def test_restore_overwrite_reemplaza_por_clave_primaria(pg_backups) -> None:
    resultado = _crear_dump(pg_backups)
    key = resultado["key"]
    _ejecutar(f"UPDATE {PADRE} SET nombre = 'CAMBIADO' WHERE id = 'p1'")
    _ejecutar(f"DELETE FROM {HIJO} WHERE id = 'h1'")
    _ejecutar(f"INSERT INTO {PADRE} (id, nombre) VALUES ('p3', 'tres')")

    respuesta = pg_backups.cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": key,
            "modo": "overwrite",
            "motivo": "restauración overwrite de integración",
            "tablas": [PADRE, HIJO],
            "confirmacion": "RESTAURAR",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["success"] is True
    por_tabla = {item["tabla"]: item for item in cuerpo["tablas"]}
    # padre: p1 y p2 se pisan en el lugar (upsert), sin borrar la fila: el padre
    # tiene un hijo que lo referencia con ON DELETE RESTRICT.
    assert por_tabla[PADRE]["reemplazadas"] == 2
    assert por_tabla[PADRE]["insertadas"] == 0
    assert por_tabla[PADRE].get("error") is None
    assert por_tabla[PADRE]["sin_pk"] is False
    # hijo: no existía en el destino -> solo inserta.
    assert por_tabla[HIJO]["reemplazadas"] == 0
    assert por_tabla[HIJO]["insertadas"] == 1

    # La fila modificada vuelve al valor del dump; p3 no estaba en el backup y sigue.
    assert _filas(PADRE) == [("p1", "uno"), ("p2", "dos"), ("p3", "tres")]
    assert _filas(HIJO) == [("h1", "p1", "nota uno")]
    assert cuerpo["alembic_revision"] == resultado["alembic_revision"]
    assert cuerpo["alembic_mismatch"] is False


def test_restore_dry_run_no_muta_la_base(pg_backups) -> None:
    resultado = _crear_dump(pg_backups)
    _ejecutar(f"UPDATE {PADRE} SET nombre = 'CAMBIADO' WHERE id = 'p1'")
    _ejecutar(f"DELETE FROM {HIJO} WHERE id = 'h1'")

    respuesta = pg_backups.cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": resultado["key"],
            "modo": "overwrite",
            "motivo": "simulación de restauración de integración",
            "tablas": [PADRE, HIJO],
            "dry_run": True,
            "confirmacion": "RESTAURAR",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    assert cuerpo["dry_run"] is True
    por_tabla = {item["tabla"]: item for item in cuerpo["tablas"]}
    assert por_tabla[PADRE]["colisionarian"] == 2
    assert por_tabla[PADRE]["insertadas"] == 0
    # El plan del overwrite es un upsert (no borra la fila).
    assert len(por_tabla[PADRE]["plan"]) == 1
    assert por_tabla[PADRE]["plan"][0].startswith("INSERT INTO public.")
    assert "DO UPDATE SET" in por_tabla[PADRE]["plan"][0]
    assert por_tabla[HIJO]["colisionarian"] == 0
    # Nada cambió: la fila modificada sigue modificada y la borrada sigue ausente.
    assert _filas(PADRE) == [("p1", "CAMBIADO"), ("p2", "dos")]
    assert _filas(HIJO) == []

    # La misma simulación en modo append no propone ningún DELETE.
    respuesta = pg_backups.cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": resultado["key"],
            "modo": "append",
            "motivo": "simulación append de integración",
            "tablas": [PADRE],
            "dry_run": True,
            "confirmacion": "RESTAURAR",
        },
    )
    assert respuesta.status_code == 200
    plan = respuesta.json()["tablas"][0]["plan"]
    assert all(not sentencia.startswith("DELETE") for sentencia in plan)
    assert respuesta.json()["tablas"][0]["reemplazadas"] == 0


def test_restore_sin_pk_avisa_que_no_puede_decidir_el_pisoton(pg_backups) -> None:
    """Sin clave primaria no hay pisotón posible: se informa y solo se inserta."""
    resultado = _crear_dump(pg_backups)
    respuesta = pg_backups.cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": resultado["key"],
            "modo": "overwrite",
            "motivo": "restauración de tabla sin pk",
            "tablas": [SIN_PK],
            "confirmacion": "RESTAURAR",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    item = respuesta.json()["tablas"][0]
    assert item["sin_pk"] is True
    assert item["reemplazadas"] == 0
    assert "advertencia" in item
    assert "error" not in item


def test_restore_aborta_si_el_sha256_no_coincide_y_no_toca_la_base(pg_backups) -> None:
    resultado = _crear_dump(pg_backups)
    key = resultado["key"]
    manifiesto, copias = _manifest_del_bucket(pg_backups, key)
    # Se reescribe el tar con el .copy del padre alterado y el mismo manifiesto.
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for nombre, datos in copias.items():
            contenido = b"p1\tALTERADO\n" if nombre == PADRE else datos
            info = tarfile.TarInfo(f"{nombre}.copy")
            info.size = len(contenido)
            tar.addfile(info, io.BytesIO(contenido))
        crudo = json.dumps(manifiesto).encode("utf-8")
        info = tarfile.TarInfo("manifest.json")
        info.size = len(crudo)
        tar.addfile(info, io.BytesIO(crudo))
    pg_backups.bucket.objetos[key] = buffer.getvalue()

    antes = _filas(PADRE)
    respuesta = pg_backups.cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": key,
            "modo": "overwrite",
            "motivo": "restauración de backup corrupto",
            "confirmacion": "RESTAURAR",
        },
    )
    assert respuesta.status_code == 400
    assert "sha256" in respuesta.json()["detail"]
    assert _filas(PADRE) == antes


def test_restore_informa_tabla_ausente_sin_abortar_las_demas(pg_backups) -> None:
    resultado = _crear_dump(pg_backups)
    _ejecutar(f"DROP TABLE {HIJO}")
    respuesta = pg_backups.cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": resultado["key"],
            "modo": "append",
            "motivo": "restauración con tabla ausente",
            "confirmacion": "RESTAURAR",
        },
    )
    assert respuesta.status_code == 200, respuesta.text
    cuerpo = respuesta.json()
    por_tabla = {item["tabla"]: item for item in cuerpo["tablas"]}
    assert por_tabla[HIJO]["error_tipo"] == "tabla_ausente"
    assert "no existe" in por_tabla[HIJO]["error"]
    assert por_tabla[PADRE].get("error") is None
    assert por_tabla[SIN_PK].get("error") is None
    assert cuerpo["resumen"]["tablas_con_error"] == 1
    assert cuerpo["success"] is False
    assert cuerpo["resumen"]["detenido"] is False


def test_restore_audita_y_valida_confirmacion(pg_backups) -> None:
    from central_api.admin import audit

    resultado = _crear_dump(pg_backups)
    rechazado = pg_backups.cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": resultado["key"],
            "modo": "append",
            "motivo": "restauración sin confirmación válida",
            "confirmacion": "SI",
        },
    )
    assert rechazado.status_code == 400

    antes = len(audit.AUDIT_LOG)
    aceptado = pg_backups.cliente.post(
        "/admin/backups/restore",
        headers=H,
        json={
            "key": resultado["key"],
            "modo": "append",
            "motivo": "restauración auditada de integración",
            "tablas": [PADRE],
            "confirmacion": "RESTAURAR",
        },
    )
    assert aceptado.status_code == 200, aceptado.text
    eventos = [e for e in audit.AUDIT_LOG[antes:]]
    assert [e.action for e in eventos] == ["backup.restore"]
    assert eventos[0].reason == "restauración auditada de integración"
    assert eventos[0].result == "success"
    # El motivo y el modo quedan en auditoría; nada de credenciales del bucket.
    assert eventos[0].metadata_redacted["modo"] == "append"
    assert "secreto-falso" not in json.dumps(eventos[0].metadata_redacted)


def test_dump_sin_datos_deja_solo_cabeceras(pg_backups) -> None:
    resultado = _crear_dump(pg_backups, incluir_datos=False)
    assert resultado["total_filas"] == 0
    manifiesto, copias = _manifest_del_bucket(pg_backups, resultado["key"])
    assert all(entrada["filas"] == 0 for entrada in manifiesto["tablas"])
    assert all(copias[nombre] == b"" for nombre in copias)
    assert manifiesto["incluir_datos"] is False
    # Las columnas siguen en el manifiesto: el dump sin datos es utilizable.
    assert manifiesto["tablas"][0]["columnas"] == ["id", "nombre"]
