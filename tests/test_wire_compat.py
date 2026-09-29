"""Guarda de compatibilidad del wire central <-> worker <-> contratos.

Verifica que puertos, rutas, modelos de sobre, estados y versión de
protocolo coinciden en ambos lados y falla si divergen. Solo lee
implementación; no la modifica. Usa TestClient real donde es posible y
comprobación estática del texto fuente donde el HTTP no llega.

Convenciones del guard:
- ``protocol_version`` es entero y vale 1 en el wire.
- Tope duro de capacidad 5 (W-2) en central, worker y contratos.
- Lo sensible viaja sellado; el worker no toca base de datos (W-1).
- Nodos de prueba en TEST-NET-1 (192.0.2.0/24) y secretos ficticios.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent
CENTRAL_MAIN = RAIZ / "services/central-api/src/central_api/main.py"
WORKER_MAIN = RAIZ / "services/bot-worker/src/bot_worker/main.py"
COMPOSE = RAIZ / "infra/compose/docker-compose.yml"

RUTAS_CENTRALES_ESPERADAS = (
    "/internal/v1/workers/register",
    "/internal/v1/workers/{worker_id}/heartbeat",
    "/internal/v1/jobs/{job_id}/events",
    "/internal/v1/jobs/{job_id}/result",
    "/internal/v1/jobs/{job_id}/artifacts/presign",
    "/internal/v1/jobs/{job_id}/cancel-ack",
)
RUTAS_WORKER_ESPERADAS = (
    "/internal/v1/health",
    "/internal/v1/jobs",
    "/internal/v1/jobs/{job_id}/cancel",
    "/internal/v1/status",
    "/internal/v1/bots",
)
CLAVES_SOBRE_SELLADO = ("alg", "enc_key_b64", "blob_b64")
ALG_ESPERADO = "RSA-OAEP-SHA256+Fernet"
ESTADOS_JOB_ESPERADOS = (
    "PENDIENTE",
    "ASIGNADO",
    "CORRIENDO",
    "COMPLETO",
    "FALLIDO",
    "CANCELADO",
)

_TOKEN_FICTICIO = "token-ficticio-guard-wire"
_CENTRAL_FICTICIA = "http://central-inaccesible.local"


def _cliente_central() -> TestClient:
    """Crea un cliente de prueba de la central sin servidor real."""
    from central_api.main import create_app

    return TestClient(create_app(), raise_server_exceptions=True)


def _llaves():
    """Par Ed25519 de prueba: la central firma, el worker verifica."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from bot_worker.security.assignments import load_verify_key
    from central_api.security.assignments import public_pem

    privada = Ed25519PrivateKey.generate()
    return privada, load_verify_key(public_pem(privada))


def _firmar(privada, sobre: dict) -> dict:
    """Firma un sobre de prueba como lo haría la central."""
    from central_api.security.assignments import (
        ASSIGNMENT_SCOPE_ASSIGN,
        default_expiry,
        sealed_hash_of,
        sign_assignment,
    )

    expira = default_expiry(300)
    sobre = dict(sobre)
    sobre["assignment_expires_at"] = expira
    sobre["assignment_signature"] = sign_assignment(
        privada,
        scope=ASSIGNMENT_SCOPE_ASSIGN,
        job_id=sobre["job_id"],
        attempt=sobre["attempt"],
        lease_id=sobre["lease_id"],
        expires_at=expira,
        sealed_hash=sealed_hash_of(sobre.get("sealed")),
    )
    return sobre


def _cliente_worker(clave_publica=None) -> TestClient:
    """Crea un cliente de prueba del worker sin ciclo de vida.

    Sin entorno ni secretos: la config es explícita y la clave de la
    central se fija en memoria como en el registro real.
    """
    from bot_worker.config import WorkerConfig
    from bot_worker.main import create_app

    ajustes = WorkerConfig(
        central_url=_CENTRAL_FICTICIA,
        advertised_url="http://127.0.0.1:8080",
    )
    app = create_app(ajustes)
    app.state.central_verify_key = clave_publica
    return TestClient(app, raise_server_exceptions=True)


