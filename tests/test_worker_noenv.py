"""Worker sin entorno + gate de ejecución por firma de la central.

Fija el contrato nuevo:
- la config del worker sale solo de argv (el entorno con secretos falla);
- lo sensible viaja sellado y provisionado por la central (proxy, captcha,
  servicio), nunca en entorno ni en reposo;
- el worker solo ejecuta asignaciones firmadas por la central (Ed25519),
  con interoperabilidad real central→worker.
"""
from __future__ import annotations

import os
import uuid
from unittest import mock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from bot_worker.config import (
    WorkerConfig,
    assert_no_forbidden_env,
    parse_args,
)
from bot_worker.main import create_app
from bot_worker.runtime.captcha import CaptchaSolver
from bot_worker.runtime.proxies import build_proxy_config
from bot_worker.security.assignments import (
    AssignmentDenied,
    load_verify_key,
    verify_assignment,
)
from central_api.security.assignments import (
    ASSIGNMENT_SCOPE_ASSIGN,
    default_expiry,
    ensure_signing_key,
    public_pem,
    sealed_hash_of,
    sign_assignment,
    verify_with_public_key,
)

_ADVERTISED = "http://127.0.0.1:8080"
_CENTRAL = "http://central.local"


def _argv(**cambios):
    base = {
        "central_url": _CENTRAL,
        "advertised_url": _ADVERTISED,
    }
    base.update(cambios)
    args = []
    for clave, valor in base.items():
        args += [f"--{clave.replace('_', '-')}", str(valor)]
    return args  # sin nombre de programa: parse_args recibe solo flags


# ------------------------------------------------- config sin entorno ---


def test_config_solo_argv_ignora_entorno():
    """Variables en el entorno no configuran nada: manda argv."""
    with mock.patch.dict(
        os.environ,
        {"CENTRAL_URL": "http://maligno.local", "WORKER_CONCURRENCY": "1"},
        clear=False,
    ):
        # CENTRAL_URL/WORKER_* en entorno son prohibidas: primero el guard.
        with pytest.raises(RuntimeError):
            parse_args(_argv())
    cfg = parse_args(_argv())
    assert cfg.central_url == _CENTRAL
    assert cfg.worker_concurrency == 5


def test_guard_rechaza_secretos_en_entorno():
    """Cualquier secreto en el entorno aborta el arranque."""
    with mock.patch.dict(os.environ, {"WORKER_TOKEN": "x"}):
        with pytest.raises(RuntimeError, match="prohibidas"):
            assert_no_forbidden_env()
    with mock.patch.dict(os.environ, {"CAPMONSTER_ARCA_KEY": "x"}):
        with pytest.raises(RuntimeError, match="prohibidas"):
            assert_no_forbidden_env()


def test_concurrencia_fuera_de_rango_falla():
    """W-2 también en construcción programática."""
    with pytest.raises(ValueError):
        WorkerConfig(
            central_url=_CENTRAL, advertised_url=_ADVERTISED, worker_concurrency=6
        )
    with pytest.raises(ValueError):
        parse_args(_argv(**{"concurrency": "0"}))


# --------------------------------------- provisión sin entorno/reposo ---


def test_proxy_solo_desde_perfil_sin_entorno():
    """Sin perfil no hay proxy; con perfil se construye sin leer entorno."""
    with mock.patch.dict(os.environ, {"PROXY_HOST": "proxy.maligno.local"}):
        assert build_proxy_config(None) is None
        assert build_proxy_config({}) is None
    proxy = build_proxy_config({"host": "10.0.0.5:8080", "username": "u"})
    assert proxy is not None and proxy.host == "10.0.0.5:8080"


def test_captcha_deshabilitado_sin_clave_provisionada():
    """Sin clave del sobre no se resuelve nada (stub deshabilitado)."""
    solver = CaptchaSolver.from_profile("arca", {"enabled": True})
    assert solver.enabled is False
    solver = CaptchaSolver.from_profile(
        "arca", {"enabled": True, "arca_key": "k-ficticia"}
    )
    assert solver.enabled is True
    solver = CaptchaSolver.from_profile(
        "arca", {"enabled": True, "arca_key": "sin-configurar"}
    )
    assert solver.enabled is False


# --------------------------------------------- firma central → worker ---


