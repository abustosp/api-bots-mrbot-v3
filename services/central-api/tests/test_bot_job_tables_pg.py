"""Aceptación PostgreSQL real de la revisión 0015 (detalle físico por bot).

La suite se ``skip`` completa salvo que exista ``CENTRAL_API_TEST_DATABASE_URL``
(o, en su defecto, ``DATABASE_URL``) apuntando a una base PostgreSQL
**descartable**: el módulo aplica, revierte y vuelve a aplicar migraciones
reales sobre esa base.

DESTRUCTIVA: la fixture de módulo baja a ``base`` y vuelve a subir por ``0014``,
así que **borra todas las tablas y filas** de la base apuntada y la deja con el
catálogo sintético que siembra la propia suite. Nunca debe apuntarse a una
producción ni a una réplica restaurada de producción: para ensayos sobre datos
reales usar solo ``test_bot_tables_lifecycle_pg.py``, que crea y borra sus
propias filas.

Ejecución:

    export CENTRAL_API_TEST_DATABASE_URL=\\
        "postgresql://mrbot_test:localonlytest@127.0.0.1:32769/mrbot_test"
    cd services/central-api && ../../.venv/bin/python -m pytest \\
        tests/test_bot_job_tables_pg.py -v

El DSN se entrega **sin driver explícito** (``postgresql://``): el mismo camino
de normalización a ``psycopg`` que usan ``alembic/env.py`` y la API queda
ejercitado por la suite.

Los tests comparten una única base y dependen del orden de definición (el
proyecto no usa plugins de ordenamiento): preparación en 0014 -> estructura ->
backfill/redacción -> triggers -> cascade -> cambio de bot -> ciclo
downgrade/upgrade.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy import pool

SERVICE_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_INI = SERVICE_DIR / "alembic" / "alembic.ini"
REVISION_PATH = SERVICE_DIR / "alembic" / "versions" / "0015_bot_job_tables.py"

_EJEMPLO_DSN = "postgresql://mrbot_test:localonlytest@127.0.0.1:32769/mrbot_test"
_COMO_CORRER = (
    "test_bot_job_tables_pg requiere una base PostgreSQL descartable; "
    "CENTRAL_API_TEST_DATABASE_URL (o DATABASE_URL) no está configurada.\n"
    "Para correrla:\n"
    f"    export CENTRAL_API_TEST_DATABASE_URL='{_EJEMPLO_DSN}'\n"
    "    cd services/central-api && ../../.venv/bin/python -m pytest "
    "tests/test_bot_job_tables_pg.py -v\n"
    "ATENCIÓN: la suite aplica y revierte migraciones sobre esa base y "
    "borra todo su contenido (baja a base antes de subir por 0014)."
)

_TEST_DSN = (
    os.environ.get("CENTRAL_API_TEST_DATABASE_URL")
    or os.environ.get("DATABASE_URL")
    or ""
).strip()

if not _TEST_DSN:
    pytest.skip(_COMO_CORRER, allow_module_level=True)

_SRC = SERVICE_DIR / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from central_api.db import normalizar_dsn  # noqa: E402


def _cargar_revision():
    """Carga ``0015_bot_job_tables.py`` sin importar la aplicación."""
    spec = importlib.util.spec_from_file_location("bot_jobs_revision_0015_pg", REVISION_PATH)
    assert spec is not None and spec.loader is not None
    revision = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(revision)
    return revision


REVISION = _cargar_revision()
BOT_CODES = tuple(REVISION.BOT_CODES)
DETAIL_COLUMNS = frozenset(REVISION._DETAIL_COLUMNS)
FUNCIONES_0015 = (
    "sanitize_bot_job_json",
    "sync_bot_job_detail",
    "sync_bot_job_detail_from_job",
    "sync_bot_job_detail_from_child",
)
# Columnas canónicas que NUNCA deben copiarse al detalle físico.
COLUMNAS_PROHIBIDAS = frozenset(
    {
        "credential_ciphertext",
        "error_message",
        "idempotency_key",
        "lease_expires_at",
        "object_key",
    }
)

# --- Datos canónicos sembrados en 0014 (antes del backfill) -----------------

BOT_A = "apoc"
BOT_B = "ccma"
BOT_C = "srt"
OPERACION = "consulta"

JOB_A_ID = uuid.UUID("0015aaaa-0000-4000-8000-0000000000a1")
JOB_B_ID = uuid.UUID("0015bbbb-0000-4000-8000-0000000000b2")
JOB_C_ID = uuid.UUID("0015cccc-0000-4000-8000-0000000000c3")
USER_ID = uuid.UUID("0015dddd-0000-4000-8000-0000000000d4")
WORKER_ID = uuid.UUID("0015eeee-0000-4000-8000-0000000000e5")

_CIPHERTEXT_A = "SECRET-CIPHERTEXT-VALUE"

REQUEST_A = {
    "cuit": "20301234567",
    "periodo": "2026-08",
    "notas": "token=SECRET-INLINE-TOKEN-VALUE",
    "endpoint": "https://internal.invalid/api/v1/consultar",
    "security": {"nivel": "alto", "revisado": True, "comentario": "security review ok"},
    "api_key": "SECRET-API-KEY-VALUE",
    "access_token": "SECRET-ACCESS-TOKEN-VALUE",
    "callback_uri": "https://internal.invalid/callback",
    "authorization": "Bearer SECRET-BEARER-VALUE",
}
REQUEST_A_EN_DETALLE = {
    "cuit": "20301234567",
    "periodo": "2026-08",
    "notas": "token=[REDACTED_SECRET]",
    "endpoint": "[REDACTED_URL]",
    "security": {"nivel": "alto", "revisado": True, "comentario": "security review ok"},
}

RESPONSE_A = {
    "estado": "OK",
    "detalle": "consulta realizada",
    "reintento_endpoint": "https://internal.invalid/retry",
    "bearer_header": "Bearer SECRET-BEARER-IN-RESPONSE",
    "credencial": "SECRET-CREDENCIAL-VALUE",
    "security": {"ok": True},
}
RESPONSE_A_EN_DETALLE = {
    "estado": "OK",
    "detalle": "consulta realizada",
    "reintento_endpoint": "[REDACTED_URL]",
    "bearer_header": "Bearer [REDACTED_SECRET]",
    "security": {"ok": True},
}

SUMMARY_A = {"mensaje": "listo", "security": "nivel-alto"}
SUMMARY_A_EN_DETALLE = {"mensaje": "listo", "security": "nivel-alto"}

CRED_META_A = {
    "cuit": "20301234567",
    "sujeto": "ACME",
    "clave_fiscal": "SECRET-CLAVE-FISCAL-VALUE",
    "security": {"nivel": "alto"},
}
CRED_META_A_EN_DETALLE = {
    "cuit": "20301234567",
    "sujeto": "ACME",
    "security": {"nivel": "alto"},
}

ARTIFACTO_PLANO = {
    "id": uuid.UUID("0015f000-0000-4000-8000-0000000000f1"),
    "kind": "ARCHIVO",
    "object_key": "jobs/apoc/informe.pdf",
    "filename": "informe.pdf",
    "content_type": "application/pdf",
    "size_bytes": 1234,
    "sha256": "a" * 64,
}
ARTIFACTO_PRESIGNADO = {
    "id": uuid.UUID("0015f000-0000-4000-8000-0000000000f2"),
    "kind": "CAPTURA",
    "object_key": "jobs/apoc/captura.png",
    "filename": "https://minio.invalid/mrbot/captura.png?X-Amz-Signature=PRESIGN-SIG-VALUE",
    "content_type": "image/png",
    "size_bytes": 4321,
    "sha256": "b" * 64,
}
CLAVES_ARTIFACT_METADATA = {
    "kind",
    "filename",
    "content_type",
    "size_bytes",
    "sha256",
    "created_at",
    "expires_at",
}
# Marcadores que jamás deben aparecer en el detalle físico.
MARCADORES_PROHIBIDOS = (
    "SECRET-INLINE-TOKEN-VALUE",
    "SECRET-API-KEY-VALUE",
    "SECRET-ACCESS-TOKEN-VALUE",
    "SECRET-BEARER-VALUE",
    "SECRET-CREDENCIAL-VALUE",
    "SECRET-BEARER-IN-RESPONSE",
    "SECRET-CLAVE-FISCAL-VALUE",
    _CIPHERTEXT_A,
    "internal.invalid",
    "minio.invalid",
    "X-Amz-Signature",
    "PRESIGN-SIG-VALUE",
    ARTIFACTO_PLANO["object_key"],
    ARTIFACTO_PRESIGNADO["object_key"],
)

_SQL_INSERT_JOB = """
INSERT INTO jobs (
    id, user_id, worker_id, bot, operation, status, result, priority, attempts,
    max_attempts, app_version, protocol_version, request_payload,
    credential_ciphertext, credential_metadata, created_at, assigned_at,
    started_at, finished_at
) VALUES (
    :id, :user_id, :worker_id, :bot, :operation, :status, :result, :priority,
    :attempts, :max_attempts, :app_version, :protocol_version,
    CAST(:request_payload AS jsonb), :credential_ciphertext,
    CAST(:credential_metadata AS jsonb), :created_at, :assigned_at,
    :started_at, :finished_at
)
"""

_SQL_UPSERT_JOB_RESULT = """
INSERT INTO job_results (job_id, attempt, result, payload, summary, received_at)
VALUES (
    :job_id, :attempt, :result, CAST(:payload AS jsonb),
    CAST(:summary AS jsonb), :received_at
)
ON CONFLICT (job_id) DO UPDATE SET
    attempt = EXCLUDED.attempt,
    result = EXCLUDED.result,
    payload = EXCLUDED.payload,
    summary = EXCLUDED.summary,
    received_at = EXCLUDED.received_at