def _rutas(app) -> set[str]:
    """Devuelve el conjunto de rutas reales, internas incluidas.

    No sirve ``app.openapi()``: las rutas internas se registran con
    ``include_in_schema=False`` y por eso no aparecen en el esquema. Tampoco
    sirve mirar ``app.routes`` sin más: desde la versión instalada de FastAPI
    ``include_router`` guarda un ``_IncludedRouter`` diferido que expone el
    router original junto al prefijo del include, así que el recorrido baja por
    ``original_router`` agregando ese prefijo.
    """
    salida: set[str] = set()

    def visitar(nodos, prefijo: str) -> None:
        for nodo in nodos:
            anidado = getattr(nodo, "original_router", None)
            if anidado is not None:
                contexto = getattr(nodo, "include_context", None)
                sub_prefijo = str(getattr(contexto, "prefix", "") or "")
                visitar(anidado.routes, prefijo + sub_prefijo)
                continue
            camino = getattr(nodo, "path", None)
            if isinstance(camino, str) and camino:
                salida.add(prefijo + camino)
                continue
            hijos = getattr(nodo, "routes", None)
            if hijos:
                visitar(hijos, prefijo)

    visitar(app.routes, "")
    salida |= set(app.openapi()["paths"])
    return salida


def _bloque_worker_compose() -> str:
    """Extrae el bloque del servicio bot-worker del compose de desarrollo."""
    texto = COMPOSE.read_text(encoding="utf-8")
    inicio = texto.index("\n  bot-worker:")
    fin = texto.index("\n  minio:", inicio)
    return texto[inicio:fin]


def _uuid_v7_ejemplo() -> str:
    """Devuelve un UUIDv7 fijo válido para los modelos de contratos."""
    return "0190d5a1-6c9e-7a1b-8c2d-3e4f5a6b7c8d"


# ------------------------------------------------------------- puertos ---


def test_puerto_central_8000_en_ajustes_imagen_y_compose():
    """La central escucha en 8000 en ajustes, Dockerfile y compose."""
    from central_api.settings import get_settings

    assert get_settings().central_api_port == 8000
    docker = (RAIZ / "services/central-api/Dockerfile").read_text(encoding="utf-8")
    assert "EXPOSE 8000" in docker
    assert '"--port", "8000"' in docker
    compose = COMPOSE.read_text(encoding="utf-8")
    assert "127.0.0.1:8000:8000" in compose
    assert "--central-url" in compose and "http://central-api:8000" in compose


def test_puerto_worker_8080_sin_publicar_al_host():
    """El worker expone 8080 solo interno (W-3) en imagen y compose."""
    docker = (RAIZ / "services/bot-worker/Dockerfile").read_text(encoding="utf-8")
    assert "EXPOSE 8080" in docker
    assert "--concurrency" in COMPOSE.read_text(encoding="utf-8")
    from bot_worker.config import WorkerConfig

    assert WorkerConfig.__dataclass_fields__["port"].default == 8080
    bloque = _bloque_worker_compose()
    assert '"8080"' in bloque
    assert "\n    ports:" not in bloque


# -------------------------------------------------------------- rutas ---


def test_prefijo_interno_v1_en_central():
    """La central monta el borde interno bajo /internal/v1."""
    texto = CENTRAL_MAIN.read_text(encoding="utf-8")
    assert 'include_router(internal_router, prefix="/internal/v1")' in texto


def test_rutas_internas_de_central_existen():
    """Las rutas internas que el worker consume están declaradas."""
    cliente = _cliente_central()
    rutas = _rutas(cliente.app)
    for esperada in RUTAS_CENTRALES_ESPERADAS:
        assert esperada in rutas, f"falta ruta central {esperada}"


def test_rutas_internas_de_worker_existen():
    """Las rutas del worker que la central consume están declaradas."""
    cliente = _cliente_worker()
    rutas = _rutas(cliente.app)
    for esperada in RUTAS_WORKER_ESPERADAS:
        assert esperada in rutas, f"falta ruta worker {esperada}"
    operaciones = cliente.app.openapi()["paths"]["/internal/v1/jobs"]
    assert "get" in operaciones
    assert "post" in operaciones


