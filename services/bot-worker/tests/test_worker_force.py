"""Forzado de ejecución: única vía que supera el cupo del worker.

Cubre que ``supervisor.accept(force=True)`` admite sobre el tope, que el
``POST /internal/v1/jobs`` con ``force`` firmado bajo ``assign-force``
entra aun saturado, que sin esa firma se rechaza y que el drenaje se sigue
respetando incluso forzado.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient

from bot_worker.config import PROTOCOL_VERSION, WorkerConfig
from bot_worker.main import create_app
from bot_worker.scheduler.supervisor import (
    JobSupervisor,
    LocalJob,
    WorkerDraining,
    WorkerSaturated,
)


def _trabajo() -> LocalJob:
    return LocalJob(
        job_id=str(uuid.uuid4()),
        attempt=1,
        lease_id=str(uuid.uuid4()),
        plugin="siper",
        operation="consultar",
    )


def test_accept_force_supera_cupo_pero_no_drenaje() -> None:
    async def _ciclo() -> None:
        sup = JobSupervisor(configured=1)
        await sup.accept(_trabajo(), {"accepted": True})
        try:
            await sup.accept(_trabajo(), {"accepted": True})
            raise AssertionError("sin cupo debería saturar")
        except WorkerSaturated:
            pass
        forzado = await sup.accept(_trabajo(), {"accepted": True}, force=True)
        assert forzado.duplicate is False
        assert sup.en_ejecucion == 2
        sup.begin_drain()
        try:
            await sup.accept(_trabajo(), {"accepted": True}, force=True)
            raise AssertionError("en drenaje debería rechazar incluso forzado")
        except WorkerDraining:
            pass

    asyncio.run(_ciclo())


def _sobre_firmado(app, privada, *, force: bool, scope: str) -> dict:
    from central_api.security.assignments import (
        default_expiry,
        sealed_hash_of,
        sign_assignment,
    )

    jid, lid = str(uuid.uuid4()), str(uuid.uuid4())
    expira = default_expiry(300)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "job_id": jid,
        "attempt": 1,
        "lease_id": lid,
        "lease_expires_at": "2030-01-01T00:00:00Z",
        "plugin": "siper",
        "operation": "consultar",
        "credentials": {"cuit_representante": "20123456789", "clave": "ficticia"},
        "force": force,
        "assignment_expires_at": expira,
        "assignment_signature": sign_assignment(
            privada,
            scope=scope,
            job_id=jid,
            attempt=1,
            lease_id=lid,
            expires_at=expira,
            sealed_hash=sealed_hash_of(None),
        ),
    }


def test_post_jobs_force_saturado_y_firma_incorrecta() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from bot_worker.security.assignments import load_verify_key
    from central_api.security.assignments import (
        ASSIGNMENT_SCOPE_ASSIGN,
        ASSIGNMENT_SCOPE_ASSIGN_FORCE,
        public_pem,
    )

    ajustes = WorkerConfig(
        central_url="http://central-inaccesible.local",
        advertised_url="http://127.0.0.1:8080",
        worker_concurrency=1,
    )
    app = create_app(ajustes)
    privada = Ed25519PrivateKey.generate()
    app.state.central_verify_key = load_verify_key(public_pem(privada))

    async def _ocupar() -> None:
        await app.state.supervisor.accept(_trabajo(), {"accepted": True})

    asyncio.run(_ocupar())
    cliente = TestClient(app, raise_server_exceptions=True)

    normal = _sobre_firmado(app, privada, force=False, scope=ASSIGNMENT_SCOPE_ASSIGN)
    respuesta = cliente.post("/internal/v1/jobs", json=normal)
    assert respuesta.status_code == 409
    assert respuesta.json()["code"] == "WORKER_SATURADO"

    forzado = _sobre_firmado(
        app, privada, force=True, scope=ASSIGNMENT_SCOPE_ASSIGN_FORCE
    )
    respuesta = cliente.post("/internal/v1/jobs", json=forzado)
    assert respuesta.status_code == 202
    assert respuesta.json()["accepted"] is True

    # Forzado con firma de alcance normal: se rechaza (403), no se ejecuta.
    trucho = _sobre_firmado(app, privada, force=True, scope=ASSIGNMENT_SCOPE_ASSIGN)
    respuesta = cliente.post("/internal/v1/jobs", json=trucho)
    assert respuesta.status_code == 403
