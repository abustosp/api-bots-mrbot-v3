"""Ciclo de vida real de un job hasta la tabla física por bot (PostgreSQL).

Prueba punta a punta contra una base PostgreSQL REAL (aislada) que el camino
completo de un job escribe la proyección ``bot_jobs_<bot>``:

1. creación por la capa HTTP real (``create_app`` + ``TestClient``) con
   ``DATABASE_URL`` y credenciales de cliente/worker reales;
2. claim durable (``scheduler.loop.claim_and_reserve`` -> ``ASSIGN_JOB_SQL``)
   con una fila de worker sana;
3. dispatch real (``dispatch_claimed`` -> POST HTTP al worker, aquí un stub
   HTTP que registra el sobre firmado);
4. callbacks del worker firmados/autenticados como en los tests existentes
   (``started`` y resultado terminal ``PARCIAL`` con un artifact);
5. verificación SQL de la fila física (request, response, artifacts) y de que
   el saneamiento deja ``[REDACTED_SECRET]``/``[REDACTED_URL]`` sin
   ``object_key`` ni URLs prefirmadas;
6. acuerdo con las tablas canónicas ``jobs``/``job_results``/``job_artifacts``;
7. replay idempotente (200 dedup) y segundo resultado divergente (409).

Se omite salvo que ``CENTRAL_API_TEST_DATABASE_URL`` apunte a una base de
prueba ya migrada (se espera el DSN en formato plano ``postgresql://...``, que
el código normaliza a ``postgresql+psycopg``). Ejemplo:

  CENTRAL_API_TEST_DATABASE_URL=postgresql://mrbot_test:***@127.0.0.1:32770/mrbot_test \
    ../../.venv/bin/python -m pytest tests/test_bot_tables_lifecycle_pg.py -s

La prueba crea y borra sus propias filas (usuario, api_key, worker, job); el
catálogo mínimo (``bots``/``bot_operations``) se siembra de forma idempotente.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

AQUI = Path(__file__).resolve()
SRC = AQUI.parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.main import create_app  # noqa: E402
from central_api.security.api_keys import (  # noqa: E402
    fingerprint_secret,
    new_key_id,
    new_secret,
)
from central_api.settings import get_settings  # noqa: E402
from central_api.store import JOBS, WORKERS  # noqa: E402

DSN_VAR = "CENTRAL_API_TEST_DATABASE_URL"
BOT = "consulta_cuit"
OPERATION = "consultar"
TABLE = f"bot_jobs_{BOT}"
HMAC_SECRET = "hmac-lifecycle-test"          # no es un secreto real
INTERNAL_SIGNING_KEY = "firma-interna-lifecycle-test"
CUIT = "20123456786"
#: Valor libre con forma de secreto: solo el saneamiento de la proyección lo
#: convierte en ``[REDACTED_SECRET]`` (``assert_no_secretos`` mira claves, no
#: valores).
REQUEST_NOTE = "usuario=prueba password=secreta123"
REQUEST_URL = "https://minio.invalid/mrbot/entrada.pdf?X-Amz-Signature=deadbeef"
RESPONSE_TOKEN = "token=abc123def456"
RESPONSE_URL = "https://minio.invalid/mrbot/jobs/x/salida.pdf?X-Amz-Signature=cafebabe"

pytestmark = pytest.mark.skipif(
    not os.environ.get(DSN_VAR),
    reason=f"{DSN_VAR} no configurada: prueba contra PostgreSQL real omitida",
)


class _WorkerStubHandler(BaseHTTPRequestHandler):
    """Worker HTTP mínimo: acepta la asignación y guarda el sobre firmado."""

    envelopes: list[dict] = []
    paths: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - API de BaseHTTPRequestHandler
        length = int(self.headers.get("content-length") or 0)
        cuerpo = self.rfile.read(length)
        type(self).paths.append(self.path)
        if self.path == "/internal/v1/jobs":
            type(self).envelopes.append(json.loads(cuerpo or b"{}"))
            self._responder(202, {"accepted": True, "job_id": None})
            return
        self._responder(404, {"detail": "ruta desconocida"})

    def _responder(self, codigo: int, payload: dict) -> None:
        datos = json.dumps(payload).encode("utf-8")
        self.send_response(codigo)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def log_message(self, *args: object) -> None:  # noqa: D102 - silencio
        return None


@pytest.fixture
def worker_stub():
    """Servidor HTTP efímero en 127.0.0.1 que actúa como worker."""
    _WorkerStubHandler.envelopes = []
    _WorkerStubHandler.paths = []
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _WorkerStubHandler)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        yield servidor.server_address[1]
    finally:
        servidor.shutdown()
        servidor.server_close()
        hilo.join(timeout=5)


def _truncar(valor: object, limite: int = 300) -> object:
    texto = json.dumps(valor, ensure_ascii=False, default=str, sort_keys=True)
    return texto if len(texto) <= limite else texto[:limite] + "…(truncado)"


def _limpiar_cache_orm() -> None:
    """Reinicia los motores cacheados para que tomen la DATABASE_URL del test."""
    from central_api import db as db_mod

    for cache in (db_mod._motor, db_mod._fabrica):
        try:
            cache.cache_clear()
        except Exception:  # noqa: BLE001 - sin caché previa
            pass


def test_ciclo_de_vida_completo_escribe_tabla_fisica_por_bot(
    monkeypatch, worker_stub
) -> None:
    dsn = os.environ[DSN_VAR]
    assert dsn.startswith(("postgresql://", "postgres://")), (
        "usar el DSN plano postgresql:// para ejercitar también la normalización"
    )
    node = f"127.0.0.1:{worker_stub}"
    api_key_id = new_key_id()
    api_secret = new_secret()
    user_id = uuid.uuid4()
    job_id: str | None = None
    worker_id: str | None = None

    monkeypatch.setenv("DATABASE_URL", dsn)
    monkeypatch.setenv("API_KEY_HMAC_SECRET", HMAC_SECRET)
    monkeypatch.setenv("INTERNAL_JWT_SIGNING_KEY", INTERNAL_SIGNING_KEY)
    monkeypatch.setenv("WORKER_NODES", node)
    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    for var in (
        "ADMIN_TOKEN", "ASSIGNMENT_SIGNING_KEY", "MP_ACCESS_TOKEN",
        "MP_WEBHOOK_SECRET", "OBJECT_STORAGE_ENDPOINT", "OBJECT_STORAGE_BUCKET",
        "OBJECT_STORAGE_ACCESS_KEY", "OBJECT_STORAGE_SECRET_KEY",
        "RSA_PRIVATE_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    _limpiar_cache_orm()

    conn = psycopg.connect(dsn, autocommit=True)
    try:
        # ---- 1. esquema migrado: 32 tablas físicas ------------------------
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables"
                " WHERE table_schema='public' AND table_name LIKE 'bot_jobs_%'"
            )
            total_tablas = cur.fetchone()[0]
            cur.execute("SELECT version_num FROM alembic_version")
            revision = cur.fetchone()[0]
        print(f"\n[1] tablas bot_jobs_*={total_tablas} revision_alembic={revision}")
        assert total_tablas == 32, f"se esperaban 32 tablas físicas, hay {total_tablas}"
        assert revision == "0015"

        # Catálogo mínimo (no hay siembra en las migraciones).
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (id, email, habilitado) VALUES (%s, %s, true)"
                " ON CONFLICT DO NOTHING",
                (user_id, f"lifecycle-{user_id}@example.invalid"),
            )
            cur.execute(
                "INSERT INTO bots (id, code, display_name) VALUES (%s, %s, %s)"
                " ON CONFLICT (code) DO NOTHING",
                (uuid.uuid4(), BOT, BOT),
            )
            cur.execute(
                "INSERT INTO bot_operations"
                " (id, bot_code, code, input_schema_version, effect_class)"
                " VALUES (%s, %s, %s, '1', 'CONSULTA')"
                " ON CONFLICT (bot_code, code) DO NOTHING",
                (uuid.uuid4(), BOT, OPERATION),
            )
            cur.execute(
                "INSERT INTO api_keys (id, user_id, key_prefix, verifier_hmac, scopes)"
                " VALUES (%s, %s, %s, %s, '[]'::jsonb)",
                (
                    uuid.uuid4(), user_id, api_key_id,
                    fingerprint_secret(HMAC_SECRET, api_secret),
                ),
            )

        with TestClient(create_app(), raise_server_exceptions=True) as client:
            # ---- registro de worker real (uuid + token de servicio) --------
            registro = client.post(
                "/internal/v1/workers/register",
                json={
                    "advertised_url": f"http://{node}",
                    "capacity": 5,
                    "build_version": "3.0.0-lifecycle-test",
                    "protocol_version": 1,
                },
            )
            assert registro.status_code == 200, registro.text
            reg = registro.json()
            worker_id = reg["worker_id"]
            assert reg["node"] == node and worker_id
            auth_worker = {
                "X-Worker-Node": node,
                "Authorization": f"Bearer {reg['service_token']}",
            }
            latido = client.post(
                f"/internal/v1/workers/{worker_id}/heartbeat",
                json={"status": "SANO", "capacity": 5, "en_ejecucion": 0},
            )
            assert latido.status_code == 200, latido.text
            assert latido.json()["status"] == "SANO"

            # ---- 2. creación por la capa API real -------------------------
            payload = {
                "cuit": CUIT,
                "notas": REQUEST_NOTE,
                "referencia_descarga": REQUEST_URL,
                "url_descarga": "pendiente-de-firma",
            }
            creado = client.post(
                f"/api/v3/bots/{BOT}/{OPERATION}",
                json={"payload": payload},
                headers={
                    "X-API-Key": f"mbk_{api_key_id}_{api_secret}",
                    "Idempotency-Key": f"lifecycle-{uuid.uuid4().hex}",
                },
            )
            assert creado.status_code == 202, creado.text
            job_id = creado.json()["job_id"]
            print(f"[2] POST /api/v3/bots/{BOT}/{OPERATION} -> {creado.status_code} job_id={job_id}")
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, attempts, worker_id FROM jobs WHERE id = %s",
                    (job_id,),
                )
                fila = cur.fetchone()
            assert fila == ("PENDIENTE", 0, None), fila
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, result, request_payload, response_payload,"
                    " artifact_names, artifact_metadata, assigned_at"
                    f" FROM {TABLE} WHERE job_id = %s",
                    (job_id,),
                )
                inicial = cur.fetchone()
            print(
                "[2b] proyección tras la admisión: status=%s result=%s request=%s"
                " response=%s artifact_names=%s artifact_metadata=%s assigned_at=%s"
                % (
                    inicial[0], inicial[1], _truncar(inicial[2], 160),
                    inicial[3], inicial[4], inicial[5], inicial[6],
                )
            )
            assert inicial[0] == "PENDIENTE" and inicial[1] is None
            assert inicial[3] is None and inicial[4] == [] and inicial[5] == []
            assert inicial[2]["cuit"] == CUIT
            assert inicial[2]["referencia_descarga"] == "[REDACTED_URL]"

            # ---- 3. claim durable ----------------------------------------
            from central_api.scheduler.loop import claim_and_reserve, dispatch_claimed

            job = JOBS[job_id]
            aselect, token, lease_id, expira = client.portal.call(
                claim_and_reserve, job
            )
            assert aselect is not None and aselect.node == node
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, worker_id::text, attempts, assigned_at IS NOT NULL,"
                    " lease_expires_at IS NOT NULL, app_version, protocol_version,"
                    " lease_expires_at"
                    " FROM jobs WHERE id = %s",
                    (job_id,),
                )
                claim_row = cur.fetchone()
            print(
                "[3] claim -> status=%s worker_id=%s attempts=%s assigned_at?=%s"
                " lease?=%s app_version=%s protocol=%s"
                % claim_row
            )
            assert claim_row[0] == "ASIGNADO"
            assert claim_row[1] == worker_id
            assert claim_row[2] == 1
            assert claim_row[3] is True and claim_row[4] is True
            assert claim_row[5] == "3.0.0-lifecycle-test"
            assert claim_row[6] == 1

            # ---- 4a. dispatch real por HTTP al worker ---------------------
            desenlace = client.portal.call(
                dispatch_claimed, job, aselect, token, lease_id, expira
            )
            assert desenlace == "ASIGNADO", desenlace
            assert _WorkerStubHandler.paths == ["/internal/v1/jobs"]
            sobre = _WorkerStubHandler.envelopes[-1]
            print(
                "[4a] dispatch HTTP real -> %s job_id=%s attempt=%s plugin=%s"
                " operation=%s firma=%s"
                % (
                    desenlace, sobre["job_id"], sobre["attempt"], sobre["plugin"],
                    sobre["operation"], bool(sobre["assignment_signature"]),
                )
            )
            assert sobre["job_id"] == job_id and sobre["attempt"] == 1
            assert sobre["operation"] == OPERATION and sobre["plugin"] == BOT
            assert sobre["sealed"] is False and sobre["credentials"] is None

            # ---- 4b. callbacks del worker (started + resultado PARCIAL) ---
            evt = client.post(
                f"/internal/v1/jobs/{job_id}/events",
                headers=auth_worker,
                json={
                    "event_id": f"evt-{job_id}-1-started",
                    "event_type": "started",
                    "assignment_attempt": 1,
                },
            )
            assert evt.status_code == 200, evt.text
            assert evt.json()["status"] == "CORRIENDO", evt.text

            # La renovación tiene que quedar escrita en la DB: el reaper en
            # PostgreSQL mira ``jobs.lease_expires_at`` y no la copia en
            # memoria, así que un evento válido debe extender la lease de
            # trabajo (60 s por defecto) por encima de la de asignación.
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT lease_expires_at FROM jobs WHERE id = %s", (job_id,)
                )
                lease_tras_started = cur.fetchone()[0]
            lease_claim = claim_row[7]
            print(
                "[4b] lease tras claim=%s | tras started=%s (+%ss)"
                % (
                    lease_claim,
                    lease_tras_started,
                    (
                        lease_tras_started - lease_claim
                        if lease_tras_started and lease_claim
                        else None
                    ),
                )
            )
            assert lease_tras_started is not None and lease_claim is not None
            assert lease_tras_started >= lease_claim + timedelta(seconds=30), (
                lease_claim,
                lease_tras_started,
            )

            art_key = f"jobs/{job_id}/1/salida.pdf"
            cuerpo_resultado = {
                "result": "PARCIAL",
                "data": {
                    "resumen": "consultado",
                    "detalle": RESPONSE_TOKEN,
                    "referencia_archivo": RESPONSE_URL,
                },
                "artifacts": [
                    {
                        "object_key": art_key,
                        "name": "salida.pdf",
                        "size_bytes": 1234,
                        "content_type": "application/pdf",
                        "sha256": "a" * 64,
                    }
                ],
                "assignment_attempt": 1,
                "event_id": f"res-{job_id}-1",
            }
            res = client.post(
                f"/internal/v1/jobs/{job_id}/result",
                headers=auth_worker,
                json=cuerpo_resultado,
            )
            assert res.status_code == 200, res.text
            print(f"[4b] callback resultado -> {res.status_code} {res.json()}")
            assert res.json() == {
                "success": True, "job_id": job_id, "status": "COMPLETO",
            }

            # ---- 5. fila física por bot ----------------------------------
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT job_id::text, user_id::text, worker_id::text, bot,"
                    " operation, status, result, attempts, request_payload,"
                    " response_payload, response_summary, response_received_at,"
                    " artifact_names, artifact_metadata, credential_metadata,"
                    " (started_at IS NOT NULL) AS tiene_started_at,"
                    " (finished_at IS NOT NULL) AS tiene_finished_at"
                    f" FROM {TABLE} WHERE job_id = %s",
                    (job_id,),
                )
                columnas = [d.name for d in cur.description]
                fila = dict(zip(columnas, cur.fetchone()))
            print("[5] fila física (truncada):")
            for clave, valor in fila.items():
                print(f"    {clave} = {_truncar(valor)}")
            assert fila["job_id"] == job_id and fila["user_id"] == str(user_id)
            assert fila["worker_id"] == worker_id
            assert fila["bot"] == BOT and fila["operation"] == OPERATION
            assert fila["status"] == "COMPLETO" and fila["result"] == "PARCIAL"
            assert fila["request_payload"] == {
                "cuit": CUIT,
                "notas": "usuario=prueba password=[REDACTED_SECRET]",
                "referencia_descarga": "[REDACTED_URL]",
            }, fila["request_payload"]
            assert fila["response_payload"] == {
                "resumen": "consultado",
                "detalle": "token=[REDACTED_SECRET]",
                "referencia_archivo": "[REDACTED_URL]",
            }, fila["response_payload"]
            assert fila["artifact_names"] == ["salida.pdf"]
            assert fila["artifact_metadata"] == [
                {
                    "kind": "ARCHIVO",
                    "filename": "salida.pdf",
                    "content_type": "application/pdf",
                    "size_bytes": 1234,
                    "sha256": "a" * 64,
                    "created_at": fila["artifact_metadata"][0]["created_at"],
                    "expires_at": None,
                }
            ]
            assert fila["response_summary"]["event_id"] == f"res-{job_id}-1"
            assert fila["response_received_at"] is not None
            assert fila["tiene_started_at"] is True
            assert fila["tiene_finished_at"] is True
            assert fila["credential_metadata"] == {"fields": [], "context": {}}

            # ni object_key ni URLs prefirmadas en TODA la proyección
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT to_jsonb(t)::text FROM {TABLE} AS t WHERE job_id = %s",
                    (job_id,),
                )
                proyeccion = cur.fetchone()[0]
            for prohibido in (art_key, "X-Amz-Signature", "http", "secreta123", RESPONSE_TOKEN):
                assert prohibido not in proyeccion, f"{prohibido!r} aparece en la proyección"
                assert f'"{prohibido}"' not in proyeccion
            assert "url_descarga" not in proyeccion  # clave con forma de URL: se descarta

            # ---- 6. acuerdo con las tablas canónicas ----------------------
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, result, attempts, worker_id::text,"
                    " request_payload->>'notas' FROM jobs WHERE id = %s",
                    (job_id,),
                )
                canon_job = cur.fetchone()
                cur.execute(
                    "SELECT attempt, result, payload, summary FROM job_results"
                    " WHERE job_id = %s",
                    (job_id,),
                )
                canon_res = cur.fetchone()
                cur.execute(
                    "SELECT kind, object_key, filename, size_bytes, sha256 FROM"
                    " job_artifacts WHERE job_id = %s",
                    (job_id,),
                )
                canon_art = cur.fetchall()
            print(
                "[6] canónico: jobs=%s | job_results=(attempt=%s result=%s payload=%s"
                " summary=%s) | job_artifacts=%s"
                % (
                    (canon_job[0], canon_job[1], canon_job[2], canon_job[3]),
                    canon_res[0], canon_res[1], _truncar(canon_res[2], 120),
                    _truncar(canon_res[3], 160),
                    [(a[0], a[2], a[3]) for a in canon_art],
                )
            )
            assert canon_job[0] == fila["status"] == "COMPLETO"
            assert canon_job[1] == fila["result"] == "PARCIAL"
            assert canon_job[2] == fila["attempts"] and canon_job[3] == worker_id
            assert canon_res[0] == 1 and canon_res[1] == "PARCIAL"
            assert canon_res[2] == cuerpo_resultado["data"]
            assert canon_res[2]["detalle"] == RESPONSE_TOKEN  # crudo en lo canónico
            assert canon_job[4] == REQUEST_NOTE  # crudo en lo canónico
            assert len(canon_art) == 1 and canon_art[0][1] == art_key

            # ---- 7. replay idempotente y divergencia ----------------------
            replay = client.post(
                f"/internal/v1/jobs/{job_id}/result",
                headers=auth_worker,
                json=cuerpo_resultado,
            )
            print(f"[7a] replay idéntico -> {replay.status_code} {replay.json()}")
            assert replay.status_code == 200, replay.text
            assert replay.json().get("dedup") is True
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM job_results WHERE job_id = %s", (job_id,))
                assert cur.fetchone()[0] == 1
                cur.execute(
                    "SELECT count(*) FROM job_artifacts WHERE job_id = %s", (job_id,)
                )
                assert cur.fetchone()[0] == 1

            divergente = dict(cuerpo_resultado)
            divergente["data"] = {"resumen": "otro"}
            conflicto = client.post(
                f"/internal/v1/jobs/{job_id}/result",
                headers=auth_worker,
                json=divergente,
            )
            print(
                f"[7b] segundo resultado divergente -> {conflicto.status_code}"
                f" {conflicto.json()}"
            )
            assert conflicto.status_code == 409, conflicto.text
            assert conflicto.json()["detail"] == "resultado duplicado divergente"
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT result, response_payload FROM {TABLE} WHERE job_id = %s",
                    (job_id,),
                )
                assert cur.fetchone()[1] == fila["response_payload"]
    finally:
        if job_id is not None:
            JOBS.pop(job_id, None)
        WORKERS.pop(node, None)
        get_settings.cache_clear()
        _limpiar_cache_orm()
        with conn.cursor() as cur:
            if job_id is not None:
                cur.execute("DELETE FROM jobs WHERE id = %s", (job_id,))
                # La proyección física se va por FK CASCADE: la evidencia se
                # imprime arriba, no se conserva estado del test.
                cur.execute(
                    "SELECT count(*) FROM information_schema.tables"
                    " WHERE table_schema='public' AND table_name = %s",
                    (TABLE,),
                )
                assert cur.fetchone()[0] == 1
            cur.execute("DELETE FROM api_keys WHERE key_prefix = %s", (api_key_id,))
            cur.execute("DELETE FROM workers WHERE endpoint = %s", (node,))
            cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.close()