def test_dispatcher_apunta_a_rutas_reales_del_worker():
    """El despachador de la central usa los paths que el worker sirve."""
    from central_api.scheduler import dispatcher

    rutas_worker = _rutas(_cliente_worker().app)
    assert dispatcher.ASSIGN_PATH == "/internal/v1/jobs"
    assert dispatcher.STATUS_PATH == "/internal/v1/status"
    assert dispatcher.ASSIGN_PATH in rutas_worker
    assert dispatcher.STATUS_PATH in rutas_worker


def test_callbacks_del_worker_apuntan_a_rutas_reales_de_central():
    """Presign, cancel-ack, eventos, resultado, latido y registro coinciden."""
    rutas_central = _rutas(_cliente_central().app)
    assert "/internal/v1/jobs/{job_id}/artifacts/presign" in rutas_central
    assert "/internal/v1/jobs/{job_id}/cancel-ack" in rutas_central
    assert "/internal/v1/jobs/{job_id}/events" in rutas_central
    assert "/internal/v1/jobs/{job_id}/result" in rutas_central
    assert "/internal/v1/workers/{worker_id}/heartbeat" in rutas_central
    assert "/internal/v1/workers/register" in rutas_central
    reporting = RAIZ / "services/bot-worker/src/bot_worker/reporting"
    central_py = (reporting / "central.py").read_text(encoding="utf-8")
    assert "/artifacts/presign" in central_py
    assert "/cancel-ack" in central_py
    latido = (reporting / "heartbeat.py").read_text(encoding="utf-8")
    assert "/workers/" in latido and "/heartbeat" in latido
    resultados = (reporting / "results.py").read_text(encoding="utf-8")
    assert "/internal/v1/jobs/" in resultados and "/result" in resultados
    worker_src = WORKER_MAIN.read_text(encoding="utf-8")
    assert "/internal/v1/workers/register" in worker_src


def test_registro_central_acepta_campos_que_envia_el_worker():
    """El cuerpo de registro acepta cada campo que el worker publica."""
    from central_api.internal.workers import RegisterBody

    for campo in (
        "advertised_url",
        "capacity",
        "capabilities",
        "build_version",
        "protocol_version",
        "instance_nonce",
        "sealed_pubkey_pem",
    ):
        assert campo in RegisterBody.model_fields, f"falta {campo}"


def test_presign_cuerpo_y_respuesta_coinciden_en_ambos_lados():
    """El cuerpo presign del worker llena PresignBody y lee upload_url."""
    from central_api.internal.uploads import PresignBody

    assert set(PresignBody.model_fields) == {
        "artifact_id",
        "content_type",
        "size_bytes",
    }
    central = (
        RAIZ / "services/central-api/src/central_api/internal/uploads.py"
    ).read_text(encoding="utf-8")
    for clave in (
        "upload_id",
        "object_key",
        "upload_url",
        "expires_at",
        "required_headers",
    ):
        assert f'"{clave}"' in central, f"falta {clave} en presign"
    worker = (
        RAIZ / "services/bot-worker/src/bot_worker/reporting/central.py"
    ).read_text(encoding="utf-8")
    assert "upload_url" in worker


# ------------------------------------------------------------ protocolo ---


def test_protocolo_normativo_entero_1_y_compatible():
    """El contrato normativo es el entero 1 y solo acepta el 1."""
    from mrbot_contracts.version import PROTOCOL_VERSION, is_compatible

    assert PROTOCOL_VERSION == 1
    assert isinstance(PROTOCOL_VERSION, int)
    assert is_compatible(1) is True
    assert is_compatible(0) is False
    assert is_compatible(2) is False


def test_mensaje_base_versiona_por_defecto_y_rechaza_extras():
    """Todo mensaje top-level lleva protocol_version 1 y es estricto."""
    import pytest as _pytest

    from mrbot_contracts.version import ProtocolMessage

    assert ProtocolMessage.model_fields["protocol_version"].default == 1
    with _pytest.raises(Exception):
        ProtocolMessage(protocol_version=1, campo_inesperado="x")