def _firmar(privada, job_id, intento=1, expira=None, sellado=None):
    expira = expira or default_expiry(300)
    firma = sign_assignment(
        privada,
        scope=ASSIGNMENT_SCOPE_ASSIGN,
        job_id=job_id,
        attempt=intento,
        lease_id=job_id,
        expires_at=expira,
        sealed_hash=sealed_hash_of(sellado),
    )
    return expira, firma


def test_interop_firma_central_verifica_worker():
    """La firma de la central abre el gate del worker (ida y vuelta)."""
    privada, _ = ensure_signing_key("")
    publica = load_verify_key(public_pem(privada))
    jid = str(uuid.uuid4())
    expira, firma = _firmar(privada, jid)
    verify_assignment(
        publica,
        scope=ASSIGNMENT_SCOPE_ASSIGN,
        job_id=jid,
        attempt=1,
        lease_id=jid,
        expires_at=expira,
        sealed_section=None,
        signature_b64=firma,
    )
    assert verify_with_public_key(
        privada.public_key(),
        scope=ASSIGNMENT_SCOPE_ASSIGN,
        job_id=jid,
        attempt=1,
        lease_id=jid,
        expires_at=expira,
        sealed_hash=sealed_hash_of(None),
        signature_b64=firma,
    )


def test_gate_rechaza_adulterada_vencida_y_otra_clave():
    """Cualquier desvío del sobre firmado se rechaza en cerrado."""
    privada, _ = ensure_signing_key("")
    publica = load_verify_key(public_pem(privada))
    jid = str(uuid.uuid4())
    expira, firma = _firmar(privada, jid)
    base = dict(
        scope=ASSIGNMENT_SCOPE_ASSIGN,
        attempt=1,
        lease_id=jid,
        expires_at=expira,
        sealed_section=None,
        signature_b64=firma,
    )
    with pytest.raises(AssignmentDenied):
        verify_assignment(publica, job_id=str(uuid.uuid4()), **base)
    with pytest.raises(AssignmentDenied):
        verify_assignment(
            publica, job_id=jid, expires_at="2000-01-01T00:00:00Z", **{
                k: v for k, v in base.items() if k != "expires_at"
            },
        )
    otra, _ = ensure_signing_key("")
    with pytest.raises(AssignmentDenied):
        verify_assignment(load_verify_key(public_pem(otra)), job_id=jid, **base)
    with pytest.raises(AssignmentDenied):
        verify_assignment(None, job_id=jid, **base)


def _cliente(publica=None) -> TestClient:
    app = create_app(
        WorkerConfig(central_url=_CENTRAL, advertised_url=_ADVERTISED)
    )
    app.state.central_verify_key = publica
    return TestClient(app, raise_server_exceptions=True)


def _sobre(job_id, privada, **extras):
    expira, firma = _firmar(privada, job_id)
    sobre = {
        "protocol_version": 1,
        "job_id": job_id,
        "attempt": 1,
        "lease_id": job_id,
        "lease_expires_at": default_expiry(600),
        "plugin": "consulta_cuit",
        "operation": "consultar",
        "payload": {},
        "assignment_expires_at": expira,
        "assignment_signature": firma,
    }
    sobre.update(extras)
    return sobre


def test_post_sin_firma_no_ejecuta_aunque_puerto_abierto():
    """Atacante con red pero sin clave de la central: 403 sin ejecutar."""
    jid = str(uuid.uuid4())
    sobre = _sobre(jid, Ed25519PrivateKey.generate())
    sobre["assignment_signature"] = ""
    respuesta = _cliente().post("/internal/v1/jobs", json=sobre)
    assert respuesta.status_code == 403
    assert respuesta.json()["code"] == "NO_FIRMADO"


def test_post_firmado_llega_a_validacion_no_al_gate():
    """Firma válida + intento inválido: 422 de validación, no 403 de gate."""
    privada, _ = ensure_signing_key("")
    publica = load_verify_key(public_pem(privada))
    jid = str(uuid.uuid4())
    expira, firma = _firmar(privada, jid, intento=0)
    sobre = _sobre(jid, privada, **{
        "attempt": 0,
        "assignment_expires_at": expira,
        "assignment_signature": firma,
    })
    respuesta = _cliente(publica).post("/internal/v1/jobs", json=sobre)
    assert respuesta.status_code == 422
    assert respuesta.json()["code"] == "SOBRE_INVALIDO"