"""

_SQL_INSERT_ARTIFACT = """
INSERT INTO job_artifacts (
    id, job_id, kind, object_key, filename, content_type, size_bytes, sha256,
    created_at, expires_at
) VALUES (
    :id, :job_id, :kind, :object_key, :filename, :content_type, :size_bytes,
    :sha256, :created_at, :expires_at
)
"""


# --- Herramientas ----------------------------------------------------------


def _tabla(bot_code: str) -> str:
    assert bot_code in BOT_CODES, f"bot fuera del catálogo congelado: {bot_code!r}"
    return f"bot_jobs_{bot_code}"


def _alembic(*args: str) -> None:
    """Corre el CLI de Alembic contra la base de prueba con DSN plano."""
    completado = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC_INI), *args],
        cwd=str(SERVICE_DIR),
        env={**os.environ, "DATABASE_URL": _TEST_DSN},
        capture_output=True,
        text=True,
    )
    if completado.returncode != 0:
        raise AssertionError(
            f"alembic {' '.join(args)} falló (rc={completado.returncode})\n"
            f"--- stdout ---\n{completado.stdout}\n--- stderr ---\n{completado.stderr}"
        )


class Pg:
    """Acceso síncrono a la base de prueba (DSN normalizado a psycopg)."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self.engine = sa.create_engine(normalizar_dsn(dsn), poolclass=pool.NullPool)

    def scalar(self, sql: str, **params: Any) -> Any:
        with self.engine.connect() as conn:
            return conn.execute(sa.text(sql), params).scalar()

    def filas(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        with self.engine.connect() as conn:
            return [dict(fila) for fila in conn.execute(sa.text(sql), params).mappings()]

    def ejecutar(self, sql: str, **params: Any) -> None:
        with self.engine.begin() as conn:
            conn.execute(sa.text(sql), params)

    @contextmanager
    def transaccion(self) -> Iterator[sa.Connection]:
        """Transacción explícita que SIEMPRE termina en rollback."""
        conn = self.engine.connect()
        trans = conn.begin()
        try:
            yield conn
        finally:
            trans.rollback()
            conn.close()

    # -- consultas de estado ------------------------------------------------
    def tablas_fisicas(self) -> set[str]:
        return {
            fila["table_name"]
            for fila in self.filas(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name ~ '^bot_jobs_'"
            )
        }

    def funciones(self) -> set[str]:
        return {
            fila["proname"]
            for fila in self.filas(
                "SELECT p.proname FROM pg_proc AS p "
                "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                "WHERE n.nspname = 'public'"
            )
        }

    def triggers_detalle(self) -> set[str]:
        return {
            fila["tgname"]
            for fila in self.filas(
                "SELECT tgname FROM pg_trigger WHERE NOT tgisinternal "
                "AND tgname LIKE 'trg_%_bot_detail_sync'"
            )
        }

    def version(self) -> str | None:
        if self.scalar(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
        ):
            return self.scalar("SELECT version_num FROM alembic_version")
        return None

    def columnas(self, tabla: str) -> set[str]:
        return {
            fila["column_name"]
            for fila in self.filas(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :tabla",
                tabla=tabla,
            )
        }

    def detalle(self, bot_code: str, job_id: uuid.UUID) -> dict[str, Any] | None:
        filas = self.filas(
            f"SELECT * FROM {_tabla(bot_code)} WHERE job_id = :jid", jid=job_id
        )
        assert len(filas) <= 1, f"{_tabla(bot_code)} tiene {len(filas)} filas para {job_id}"
        return filas[0] if filas else None

    def job(self, job_id: uuid.UUID) -> dict[str, Any] | None:
        filas = self.filas("SELECT * FROM jobs WHERE id = :jid", jid=job_id)
        return filas[0] if filas else None


def _insertar_job(
    pg: Pg,
    job_id: uuid.UUID,
    bot: str,
    *,
    status: str,
    result: str | None,
    request_payload: dict[str, Any],
    credential_metadata: dict[str, Any],
    credential_ciphertext: str | None = None,
    worker_id: uuid.UUID | None = None,
    finished_at: datetime | None = None,
) -> None:
    ahora = datetime.now(timezone.utc)
    pg.ejecutar(
        _SQL_INSERT_JOB,
        id=job_id,
        user_id=USER_ID,
        worker_id=worker_id,
        bot=bot,
        operation=OPERACION,
        status=status,
        result=result,
        priority=42,
        attempts=1,
        max_attempts=3,
        app_version="1.2.3-test",
        protocol_version=1,
        request_payload=json.dumps(request_payload),
        credential_ciphertext=credential_ciphertext,
        credential_metadata=json.dumps(credential_metadata),
        created_at=ahora,
        assigned_at=ahora,
        started_at=ahora,
        finished_at=finished_at,
    )


def _sembrar_canonico(pg: Pg) -> None:
    """Catálogo + job A (con resultado y artefactos) en la revisión 0014."""
    for code, display in ((BOT_A, "Apoc"), (BOT_B, "CCMA")):
        pg.ejecutar(
            "INSERT INTO bots (id, code, display_name) VALUES (:id, :code, :display)",
            id=uuid.uuid4(),
            code=code,
            display=display,
        )
        pg.ejecutar(
            "INSERT INTO bot_operations (id, bot_code, code, input_schema_version) "
            "VALUES (:id, :bot_code, :code, :schema)",
            id=uuid.uuid4(),
            bot_code=code,
            code=OPERACION,
            schema="1",
        )
    pg.ejecutar(
        "INSERT INTO users (id, email, habilitado) VALUES (:id, :email, true)",
        id=USER_ID,
        email="0015-pg-suite@example.invalid",
    )
    pg.ejecutar(
        "INSERT INTO workers (id, name, endpoint, app_version) "
        "VALUES (:id, :name, :endpoint, :app_version)",
        id=WORKER_ID,
        name="0015-pg-suite-worker",
        endpoint="10.255.0.15:8080",
        app_version="1.2.3-test",
    )
    _insertar_job(
        pg,
        JOB_A_ID,
        BOT_A,
        status="COMPLETO",
        result="OK",
        request_payload=REQUEST_A,
        credential_metadata=CRED_META_A,
        credential_ciphertext=_CIPHERTEXT_A,
        worker_id=WORKER_ID,
        finished_at=datetime.now(timezone.utc),
    )
    pg.ejecutar(
        _SQL_UPSERT_JOB_RESULT,
        job_id=JOB_A_ID,
        attempt=1,
        result="OK",
        payload=json.dumps(RESPONSE_A),
        summary=json.dumps(SUMMARY_A),
        received_at=datetime.now(timezone.utc),
    )
    base = datetime.now(timezone.utc)
    pg.ejecutar(
        _SQL_INSERT_ARTIFACT,
        id=ARTIFACTO_PLANO["id"],
        job_id=JOB_A_ID,
        kind=ARTIFACTO_PLANO["kind"],
        object_key=ARTIFACTO_PLANO["object_key"],
        filename=ARTIFACTO_PLANO["filename"],
        content_type=ARTIFACTO_PLANO["content_type"],
        size_bytes=ARTIFACTO_PLANO["size_bytes"],
        sha256=ARTIFACTO_PLANO["sha256"],
        created_at=base - timedelta(minutes=2),
        expires_at=None,
    )
    pg.ejecutar(
        _SQL_INSERT_ARTIFACT,
        id=ARTIFACTO_PRESIGNADO["id"],
        job_id=JOB_A_ID,
        kind=ARTIFACTO_PRESIGNADO["kind"],
        object_key=ARTIFACTO_PRESIGNADO["object_key"],
        filename=ARTIFACTO_PRESIGNADO["filename"],
        content_type=ARTIFACTO_PRESIGNADO["content_type"],
        size_bytes=ARTIFACTO_PRESIGNADO["size_bytes"],
        sha256=ARTIFACTO_PRESIGNADO["sha256"],
        created_at=base - timedelta(minutes=1),
        expires_at=base + timedelta(days=1),
    )


@pytest.fixture(scope="module")
def pg() -> Iterator[Pg]:
    """Deja la base en 0014, siembra lo canónico y aplica 0015 (backfill real)."""
    instancia = Pg(_TEST_DSN)
    assert instancia.scalar("SELECT 1") == 1
    if instancia.version():
        _alembic("downgrade", "base")
    _alembic("upgrade", "0014")
    assert instancia.version() == "0014"
    _sembrar_canonico(instancia)
    _alembic("upgrade", "0015")
    try:
        yield instancia
    finally:
        instancia.engine.dispose()


# --- 1. Estructura tras el upgrade -----------------------------------------


def test_upgrade_crea_las_32_tablas_fisicas_y_sella_0015(pg: Pg) -> None:
    assert pg.version() == "0015"
    tablas = pg.tablas_fisicas()
    assert tablas == {_tabla(codigo) for codigo in BOT_CODES}
    assert len(tablas) == 32

    assert pg.columnas(_tabla(BOT_A)) == set(DETAIL_COLUMNS)
    assert not (pg.columnas(_tabla(BOT_A)) & COLUMNAS_PROHIBIDAS)

    funciones = pg.funciones()
    assert set(FUNCIONES_0015) <= funciones
    assert pg.triggers_detalle() == {
        "trg_jobs_bot_detail_sync",
        "trg_job_results_bot_detail_sync",
        "trg_job_artifacts_bot_detail_sync",
    }


# --- 2. Backfill y redacción ----------------------------------------------


def test_backfill_copia_el_job_canonico_redactando_secretos(pg: Pg) -> None:
    fila = pg.detalle(BOT_A, JOB_A_ID)
    assert fila is not None, "el backfill no creó la fila física del job canónico"
    canonico = pg.job(JOB_A_ID)
    assert canonico is not None

    # espejo escalar del job canónico
    for columna in (
        "user_id",
        "worker_id",
        "bot",
        "operation",
        "status",
        "result",
        "priority",
        "attempts",
        "max_attempts",
        "app_version",
        "protocol_version",
        "created_at",
        "assigned_at",
        "started_at",
        "finished_at",
    ):
        assert fila[columna] == canonico[columna], columna
    assert fila["updated_at"] is not None

    # request + response presentes y redactados
    assert fila["request_payload"] == REQUEST_A_EN_DETALLE
    assert fila["response_payload"] == RESPONSE_A_EN_DETALLE
    assert fila["response_summary"] == SUMMARY_A_EN_DETALLE
    assert fila["credential_metadata"] == CRED_META_A_EN_DETALLE
    assert fila["response_received_at"] == pg.scalar(
        "SELECT received_at FROM job_results WHERE job_id = :jid", jid=JOB_A_ID
    )

    # la clave benigna ``security`` sobrevive verbatim en los cuatro documentos
    assert fila["request_payload"]["security"] == REQUEST_A["security"]
    assert fila["response_payload"]["security"] == RESPONSE_A["security"]
    assert fila["response_summary"]["security"] == SUMMARY_A["security"]
    assert fila["credential_metadata"]["security"] == CRED_META_A["security"]

    # ninguna clave secreta sobrevive ni como nombre de clave
    for documento in (
        "request_payload",
        "response_payload",
        "response_summary",
        "credential_metadata",
    ):
        claves = set(fila[documento])
        assert not (
            claves
            & {"api_key", "access_token", "callback_uri", "authorization", "credencial", "clave_fiscal"}
        ), documento

    serializado = json.dumps(fila, default=str)
    for marcador in MARCADORES_PROHIBIDOS:
        assert marcador not in serializado, f"fuga de {marcador!r} al detalle físico"
    # anti-vacuidad: el canónico sí contiene todo lo que el detalle no expone
    canonico_serializado = _canonico_serializado(pg, JOB_A_ID)
    for marcador in MARCADORES_PROHIBIDOS:
        assert marcador in canonico_serializado, f"el canónico perdió {marcador!r}"

    # el ciphertext canónico y las columnas que no se copian
    assert "credential_ciphertext" in pg.columnas("jobs")
    assert "credential_ciphertext" not in fila

    # artefactos del backfill
    assert fila["artifact_names"] == ["informe.pdf", "[REDACTED_URL]"]
    metadata = fila["artifact_metadata"]
    assert isinstance(metadata, list) and len(metadata) == 2
    for entrada in metadata:
        assert set(entrada) == CLAVES_ARTIFACT_METADATA
    assert [entrada["kind"] for entrada in metadata] == ["ARCHIVO", "CAPTURA"]
    assert [entrada["filename"] for entrada in metadata] == ["informe.pdf", "[REDACTED_URL]"]
    assert [entrada["size_bytes"] for entrada in metadata] == [1234, 4321]
    assert metadata[1]["expires_at"] is not None


# --- 3. Triggers -----------------------------------------------------------


def test_triggers_proyectan_insert_update_resultados_y_artefactos(pg: Pg) -> None:
    request_b = {
        "cuit": "27987654321",
        "endpoint": "https://internal.invalid/b",
        "security": {"nivel": "medio"},
    }
    _insertar_job(
        pg,
        JOB_B_ID,
        BOT_B,
        status="PENDIENTE",
        result=None,
        request_payload=request_b,
        credential_metadata={"cuit": "27987654321", "security": {"nivel": "medio"}},
    )
    assert pg.detalle(BOT_A, JOB_B_ID) is None

    fila = pg.detalle(BOT_B, JOB_B_ID)
    assert fila is not None, "INSERT en jobs no proyectó la fila física"
    assert fila["status"] == "PENDIENTE"
    assert fila["request_payload"] == {
        "cuit": "27987654321",
        "endpoint": "[REDACTED_URL]",
        "security": {"nivel": "medio"},
    }
    assert fila["response_payload"] is None
    assert fila["response_summary"] is None
    assert fila["response_received_at"] is None
    assert fila["artifact_names"] == []
    assert fila["artifact_metadata"] == []

    pg.ejecutar("UPDATE jobs SET status = 'CORRIENDO' WHERE id = :jid", jid=JOB_B_ID)
    assert pg.detalle(BOT_B, JOB_B_ID)["status"] == "CORRIENDO"

    # alta del resultado: ``uri`` es clave de URL y se descarta completa; el
    # valor URL de una clave benigna queda como [REDACTED_URL]
    recibido = datetime.now(timezone.utc)
    pg.ejecutar(
        _SQL_UPSERT_JOB_RESULT,
        job_id=JOB_B_ID,
        attempt=1,
        result="OK",
        payload=json.dumps(
            {
                "intento": 1,
                "reintento": "https://internal.invalid/r",
                "uri": "https://internal.invalid/descartada",
            }
        ),
        summary=json.dumps({"intento": 1}),
        received_at=recibido,
    )
    fila = pg.detalle(BOT_B, JOB_B_ID)
    assert fila["response_payload"] == {
        "intento": 1,
        "reintento": "[REDACTED_URL]",
    }
    assert fila["response_summary"] == {"intento": 1}
    assert fila["response_received_at"] == recibido

    # upsert del resultado (mismo job_id): la proyección vuelve a reflejarlo
    recibido2 = datetime.now(timezone.utc)
    pg.ejecutar(
        _SQL_UPSERT_JOB_RESULT,
        job_id=JOB_B_ID,
        attempt=2,
        result="PARCIAL",
        payload=json.dumps(
            {
                "intento": 2,
                "token": "SECRET-UPSERT-TOKEN",
                "detalle": "api_key=SECRET-INLINE-TRIGGER",
            }
        ),
        summary=json.dumps({"intento": 2, "nota": "reintento"}),
        received_at=recibido2,
    )
    fila = pg.detalle(BOT_B, JOB_B_ID)
    assert fila["response_payload"] == {
        "intento": 2,
        "detalle": "api_key=[REDACTED_SECRET]",
    }
    assert fila["response_summary"] == {"intento": 2, "nota": "reintento"}
    assert fila["response_received_at"] == recibido2
    assert pg.scalar(
        "SELECT attempt FROM job_results WHERE job_id = :jid", jid=JOB_B_ID
    ) == 2

    # artefactos: solo nombre + metadata, nunca object_key ni URL prefirmada
    base = datetime.now(timezone.utc)
    plano = uuid.UUID("0015f000-0000-4000-8000-00000000b001")
    firmado = uuid.UUID("0015f000-0000-4000-8000-00000000b002")
    pg.ejecutar(
        _SQL_INSERT_ARTIFACT,
        id=plano,
        job_id=JOB_B_ID,
        kind="ARCHIVO",
        object_key="jobs/ccma/plano.csv",
        filename="plano.csv",
        content_type="text/csv",
        size_bytes=7,
        sha256="c" * 64,
        created_at=base - timedelta(minutes=2),
        expires_at=None,
    )
    pg.ejecutar(
        _SQL_INSERT_ARTIFACT,
        id=firmado,
        job_id=JOB_B_ID,
        kind="TRACE",
        object_key="jobs/ccma/trace.zip",
        filename="https://minio.invalid/mrbot/trace.zip?X-Amz-Signature=PRESIGN-SIG-VALUE",
        content_type="application/zip",
        size_bytes=99,
        sha256="d" * 64,
        created_at=base - timedelta(minutes=1),
        expires_at=None,
    )
    fila = pg.detalle(BOT_B, JOB_B_ID)
    assert fila["artifact_names"] == ["plano.csv", "[REDACTED_URL]"]
    metadata = fila["artifact_metadata"]
    assert [entrada["kind"] for entrada in metadata] == ["ARCHIVO", "TRACE"]
    assert [entrada["filename"] for entrada in metadata] == ["plano.csv", "[REDACTED_URL]"]
    for entrada in metadata:
        assert set(entrada) == CLAVES_ARTIFACT_METADATA
        assert "object_key" not in entrada
    serializado = json.dumps(fila, default=str)
    for marcador in (
        "jobs/ccma/plano.csv",
        "jobs/ccma/trace.zip",
        "minio.invalid",
        "X-Amz-Signature",
        "PRESIGN-SIG-VALUE",
        "SECRET-UPSERT-TOKEN",
        "SECRET-INLINE-TRIGGER",
        "internal.invalid",
    ):
        assert marcador not in serializado, f"fuga de {marcador!r} al detalle físico"

    # anti-vacuidad: el canónico sí guarda los secretos y las URLs que el
    # detalle físico no debe exponer
    canonico = _canonico_serializado(pg, JOB_B_ID)
    for marcador in (
        "jobs/ccma/plano.csv",
        "jobs/ccma/trace.zip",
        "minio.invalid",
        "X-Amz-Signature",
        "SECRET-UPSERT-TOKEN",
        "SECRET-INLINE-TRIGGER",
        "internal.invalid",
    ):
        assert marcador in canonico, f"el canónico perdió {marcador!r}"


# --- 4. Cascade ------------------------------------------------------------


def test_delete_canonico_elimina_la_fila_fisica(pg: Pg) -> None:
    assert pg.detalle(BOT_B, JOB_B_ID) is not None
    pg.ejecutar("DELETE FROM jobs WHERE id = :jid", jid=JOB_B_ID)

    assert pg.job(JOB_B_ID) is None
    assert pg.detalle(BOT_B, JOB_B_ID) is None, "la fila física sobrevivió al DELETE canónico"
    assert pg.scalar(
        "SELECT count(*) FROM job_results WHERE job_id = :jid", jid=JOB_B_ID
    ) == 0
    assert pg.scalar(
        "SELECT count(*) FROM job_artifacts WHERE job_id = :jid", jid=JOB_B_ID
    ) == 0
    # el resto de las proyecciones sigue intacto
    assert pg.detalle(BOT_A, JOB_A_ID) is not None


def _canonico_serializado(pg: Pg, job_id: uuid.UUID) -> str:
    """Volcado de TODAS las filas canónicas del job (jobs + results + artifacts)."""
    filas = {
        "jobs": pg.filas("SELECT * FROM jobs WHERE id = :jid", jid=job_id),
        "job_results": pg.filas("SELECT * FROM job_results WHERE job_id = :jid", jid=job_id),
        "job_artifacts": pg.filas(
            "SELECT * FROM job_artifacts WHERE job_id = :jid", jid=job_id
        ),
    }
    return json.dumps(filas, default=str)


def _detalle_en(conn: sa.Connection, bot_code: str, job_id: uuid.UUID) -> dict[str, Any] | None:
    filas = conn.execute(
        sa.text(f"SELECT * FROM {_tabla(bot_code)} WHERE job_id = :jid"), {"jid": job_id}
    ).mappings().all()
    assert len(filas) <= 1
    return dict(filas[0]) if filas else None


# --- 5. Cambio de bot ------------------------------------------------------


def test_cambio_de_bot_mueve_la_fila_entre_tablas_fisicas(pg: Pg) -> None:
    assert BOT_C in BOT_CODES and BOT_C != BOT_B
    with pg.transaccion() as conn:
        # catálogo mínimo para el bot destino: se crea y se revierte con la tx
        conn.execute(
            sa.text("INSERT INTO bots (id, code, display_name) VALUES (:id, :code, :display)"),
            {"id": uuid.uuid4(), "code": BOT_C, "display": "SRT"},
        )
        conn.execute(
            sa.text(
                "INSERT INTO bot_operations (id, bot_code, code, input_schema_version) "
                "VALUES (:id, :code, :op, :schema)"
            ),
            {"id": uuid.uuid4(), "code": BOT_C, "op": OPERACION, "schema": "1"},
        )
        conn.execute(
            sa.text(_SQL_INSERT_JOB),
            {
                "id": JOB_C_ID,
                "user_id": USER_ID,
                "worker_id": None,
                "bot": BOT_B,
                "operation": OPERACION,
                "status": "PENDIENTE",
                "result": None,
                "priority": 42,
                "attempts": 0,
                "max_attempts": 3,
                "app_version": "1.2.3-test",
                "protocol_version": 1,
                "request_payload": json.dumps({"security": {"nivel": "bajo"}}),
                "credential_ciphertext": None,
                "credential_metadata": json.dumps({}),
                "created_at": datetime.now(timezone.utc),
                "assigned_at": None,
                "started_at": None,
                "finished_at": None,
            },
        )
        assert _detalle_en(conn, BOT_B, JOB_C_ID) is not None
        assert _detalle_en(conn, BOT_C, JOB_C_ID) is None

        conn.execute(
            sa.text("UPDATE jobs SET bot = :bot WHERE id = :jid"),
            {"bot": BOT_C, "jid": JOB_C_ID},
        )

        assert _detalle_en(conn, BOT_B, JOB_C_ID) is None, "la fila quedó en la tabla vieja"
        movida = _detalle_en(conn, BOT_C, JOB_C_ID)
        assert movida is not None, "la fila no apareció en la tabla nueva"
        assert movida["bot"] == BOT_C
        assert movida["request_payload"] == {"security": {"nivel": "bajo"}}

    # el rollback no dejó rastro: ni catálogo, ni job, ni proyección
    assert pg.scalar("SELECT count(*) FROM bots WHERE code = :c", c=BOT_C) == 0
    assert pg.scalar("SELECT count(*) FROM bot_operations WHERE bot_code = :c", c=BOT_C) == 0
    assert pg.job(JOB_C_ID) is None
    assert pg.detalle(BOT_B, JOB_C_ID) is None
    assert pg.detalle(BOT_C, JOB_C_ID) is None


# --- 6. Ciclo downgrade/upgrade --------------------------------------------


def test_ciclo_downgrade_0014_y_upgrade_reconstruye_todo(pg: Pg) -> None:
    _alembic("downgrade", "0014")

    assert pg.version() == "0014"
    assert pg.tablas_fisicas() == set()
    assert pg.funciones() & set(FUNCIONES_0015) == set()
    assert pg.triggers_detalle() == set()
    # los datos canónicos siguen intactos tras el downgrade
    assert pg.job(JOB_A_ID) is not None
    assert pg.scalar(
        "SELECT count(*) FROM job_artifacts WHERE job_id = :jid", jid=JOB_A_ID
    ) == 2
    # las tablas físicas ya no existen: ``pg.detalle`` fallaría con UndefinedTable

    _alembic("upgrade", "0015")

    assert pg.version() == "0015"
    assert pg.tablas_fisicas() == {_tabla(codigo) for codigo in BOT_CODES}
    assert set(FUNCIONES_0015) <= pg.funciones()

    # re-backfill: exactamente los jobs vivos, con la misma redacción
    filas = sum(
        pg.scalar(f"SELECT count(*) FROM {_tabla(codigo)}") for codigo in BOT_CODES
    )
    assert filas == pg.scalar("SELECT count(*) FROM jobs")
    reaparecida = pg.detalle(BOT_A, JOB_A_ID)
    assert reaparecida is not None, "el upgrade no re-backfilleó el job canónico"
    assert reaparecida["request_payload"] == REQUEST_A_EN_DETALLE
    assert reaparecida["response_payload"] == RESPONSE_A_EN_DETALLE
    assert reaparecida["artifact_names"] == ["informe.pdf", "[REDACTED_URL]"]
    serializado = json.dumps(reaparecida, default=str)
    for marcador in MARCADORES_PROHIBIDOS:
        assert marcador not in serializado, f"fuga tras re-backfill: {marcador!r}"