def test_espejos_de_protocolo_valen_1_en_ambos_lados():
    """Cada espejo de PROTOCOL_VERSION coincide con el contrato."""
    import ast as _ast

    from central_api.internal import workers as w
    from central_api.scheduler import loop as scheduler_loop
    from central_api.security import worker_auth
    from central_api.settings import get_settings

    assert w.PROTOCOL_VERSION == 1
    assert isinstance(w.PROTOCOL_VERSION, int)
    assert scheduler_loop.PROTOCOL_VERSION == 1
    assert worker_auth.PROTOCOL_VERSION == 1
    ajustes = get_settings()
    assert ajustes.worker_protocol_version == 1
    assert isinstance(ajustes.worker_protocol_version, int)
    for ruta in (
        RAIZ / "services/bot-worker/src/bot_worker/config.py",
        RAIZ / "services/bot-worker/src/bot_worker/bots/registry.py",
    ):
        arbol = _ast.parse(ruta.read_text(encoding="utf-8"))
        valores = {}
        for n in _ast.walk(arbol):
            if isinstance(n, _ast.Assign) and len(n.targets) == 1:
                objetivo = n.targets[0]
                if getattr(objetivo, "id", "") == "PROTOCOL_VERSION":
                    valores[objetivo.id] = _ast.literal_eval(n.value)
            elif isinstance(n, _ast.AnnAssign):
                if getattr(n.target, "id", "") == "PROTOCOL_VERSION":
                    valores[n.target.id] = _ast.literal_eval(n.value)
        assert valores.get("PROTOCOL_VERSION") == 1, f"diverge {ruta.name}"


def test_registro_vivo_negocia_protocolo_1():
    """El registro acepta protocolo 1 y rechaza el 2 con bandera visible."""
    from central_api.store import WORKERS

    cliente = _cliente_central()
    nodo_ok = "192.0.2.11:8080"
    nodo_mal = "192.0.2.12:8080"
    try:
        ok = cliente.post(
            "/internal/v1/workers/register",
            json={"advertised_url": f"http://{nodo_ok}"},
        )
        assert ok.status_code == 200
        cuerpo = ok.json()
        assert cuerpo["accepted"] is True
        assert cuerpo["protocol_version"] == 1
        assert isinstance(cuerpo["protocol_version"], int)
        mal = cliente.post(
            "/internal/v1/workers/register",
            json={
                "advertised_url": f"http://{nodo_mal}",
                "protocol_version": 2,
            },
        )
        assert mal.status_code == 200
        cuerpo_mal = mal.json()
        assert cuerpo_mal["accepted"] is False
        assert cuerpo_mal["protocol_version"] == 1
    finally:
        WORKERS.pop(nodo_ok, None)
        WORKERS.pop(nodo_mal, None)


def test_latido_vivo_tras_registro():
    """El latido del worker llega a la ruta que la central declara."""
    from central_api.store import WORKERS

    cliente = _cliente_central()
    nodo = "192.0.2.13:8080"
    try:
        registro = cliente.post(
            "/internal/v1/workers/register",
            json={"advertised_url": f"http://{nodo}"},
        )
        assert registro.status_code == 200
        latido = cliente.post(
            f"/internal/v1/workers/{nodo}/heartbeat",
            json={"status": "SANO", "capacity": 5},
        )
        assert latido.status_code == 200
        assert latido.json()["node"] == nodo
    finally:
        WORKERS.pop(nodo, None)


def test_worker_vivo_reporta_protocolo_1_y_tope_5():
    """El estado y el inventario vivos declaran protocolo 1 entero."""
    cliente = _cliente_worker()
    estado = cliente.get("/internal/v1/status")
    assert estado.status_code == 200
    cuerpo = estado.json()
    assert cuerpo["protocol_version"] == 1
    assert isinstance(cuerpo["protocol_version"], int)
    assert cuerpo["capacity"] <= 5
    bots = cliente.get("/internal/v1/bots")
    assert bots.status_code == 200
    assert bots.json()["protocol_version"] == 1


