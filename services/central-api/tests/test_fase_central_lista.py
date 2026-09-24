"""Fase central lista para correr: sesión/token, presign, MP y guards PG.

Cubre las cuatro tareas del scope (plans/02-central-api, 04-billing):
UUID pleno sin correlativos (I-1/I-2), presign real SigV4 S3/MinIO con
fallback de desarrollo documentado, guards fail-closed de MercadoPago
sandbox y verificación de ``require_api_principal``/``require_worker``
contra PostgreSQL con fallback en memoria documentado.

Se ejecuta con ``pytest`` desde ``services/central-api``.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

AQUI = Path(__file__).resolve()
SRC = AQUI.parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.billing import mercadopago as mp  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.security import admin_sessions as sesiones  # noqa: E402
from central_api.security import worker_auth as wa  # noqa: E402
from central_api.security.sealed import SealedSectionError, encrypt_sealed_section  # noqa: E402
from central_api.settings import get_settings  # noqa: E402
from central_api.storage import firmar_subida, presign_put_url  # noqa: E402
from central_api.store import new_job_id  # noqa: E402


@pytest.fixture
def entorno_limpio(monkeypatch):
    """Limpia variables de entorno y la caché de settings por test."""
    for var in (
        "API_KEY_HMAC_SECRET", "DATABASE_URL", "MP_ENVIRONMENT",
        "MP_ACCESS_TOKEN", "MP_WEBHOOK_SECRET", "OBJECT_STORAGE_ENDPOINT",
        "OBJECT_STORAGE_REGION", "OBJECT_STORAGE_BUCKET",
        "OBJECT_STORAGE_ACCESS_KEY", "OBJECT_STORAGE_SECRET_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_secret_files_cargan_campos_con_alias(monkeypatch, tmp_path, entorno_limpio):
    """Los secretos ``*_FILE`` llenan campos declarados con alias Pydantic."""
    rsa = tmp_path / "rsa_private_key.pem"
    hmac = tmp_path / "api_key_hmac_secret.txt"
    rsa.write_text("rsa-test-value", encoding="utf-8")
    hmac.write_text("hmac-test-value", encoding="utf-8")
    monkeypatch.setenv("RSA_PRIVATE_KEY_FILE", str(rsa))
    monkeypatch.setenv("API_KEY_HMAC_SECRET_FILE", str(hmac))
    get_settings.cache_clear()

    settings = get_settings()

    assert settings.rsa_private_key == "rsa-test-value"
    assert settings.api_key_hmac_secret == "hmac-test-value"


def test_job_id_es_uuid7_sin_correlativos():
    """Los jobs en memoria usan UUIDv7 (plan 02 §3.3, I-1/I-2)."""
    vistos = {new_job_id() for _ in range(5)}
    assert len(vistos) == 5
    for crudo in vistos:
        assert uuid.UUID(crudo).version == 7


def test_protocol_version_entero_1():
    """El protocolo central-worker es entero 1 en ambos bordes."""
    from central_api.internal.workers import PROTOCOL_VERSION as interno
    from central_api.models.base import PROTOCOL_VERSION as base

    assert wa.PROTOCOL_VERSION == 1
    assert isinstance(wa.PROTOCOL_VERSION, int)
    assert interno == 1 and isinstance(interno, int)
    assert base == 1 and isinstance(base, int)
    assert get_settings().worker_protocol_version == 1


def test_tope_w2_cinco():
    """W-2: tope duro de 5 ejecuciones en central y base."""
    from central_api.internal.workers import MAX_WORKER_CAPACITY
    from central_api.models.base import WORKER_CAPACITY_MAX

    assert MAX_WORKER_CAPACITY == 5
    assert WORKER_CAPACITY_MAX == 5
    assert get_settings().worker_capacity == 5


def test_storage_fake_por_defecto_documentado():
    """Sin storage configurado el ticket es de desarrollo (sin firma)."""
    firmado = firmar_subida(
        object_key="uploads/abc", content_type="text/plain", ttl_seconds=900,
    )
    assert firmado["modo"] == "desarrollo"
    assert "uploads/abc" in firmado["upload_url"]
    assert "X-Amz-Signature" not in firmado["upload_url"]


def test_storage_sigv4_real_minio():
    """Con MinIO configurado la URL es SigV4 real sin exponer el secreto."""
    ahora = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    url = presign_put_url(
        endpoint="http://127.0.0.1:9000", region="us-east-1", bucket="mrbot",
        access_key="AKID", secret_key="secreto-solo-central",
        object_key="jobs/123/0/salida.zip", expires_seconds=300,
        content_type="application/zip", ahora=ahora,
    )
    assert url.startswith("http://127.0.0.1:9000/mrbot/jobs/123/0/salida.zip?")
    assert "X-Amz-Algorithm=AWS4-HMAC-SHA256" in url
    assert "X-Amz-Expires=300" in url
    firma = url.split("X-Amz-Signature=")[1].split("&")[0]
    assert len(firma) == 64
    assert "secreto-solo-central" not in url
    # Determinista ante mismos insumos (firma verificable sin red).
    repetida = presign_put_url(
        endpoint="http://127.0.0.1:9000", region="us-east-1", bucket="mrbot",
        access_key="AKID", secret_key="secreto-solo-central",
        object_key="jobs/123/0/salida.zip", expires_seconds=300,
        content_type="application/zip", ahora=ahora,
    )
    assert repetida == url


def test_storage_rechaza_sin_configurar():
    """La firma real falla cerrada sin endpoint/bucket/credenciales."""
    with pytest.raises(ValueError):
        presign_put_url(
            endpoint="", region="us-east-1", bucket="mrbot",
            access_key="AKID", secret_key="s", object_key="jobs/a",
        )
    with pytest.raises(ValueError):
        presign_put_url(
            endpoint="http://127.0.0.1:9000", region="us-east-1",
            bucket="mrbot", access_key="AKID", secret_key="s",
            object_key="../escape",
        )


def test_mp_fake_sin_credenciales_no_cobra():
    """Sin token no hay cobro real: modo fake documentado."""
    assert mp.modo_operacion("sandbox", "") == mp.MODO_FAKE
    assert mp.modo_operacion("production", "") == mp.MODO_FAKE
    assert mp.validar_configuracion("sandbox", "", "") == mp.MODO_FAKE
    assert "sin MP_ACCESS_TOKEN" in mp.nota_modo(mp.MODO_FAKE)
    assert "mercadopago.example" in mp.url_checkout("mrbot-abc", mp.MODO_FAKE)


def test_mp_produccion_falla_cerrada():
    """Producción exige token real y webhook secret (fail-closed)."""
    with pytest.raises(mp.ConfigMercadoPagoError):
        mp.validar_configuracion("production", "TEST-123", "secreto")
    with pytest.raises(mp.ConfigMercadoPagoError):
        mp.validar_configuracion("production", "APP-US-xxxx", "")
    assert (
        mp.validar_configuracion("production", "APP-US-xxxx", "secreto")
        == mp.MODO_PRODUCCION
    )
    assert (
        mp.validar_configuracion("sandbox", "TEST-123", "") == mp.MODO_SANDBOX
    )


def test_mp_coincide_entorno_y_urls():
    """El guard live_mode/entorno y las URLs separan sandbox y prod."""
    assert mp.coincide_entorno(False, "sandbox")
    assert mp.coincide_entorno(True, "production")
    assert not mp.coincide_entorno(True, "sandbox")
    assert not mp.coincide_entorno(False, "production")
    assert "sandbox.mercadopago.com" in mp.url_checkout("r", mp.MODO_SANDBOX)
    assert "www.mercadopago.com" in mp.url_checkout("r", mp.MODO_PRODUCCION)


def test_sesion_admin_token_opaco_y_uuid7():
    """Sesión del panel: token opaco, solo hash persistido, PK UUIDv7."""
    from central_api.models.base import new_uuid7
    from central_api.repositories.admin_sessions import AdminSessionRepository  # noqa: F401

    token = sesiones.mint_session_token()
    assert sesiones.verificar_token(token, sesiones.hash_session_token(token))
    assert not sesiones.verificar_token("otro", sesiones.hash_session_token(token))
    assert sesiones.hash_session_token(token) != token
    assert uuid.UUID(str(new_uuid7())).version == 7
    assert sesiones.parametros_cookie()["httponly"] is True
    assert sesiones.parametros_cookie()["samesite"] == "lax"


def test_worker_token_v2_ligado_a_uuid():
    """Tokens v1 (nodo) y v2 (UUID pleno) conviven; otro UUID no pasa."""
    nodo = "10.0.0.11:8080"
    otro_nodo = "10.0.0.12:8080"
    wid = uuid.uuid4()
    clave = "clave-firma-test"
    assert wa.check_service_token(clave, wa.mint_service_token(clave, nodo), nodo)
    assert not wa.check_service_token(
        clave, wa.mint_service_token(clave, nodo), otro_nodo
    )
    assert wa.check_worker_token(clave, wa.mint_worker_token(clave, wid), wid)
    assert not wa.check_worker_token(
        clave, wa.mint_worker_token(clave, wid), uuid.uuid4()
    )
    assert wa.check_any_token(clave, wa.mint_worker_token(clave, wid), nodo, wid)


def test_sobre_sellado_falla_cerrado():
    """Lo sensible viaja sellado: clave inválida no sigue en claro."""
    with pytest.raises(SealedSectionError):
        encrypt_sealed_section("no-es-una-pem", {"clave": "secreta"})


def test_auth_dev_sin_secreto(entorno_limpio):
    """Sin secreto configurado el catálogo abre en modo desarrollo."""
    cliente = TestClient(create_app(), raise_server_exceptions=True)
    respuesta = cliente.get("/api/v3/bots")
    assert respuesta.status_code == 200


def test_auth_con_secreto_falla_cerrada_sin_base(entorno_limpio, monkeypatch):
    """Con secreto pero sin PG no se puede verificar: 401 fail-closed."""
    monkeypatch.setenv("API_KEY_HMAC_SECRET", "secreto-servidor-test")
    get_settings.cache_clear()
    cliente = TestClient(create_app(), raise_server_exceptions=True)
    assert cliente.get("/api/v3/bots").status_code == 401
    malformada = cliente.get(
        "/api/v3/bots", headers={"X-API-Key": "no-valida"}
    )
    assert malformada.status_code == 401
    assert malformada.json()["detail"]["error_code"] == "authentication"


def test_checkout_modo_fake_http(entorno_limpio):
    """El checkout en modo fake no cobra y lo documenta en la respuesta."""
    cliente = TestClient(create_app(), raise_server_exceptions=True)
    respuesta = cliente.post(
        "/api/v3/billing/credit-checkouts",
        json={"credit_product_id": "pack-10"},
        headers={"Idempotency-Key": "clave-test-fake-1"},
    )
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["modo"] == "fake"
    assert "mercadopago.example" in cuerpo["checkout_url"]


def test_latido_reincorpora_nodo_inventariado(entorno_limpio, monkeypatch):
    """Un latido sin entrada en memoria reincorpora el nodo inventariado.

    Cubre el reinicio de la central con workers latiendo: en vez de 404,
    el nodo de WORKER_NODES vuelve a la caché y el latido es 200.
    """
    from central_api.store import WORKERS

    nodo = "192.0.2.99:8080"
    monkeypatch.setenv("WORKER_NODES", nodo)
    get_settings.cache_clear()
    WORKERS.pop(nodo, None)
    try:
        cliente = TestClient(create_app(), raise_server_exceptions=True)
        latido = cliente.post(
            f"/internal/v1/workers/{nodo}/heartbeat",
            json={"status": "SANO", "capacity": 5},
        )
        assert latido.status_code == 200
        assert latido.json()["node"] == nodo
        assert nodo in WORKERS
    finally:
        WORKERS.pop(nodo, None)


def test_latido_nodo_ajeno_sigue_404(entorno_limpio, monkeypatch):
    """Un latido de un nodo no inventariado sigue siendo 404."""
    from central_api.store import WORKERS

    monkeypatch.setenv("WORKER_NODES", "192.0.2.99:8080")
    get_settings.cache_clear()
    ajeno = "198.51.100.7:8080"
    WORKERS.pop(ajeno, None)
    try:
        cliente = TestClient(create_app(), raise_server_exceptions=True)
        latido = cliente.post(
            f"/internal/v1/workers/{ajeno}/heartbeat",
            json={"status": "SANO", "capacity": 5},
        )
        assert latido.status_code == 404
    finally:
        WORKERS.pop(ajeno, None)


def test_dispatcher_firma_sobre_sin_bearer(entorno_limpio, monkeypatch):
    """La central autoriza ejecución con firma Ed25519, sin Bearer compartido.

    El sobre lleva sección sellada + firma verificable por el worker; no
    viaja cabecera compartida (el worker no tiene secretos pre-compartidos).
    """
    from bot_worker.runtime.sealed import generate_sealed_keypair
    from bot_worker.security.assignments import load_verify_key, verify_assignment
    from central_api.scheduler.dispatcher import build_envelope
    from central_api.security.assignments import (
        ASSIGNMENT_SCOPE_ASSIGN,
        process_signing_key,
        public_pem,
    )
    from central_api.store import Job, WorkerEntry

    privada_w, publica_w = generate_sealed_keypair()
    trabajo = Job(
        id=new_job_id(),
        bot="consulta_cuit",
        operation="consulta",
        payload={},
        credentials={"clave": "ficticia"},
    )
    obrero = WorkerEntry(node="192.0.2.99:8080", sealed_pubkey_pem=publica_w)
    sobre = build_envelope(trabajo, obrero, "tok-ficticio")
    assert sobre["sealed"] is True
    assert sobre["credentials"] is None
    assert sobre["assignment_signature"]
    privada_c, _ = process_signing_key("")
    verify_assignment(
        load_verify_key(public_pem(privada_c)),
        scope=ASSIGNMENT_SCOPE_ASSIGN,
        job_id=trabajo.id,
        attempt=trabajo.assignment_attempt,
        lease_id="",
        expires_at=sobre["assignment_expires_at"],
        sealed_section=sobre["sealed_section"],
        signature_b64=sobre["assignment_signature"],
    )


def test_migrate_encuentra_alembic_del_checkout():
    """El job de migraciones localiza env.py + versions en el checkout."""
    from central_api.migrate import _dir_alembic

    directorio = _dir_alembic()
    assert (directorio / "env.py").is_file()
    assert (directorio / "versions").is_dir()


def test_resultado_estilo_worker_completa_con_datos(entorno_limpio):
    """El reporte con forma del worker completa y conserva los datos."""
    from central_api.api.dependencies import current_user_id, ensure_meta
    from central_api.store import JOBS, WORKERS, Job

    cliente = TestClient(create_app(), raise_server_exceptions=True)
    nodo = "192.0.2.24:8080"
    trabajo_id = str(uuid.uuid4())
    try:
        registro = cliente.post(
            "/internal/v1/workers/register",
            json={"advertised_url": f"http://{nodo}"},
        )
        assert registro.status_code == 200
        JOBS[trabajo_id] = Job(
            id=trabajo_id, bot="consulta_cuit", operation="consulta",
            payload={}, status="ASIGNADO", worker_node=nodo,
            assignment_attempt=1,
        )
        ensure_meta(trabajo_id, current_user_id())
        iniciado = cliente.post(
            f"/internal/v1/jobs/{trabajo_id}/events",
            headers={"X-Worker-Node": nodo},
            json={
                "event_id": f"evt-{trabajo_id}-1-started",
                "event_type": "started",
                "assignment_attempt": 1,
            },
        )
        assert iniciado.json()["status"] == "CORRIENDO"
        resultado = cliente.post(
            f"/internal/v1/jobs/{trabajo_id}/result",
            headers={"X-Worker-Node": nodo},
            json={
                "result": "OK",
                "data": {"cuit": "20123456786"},
                "assignment_attempt": 1,
            },
        )
        assert resultado.json()["status"] == "COMPLETO"
        visto = cliente.get(f"/api/v3/jobs/{trabajo_id}").json()
        assert visto["status"] == "COMPLETO"
        assert visto["data"] == {
            "schema_version": 1, "cuit": "20123456786",
        }
    finally:
        JOBS.pop(trabajo_id, None)
        WORKERS.pop(nodo, None)


def test_sobre_con_lease_pasa_admision_del_worker():
    """El sobre con lease pasa la admisión real del worker (sin 422)."""
    import uuid
    from datetime import datetime, timedelta, timezone

    from bot_worker.main import JobEnvelope, envelope_errors
    from central_api.scheduler.dispatcher import build_envelope, worker_operation
    from central_api.store import Job, WorkerEntry

    assert worker_operation("consulta_cuit", "consulta") == "consultar"
    assert worker_operation("desconocido", "x") == "x"
    trabajo = Job(
        id=str(uuid.uuid4()),
        bot="consulta_cuit",
        operation="consulta",
        payload={"cuit": "20123456786"},
        assignment_attempt=1,
    )
    expira = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
    sobre = build_envelope(
        trabajo, WorkerEntry(node="192.0.2.23:8080"),
        "tok-ficticio", str(uuid.uuid4()), expira,
    )
    assert sobre["plugin"] == "consulta_cuit"
    assert sobre["operation"] == "consultar"
    assert sobre["attempt"] == 1
    recibido = JobEnvelope(**sobre)
    assert envelope_errors(recibido) == []


def test_gate_worker_verifica_sealed_section_y_no_la_bandera():
    """La firma se liga al objeto sellado, no al booleano histórico ``sealed``."""
    from types import SimpleNamespace

    from bot_worker.main import JobEnvelope, verify_envelope_signature
    from bot_worker.runtime.sealed import generate_sealed_keypair
    from bot_worker.security.assignments import load_verify_key
    from central_api.scheduler.dispatcher import build_envelope
    from central_api.security.assignments import (
        ASSIGNMENT_SCOPE_ASSIGN,
        process_signing_key,
        public_pem,
    )
    from central_api.store import Job, WorkerEntry

    _, publica_worker = generate_sealed_keypair()
    trabajo = Job(
        id=str(uuid.uuid4()),
        bot="consulta_cuit",
        operation="consulta",
        payload={"cuit": "20123456786"},
        credentials={"clave": "ficticia"},
        assignment_attempt=1,
    )
    sobre = build_envelope(
        trabajo,
        WorkerEntry(node="192.0.2.23:8080", sealed_pubkey_pem=publica_worker),
        "tok-ficticio",
        str(uuid.uuid4()),
        (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
    )
    privada_central, _ = process_signing_key("")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                central_verify_key=load_verify_key(public_pem(privada_central)),
            )
        )
    )
    recibido = JobEnvelope(**sobre)
    assert verify_envelope_signature(
        request, recibido, scope=ASSIGNMENT_SCOPE_ASSIGN
    ) is None
