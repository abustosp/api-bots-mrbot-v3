"""Pruebas HTTP reales del bot-worker con TestClient.

Verifica salud anónima, monitoreo abierto en red privada, tope de capacidad
5, protocolo 1 entero y, sobre todo, el gate de ejecución: solo corre lo
firmado por la central (Ed25519). Sin firma válida no hay ejecución (403
NO_FIRMADO), aunque el puerto sea alcanzable. No toca base de datos (W-1),
no usa entorno y no expone material sensible.
"""
from __future__ import annotations

import uuid

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from bot_worker.config import PROTOCOL_VERSION, WorkerConfig
from bot_worker.main import create_app
from bot_worker.security.assignments import load_verify_key
from central_api.security.assignments import (
    ASSIGNMENT_SCOPE_ASSIGN,
    default_expiry,
    public_pem,
    sealed_hash_of,
    sign_assignment,
)

_CENTRAL_FICTICIA = "http://central-inaccesible.local"
_ANUNCIADA = "http://127.0.0.1:8080"


def _ajustes(**extras):
    """Configuración de prueba: solo CLI, sin entorno ni secretos."""
    base = {
        "central_url": _CENTRAL_FICTICIA,
        "advertised_url": _ANUNCIADA,
    }
    base.update(extras)
    return WorkerConfig(**base)


def _llaves():
    """Par Ed25519 de prueba: la central firma, el worker verifica."""
    privada = Ed25519PrivateKey.generate()
    return privada, load_verify_key(public_pem(privada))


def _cliente(clave_publica=None) -> TestClient:
    """Cliente sin ciclo de vida (evita latidos reales hacia la central)."""
    app = create_app(_ajustes())
    app.state.central_verify_key = clave_publica
    return TestClient(app, raise_server_exceptions=True)


def _sobre(job_id: str, privada, *, intento=1, vencida=False) -> dict:
    """Sobre mínimo con firma de la central de prueba."""
    expira = "2000-01-01T00:00:00Z" if vencida else default_expiry(300)
    firma = sign_assignment(
        privada,
        scope=ASSIGNMENT_SCOPE_ASSIGN,
        job_id=job_id,
        attempt=intento,
        lease_id=job_id,
        expires_at=expira,
        sealed_hash=sealed_hash_of(None),
    )
    return {
        "protocol_version": 1,
        "job_id": job_id,
        "attempt": intento,
        "lease_id": job_id,
        "lease_expires_at": default_expiry(600),
        "plugin": "consulta_cuit",
        "operation": "consultar",
        "payload": {},
        "assignment_signature": firma,
        "assignment_expires_at": expira,
    }


def test_salud_anonima():
    """La vivacidad del worker no exige autenticación."""
    respuesta = _cliente().get("/internal/v1/health")
    assert respuesta.status_code == 200
    assert respuesta.json()["status"] == "ok"


def test_estado_abierto_en_red_privada():
    """El monitoreo no pide token: la red privada + mTLS lo aíslan."""
    respuesta = _cliente().get("/internal/v1/status")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["protocol_version"] == 1
    assert PROTOCOL_VERSION == 1
    assert isinstance(cuerpo["protocol_version"], int)
    assert cuerpo["capacity"] <= 5
    assert cuerpo["en_ejecucion"] >= 0


def test_bots_abierto():
    """El inventario de bots es visible y declara protocolo 1."""
    respuesta = _cliente().get("/internal/v1/bots")
    assert respuesta.status_code == 200
    cuerpo = respuesta.json()
    assert cuerpo["protocol_version"] == 1
    assert isinstance(cuerpo["bots"], list)


def test_asignacion_sin_firma_no_ejecuta():
    """Sin firma de la central no hay ejecución, aunque todo lo demás valga."""
    jid = str(uuid.uuid4())
    sobre = _sobre(jid, Ed25519PrivateKey.generate())
    sobre["assignment_signature"] = ""
    respuesta = _cliente().post("/internal/v1/jobs", json=sobre)
    assert respuesta.status_code == 403
    assert respuesta.json()["code"] == "NO_FIRMADO"


def test_asignacion_sin_clave_fijada_no_ejecuta():
    """Sin registro (sin clave fijada) todo se rechaza: falla cerrado."""
    privada, _ = _llaves()
    jid = str(uuid.uuid4())
    respuesta = _cliente(None).post("/internal/v1/jobs", json=_sobre(jid, privada))
    assert respuesta.status_code == 403
    assert respuesta.json()["code"] == "NO_FIRMADO"


def test_asignacion_firmada_pasa_el_gate():
    """Con firma válida el gate pasa (el 422 posterior es validación, no gate)."""
    privada, publica = _llaves()
    jid = str(uuid.uuid4())
    sobre = _sobre(jid, privada, intento=0)  # intento inválido a propósito
    respuesta = _cliente(publica).post("/internal/v1/jobs", json=sobre)
    assert respuesta.status_code == 422
    assert respuesta.json()["code"] == "SOBRE_INVALIDO"


def test_asignacion_adulterada_no_ejecuta():
    """Cambiar el job_id tras firmar invalida la firma."""
    privada, publica = _llaves()
    sobre = _sobre(str(uuid.uuid4()), privada)
    sobre["job_id"] = str(uuid.uuid4())
    respuesta = _cliente(publica).post("/internal/v1/jobs", json=sobre)
    assert respuesta.status_code == 403


def test_asignacion_vencida_no_ejecuta():
    """Una firma vencida no ejecuta aunque sea auténtica."""
    privada, publica = _llaves()
    sobre = _sobre(str(uuid.uuid4()), privada, vencida=True)
    respuesta = _cliente(publica).post("/internal/v1/jobs", json=sobre)
    assert respuesta.status_code == 403


def test_asignacion_de_otra_clave_no_ejecuta():
    """Firma válida pero de otra central: se rechaza."""
    _, publica = _llaves()
    sobre = _sobre(str(uuid.uuid4()), Ed25519PrivateKey.generate())
    respuesta = _cliente(publica).post("/internal/v1/jobs", json=sobre)
    assert respuesta.status_code == 403