def test_worker_rechaza_sobre_con_protocolo_distinto():
    """Un sobre con protocolo 2 responde 422 de protocolo incompatible."""
    _priv, _pub = _llaves()
    cliente = _cliente_worker(_pub)
    respuesta = cliente.post(
        "/internal/v1/jobs",
        json=_firmar(
            _priv,
            {
                "protocol_version": 2,
                "job_id": str(uuid.uuid4()),
                "attempt": 1,
                "lease_id": str(uuid.uuid4()),
                "lease_expires_at": "2099-01-01T00:00:00Z",
                "plugin": "inexistente",
                "operation": "consulta",
            },
        ),
    )
    assert respuesta.status_code == 422
    cuerpo = respuesta.json()
    assert cuerpo["code"] == "SOBRE_INVALIDO"
    assert "protocol_version incompatible" in cuerpo["message"]


def test_worker_rechaza_sobre_sellado_invalido_en_cerrado():
    """Un sellado ilegible responde 422 sin exponer su contenido."""
    _priv, _pub = _llaves()
    cliente = _cliente_worker(_pub)
    respuesta = cliente.post(
        "/internal/v1/jobs",
        json=_firmar(
            _priv,
            {
                "protocol_version": 1,
                "job_id": str(uuid.uuid4()),
                "attempt": 1,
                "lease_id": str(uuid.uuid4()),
                "lease_expires_at": "2099-01-01T00:00:00Z",
                "plugin": "inexistente",
                "operation": "consulta",
                "sealed": {"alg": "otro"},
            },
        ),
    )
    assert respuesta.status_code == 422
    assert respuesta.json()["code"] == "SOBRE_SELLADO_INVALIDO"


# ---------------------------------------------------------------- sobre ---


def test_sobre_sellado_misma_forma_en_contratos_central_y_worker():
    """La sección sellada tiene las mismas claves y algoritmo en el wire."""
    from central_api.security.sealed import SEALED_ALG
    from mrbot_contracts.jobs import SealedSection

    assert SEALED_ALG == ALG_ESPERADO
    assert set(SealedSection.model_fields) == set(CLAVES_SOBRE_SELLADO)
    assert SealedSection.model_fields["alg"].default == ALG_ESPERADO
    worker = (
        RAIZ / "services/bot-worker/src/bot_worker/runtime/sealed.py"
    ).read_text(encoding="utf-8")
    assert ALG_ESPERADO in worker


def test_sobre_sellado_roundtrip_central_a_worker():
    """Lo que cifra la central lo abre el worker con su privada efímera."""
    from bot_worker.runtime import sealed as sellado_worker
    from central_api.security.sealed import encrypt_sealed_section

    privada, publica = sellado_worker.generate_sealed_keypair()
    seccion = {"credentials": {"cuit": "20-12345678-9"}}
    sobre = encrypt_sealed_section(publica, seccion)
    assert set(sobre) == set(CLAVES_SOBRE_SELLADO)
    assert sellado_worker.decrypt_sealed_section(privada, sobre) == seccion


def test_sobre_sellado_admite_cache_apoc_mayor_a_un_mib():
    """El cache APOC central (aprox. 1.7 MiB) debe caber en el sobre."""
    from bot_worker.runtime import sealed as sellado_worker
    from central_api.security.sealed import (
        MAX_SEALED_SECTION_BYTES,
        encrypt_sealed_section,
    )

    privada, publica = sellado_worker.generate_sealed_keypair()
    texto = "x" * 1_700_000
    sobre = encrypt_sealed_section(publica, {"service": {"apoc_base_text": texto}})
    assert MAX_SEALED_SECTION_BYTES >= 1_700_000
    assert sellado_worker.decrypt_sealed_section(privada, sobre) == {
        "service": {"apoc_base_text": texto}
    }


def test_sobre_sellado_rechaza_estrictamente_mas_de_cuatro_mib():
    """El aumento conserva un límite duro y falla cerrado al excederlo."""
    from central_api.security.sealed import (
        MAX_SEALED_SECTION_BYTES,
        SealedSectionError,
        encrypt_sealed_section,
    )
    from bot_worker.runtime.sealed import generate_sealed_keypair

    _, publica = generate_sealed_keypair()
    with pytest.raises(SealedSectionError, match="4 MiB"):
        encrypt_sealed_section(publica, {"value": "x" * MAX_SEALED_SECTION_BYTES})


