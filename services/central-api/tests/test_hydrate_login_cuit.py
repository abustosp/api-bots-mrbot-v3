"""Regresión: un job PENDIENTE rehidratado tras reiniciar la central conserva el CUIT de login.

Hallazgo de la prueba de reinicio con EC2 vivas: de 60 jobs rehidratados, 47
terminaron CREDENTIALS_REJECTED porque ``hydrate_pending_jobs`` solo restituía
``clave`` y no ``cuit_representante`` (el alta normal entrega ambos). Usa
dobles de la sesion, no requiere base.
"""

from __future__ import annotations

import asyncio
import uuid


def test_rehidratacion_restituye_cuit_de_login(monkeypatch):
    from types import SimpleNamespace

    from central_api.scheduler import loop
    from central_api.store import JOBS

    job_id = str(uuid.uuid4())
    row = SimpleNamespace(
        id=job_id, bot="mis_comprobantes", operation="consultar",
        request_payload={"representado_cuit": "27116976620"},
        credential_ciphertext="cifrado",
        credential_metadata={"fields": ["contrasena"], "context": {
            "representado_cuit": "27116976620", "cuit_representante": "20111111112"}},
        attempts=0, created_at=None, priority=0, status="PENDIENTE",
    )

    class _Res:
        def scalars(self):
            return self
        def all(self):
            return [row]

    class _Ses:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def execute(self, *_a, **_k):
            return _Res()

    monkeypatch.setattr(loop, "db_configurado", lambda: True)
    monkeypatch.setattr(loop, "nueva_sesion", lambda: _Ses())
    import central_api.security.rsa_credentials as rsa
    monkeypatch.setattr(rsa, "decrypt_configured_credential", lambda blob: "la-clave")

    JOBS.pop(job_id, None)
    try:
        elegibles = asyncio.run(loop.hydrate_pending_jobs())
        assert job_id in elegibles
        creds = JOBS[job_id].credentials
        assert creds["clave"] == "la-clave"
        assert creds["cuit_representante"] == "20111111112"  # el CUIT de login, no el representado
    finally:
        JOBS.pop(job_id, None)