def test_sobre_sellado_falla_cerrado_sin_fugar_secreto():
    """Un sobre corrupto falla sin exponer el secreto en el error."""
    from bot_worker.runtime import sealed as sellado_worker
    from central_api.security.sealed import encrypt_sealed_section

    privada, publica = sellado_worker.generate_sealed_keypair()
    sobre = encrypt_sealed_section(publica, {"credentials": {"clave": "ficticia"}})
    corrupto = dict(sobre, blob_b64="!!no-b64!!")
    with pytest.raises(sellado_worker.SealedEnvelopeError) as exc:
        sellado_worker.decrypt_sealed_section(privada, corrupto)
    assert "ficticia" not in str(exc.value)


def test_despacho_sella_lo_sensible_y_marca_bandera_visible():
    """Con pubkey lo sensible va sellado; sin pubkey y con credenciales, se rechaza."""
    from central_api.scheduler.dispatcher import build_envelope
    from central_api.store import Job, WorkerEntry

    from bot_worker.runtime.sealed import generate_sealed_keypair

    _, publica = generate_sealed_keypair()
    trabajo = Job(
        id=str(uuid.uuid4()),
        bot="consulta_cuit",
        operation="consulta",
        payload={},
        credentials={"clave": "ficticia"},
    )
    con_clave = WorkerEntry(node="192.0.2.21:8080", sealed_pubkey_pem=publica)
    sobre = build_envelope(trabajo, con_clave, "tok-ficticio")
    assert sobre["protocol_version"] == 1
    assert sobre["sealed"] is True
    assert set(sobre["sealed_section"]) == set(CLAVES_SOBRE_SELLADO)
    assert sobre["credentials"] is None

    # Sin clave pública del worker no se degrada a texto claro: el despacho
    # falla cerrado y el error no repite el secreto.
    sin_clave = WorkerEntry(node="192.0.2.22:8080")
    with pytest.raises(RuntimeError) as exc:
        build_envelope(trabajo, sin_clave, "tok-ficticio")
    assert "clave pública" in str(exc.value)
    assert "ficticia" not in str(exc.value)

    # Un job sin credenciales sí puede viajar abierto (la bandera lo declara).
    sin_secretos = Job(
        id=str(uuid.uuid4()),
        bot="consulta_cuit",
        operation="consulta",
        payload={"cuit": "20111111112"},
        credentials={},
    )
    abierto = build_envelope(sin_secretos, sin_clave, "tok-ficticio")
    assert abierto["sealed"] is False and abierto["sealed_section"] is None


def test_worker_abre_sobre_y_no_lo_reserializa():
    """El worker fusiona lo sellado en memoria y limpia la sección."""
    from bot_worker.main import JobEnvelope, unseal_envelope
    from bot_worker.runtime.sealed import generate_sealed_keypair
    from central_api.security.sealed import encrypt_sealed_section

    privada, publica = generate_sealed_keypair()
    sobre = encrypt_sealed_section(
        publica, {"credentials": {"cuit": "20-12345678-9"}}
    )
    sobre_recibido = JobEnvelope(protocol_version=1, sealed=sobre)
    abierto = unseal_envelope(sobre_recibido, privada)
    assert abierto.credentials == {"cuit": "20-12345678-9"}
    assert abierto.sealed is None


def test_modelos_de_sobre_comparten_nucleo_del_wire():
    """Sobre central, worker y contratos comparten núcleo protocolizado."""
    from mrbot_contracts.jobs import JobEnvelope as SobreContratos

    from bot_worker.main import JobEnvelope as SobreWorker

    assert SobreContratos.model_fields["protocol_version"].default == 1
    assert SobreContratos.model_fields["sealed"].is_required() is False
    for campo in ("protocol_version", "job_id", "sealed", "credentials", "payload"):
        assert campo in SobreWorker.model_fields, f"falta {campo} en worker"


# --------------------------------------------------------------- estados ---


def test_ciclo_de_vida_del_job_coincide_con_contratos():
    """Los seis estados lógicos viven en contratos y en la central."""
    from mrbot_contracts.enums import JobStatus

    assert [e.value for e in JobStatus] == list(ESTADOS_JOB_ESPERADOS)
    from central_api.store import Job

    assert Job(id="x", bot="b", operation="o", payload={}).status == "PENDIENTE"
    bucle = (RAIZ / "services/central-api/src/central_api/scheduler/loop.py").read_text(
        encoding="utf-8"
    )
    assert '"PENDIENTE"' in bucle and '"ASIGNADO"' in bucle
    eventos = (
        RAIZ / "services/central-api/src/central_api/internal/job_events.py"
    ).read_text(encoding="utf-8")
    assert '"CORRIENDO"' in eventos and '"CANCELADO"' in eventos
    resultados = (
        RAIZ / "services/central-api/src/central_api/internal/job_results.py"
    ).read_text(encoding="utf-8")
    assert '"COMPLETO"' in resultados and '"FALLIDO"' in resultados


def test_resultados_terminales_coherentes_en_contratos():
    """COMPLETO exige OK/PARCIAL, FALLIDO exige ERROR, CANCELADO va solo."""
    from mrbot_contracts.enums import JobResult, JobStatus
    from mrbot_contracts.jobs import JobResultReport

    from central_api.internal.job_results import TERMINAL_OK

    assert set(TERMINAL_OK) == {"OK", "PARCIAL"}
    assert {e.value for e in JobResult} == {"OK", "PARCIAL", "ERROR"}
    base = {
        "result_id": _uuid_v7_ejemplo(),
        "job_id": _uuid_v7_ejemplo(),
        "attempt": 1,
        "worker_id": _uuid_v7_ejemplo(),
        "finished_at": datetime.now(timezone.utc),
    }
    JobResultReport(**base, status=JobStatus.COMPLETO, result=JobResult.OK)
    JobResultReport(**base, status=JobStatus.CANCELADO)
    with pytest.raises(Exception):
        JobResultReport(**base, status=JobStatus.COMPLETO, result=JobResult.ERROR)
    with pytest.raises(Exception):
        JobResultReport(**base, status=JobStatus.CORRIENDO, result=JobResult.OK)


def test_vocabulario_de_drenaje_compartido():
    """DRENANDO existe en contratos y en el borde vivo del worker."""
    from mrbot_contracts.enums import WorkerStatus

    assert WorkerStatus.DRENANDO.value == "DRENANDO"
    cuerpo = _cliente_worker().get("/internal/v1/status")
    assert cuerpo.status_code == 200
    assert cuerpo.json()["state"] in ("SANO", "SATURADO", "DRENANDO")


def test_flujo_vivo_evento_resultado_y_acuse():
    """Evento, resultado y acuse recorren el ciclo ASIGNADO->COMPLETO."""
    from central_api.internal.job_events import SEEN_EVENTS
    from central_api.store import JOBS, WORKERS, Job

    cliente = _cliente_central()
    nodo = "192.0.2.14:8080"
    trabajo_id = str(uuid.uuid4())
    evento_id = f"evt-{uuid.uuid4().hex}"
    try:
        registro = cliente.post(
            "/internal/v1/workers/register",
            json={"advertised_url": f"http://{nodo}"},
        )
        assert registro.status_code == 200
        JOBS[trabajo_id] = Job(
            id=trabajo_id,
            bot="consulta_cuit",
            operation="consulta",
            payload={},
            status="ASIGNADO",
            worker_node=nodo,
            assignment_attempt=1,
        )
        cabecera = {"X-Worker-Node": nodo}
        evento = cliente.post(
            f"/internal/v1/jobs/{trabajo_id}/events",
            headers=cabecera,
            json={
                "event_id": evento_id,
                "event_type": "started",
                "assignment_attempt": 1,
            },
        )
        assert evento.status_code == 200
        assert evento.json()["status"] == "CORRIENDO"
        presign = cliente.post(
            f"/internal/v1/jobs/{trabajo_id}/artifacts/presign",
            headers=cabecera,
            json={
                "artifact_id": "a1",
                "content_type": "text/plain",
                "size_bytes": 10,
            },
        )
        assert presign.status_code == 201
        assert presign.json()["upload_url"]
        acuse = cliente.post(
            f"/internal/v1/jobs/{trabajo_id}/cancel-ack",
            headers=cabecera,
            json={"accepted": True},
        )
        assert acuse.status_code == 200
        assert acuse.json()["ok"] is True
        resultado = cliente.post(
            f"/internal/v1/jobs/{trabajo_id}/result",
            headers=cabecera,
            json={"result": "OK", "data": {}, "assignment_attempt": 1},
        )
        assert resultado.status_code == 200
        assert resultado.json()["status"] == "COMPLETO"
    finally:
        JOBS.pop(trabajo_id, None)
        WORKERS.pop(nodo, None)
        # El registro de idempotencia de eventos está indexado por
        # (job, intento, event_id) desde que los eventos se persisten de forma
        # duradera.
        SEEN_EVENTS.pop((trabajo_id, 1, evento_id), None)


# ----------------------------------------------------- topes y sellado ---


def test_tope_5_en_central_worker_y_contratos():
    """La capacidad máxima es 5 en los tres lados del wire."""
    from central_api.internal.workers import MAX_WORKER_CAPACITY

    assert MAX_WORKER_CAPACITY == 5
    ajustes = (RAIZ / "services/central-api/src/central_api/settings.py").read_text(
        encoding="utf-8"
    )
    assert "worker_capacity: int = 5" in ajustes
    from bot_worker.scheduler.supervisor import JobSupervisor

    assert JobSupervisor.HARD_MAX_CONCURRENCY == 5
    with pytest.raises(ValueError):
        JobSupervisor(configured=6)
    contratos = (
        RAIZ / "packages/mrbot-contracts/src/mrbot_contracts/worker.py"
    ).read_text(encoding="utf-8")
    assert contratos.count("Field(ge=1, le=5)") >= 2


def test_ajustes_del_worker_recortan_a_1_5():
    """La configuración del worker rechaza concurrencia fuera de 1..5."""
    from bot_worker.config import WorkerConfig

    with pytest.raises(Exception):
        WorkerConfig(
            central_url=_CENTRAL_FICTICIA,
            advertised_url="http://127.0.0.1:8080",
            worker_concurrency=6,
        )


def test_registro_vivo_recorta_capacidad_a_5():
    """La central recorta la capacidad declarada al tope duro."""
    from central_api.store import WORKERS

    cliente = _cliente_central()
    nodo = "192.0.2.15:8080"
    try:
        respuesta = cliente.post(
            "/internal/v1/workers/register",
            json={"advertised_url": f"http://{nodo}", "capacity": 99},
        )
        assert respuesta.status_code == 200
        assert WORKERS[nodo].capacity == 5
    finally:
        WORKERS.pop(nodo, None)


def test_worker_aislado_sin_base_ni_secretos_ajenos():
    """El worker no importa datos ni recibe secretos de la central (W-1)."""
    prohibidos = ("sqlalchemy", "asyncpg", "psycopg", "create_engine")
    fugas = []
    for ruta in (RAIZ / "services/bot-worker/src").rglob("*.py"):
        if "__pycache__" in ruta.parts:
            continue
        texto = ruta.read_text(encoding="utf-8")
        for palabra in prohibidos:
            if palabra in texto:
                fugas.append(f"{ruta.name}: {palabra}")
    assert fugas == []
    bloque = _bloque_worker_compose()
    for prefijo in ("DATABASE_", "POSTGRES", "MINIO_", "MERCADOPAGO", "RSA_"):
        assert prefijo not in bloque, f"secreto ajeno en worker: {prefijo}"


def test_credenciales_no_fugan_en_representaciones():
    """Las representaciones del runtime redactan secretos."""
    from bot_worker.runtime.context import FiscalCredentials, ProxyConfig

    creds = FiscalCredentials(cuit_representante="20-1-9", clave="secreta")
    assert "secreta" not in repr(creds)
    proxy = ProxyConfig(mode="per-job", host="h", password="pw")
    assert "pw" not in repr(proxy)
