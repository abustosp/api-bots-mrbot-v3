"""La central cifra con la pública del worker; el worker abre al correr.

Cubre los dos flujos sensibles:
- sobre de asignación: la central sella credenciales/proxy/captcha/servicio
  con la pública efímera del registro y el worker lo abre en memoria;
- presign de artefactos: la URL prefirmada viaja sellada cuando hay pubkey
  (en claro con bandera visible si no) y el worker la abre al subir.

Sin la privada del worker nada es legible: ni credenciales, ni proxy, ni
URLs de subida.
"""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from bot_worker.reporting.central import PresignError, request_presign
from bot_worker.runtime.sealed import (
    SealedEnvelopeError,
    decrypt_sealed_section,
    generate_sealed_keypair,
)
from central_api.security.sealed import encrypt_sealed_section
from central_api.store import JOBS, WORKERS, Job, WorkerEntry, new_job_id

_NODO = "192.0.2.50:8080"


@pytest.fixture()
def claves():
    """Par efímero del worker como en el arranque real."""
    return generate_sealed_keypair()


@pytest.fixture()
def central_app(claves, monkeypatch):
    """App central con job asignado y worker registrado con pubkey."""
    from central_api.main import create_app
    from central_api.settings import get_settings

    monkeypatch.delenv("INTERNAL_JWT_SIGNING_KEY", raising=False)
    get_settings.cache_clear()
    priv, pub = claves
    jid = new_job_id()
    JOBS[jid] = Job(
        id=jid,
        bot="consulta_cuit",
        operation="consulta",
        payload={},
        credentials={"cuit": "20-12345678-9"},
        status="ASIGNADO",
        worker_node=_NODO,
    )
    WORKERS[_NODO] = WorkerEntry(
        node=_NODO, status="SANO", sealed_pubkey_pem=pub
    )
    yield create_app(), jid, priv
    JOBS.pop(jid, None)
    WORKERS.pop(_NODO, None)
    get_settings.cache_clear()


class _Respuesta:
    def __init__(self, codigo: int, cuerpo: dict):
        self.status_code = codigo
        self._cuerpo = cuerpo

    def json(self):
        return self._cuerpo


class _ClienteFalso:
    """HTTP falso que devuelve una respuesta prefijada."""

    def __init__(self, cuerpo: dict, codigo: int = 201):
        self._cuerpo = cuerpo
        self._codigo = codigo

    async def post(self, *args, **kwargs):
        return _Respuesta(self._codigo, self._cuerpo)


def _pedir(client, **extras):
    import anyio

    async def _go():
        return await request_presign(
            client,
            "http://central.local",
            job_id="jid",
            artifact_id="a1",
            content_type="text/plain",
            size_bytes=10,
            worker_node=_NODO,
            **extras,
        )

    return anyio.run(_go)


def test_sobre_lleva_todo_cifrado_y_worker_lo_abre(claves):
    """Sobre de asignación real: la central sella, el worker abre en memoria."""
    from central_api.scheduler.dispatcher import build_envelope

    priv, pub = claves
    trabajo = Job(
        id=new_job_id(),
        bot="consulta_cuit",
        operation="consulta",
        payload={"cuit": "20-12345678-9"},
        credentials={"clave": "ficticia"},
    )
    obrero = WorkerEntry(node=_NODO, sealed_pubkey_pem=pub)
    sobre = build_envelope(trabajo, obrero, "tok")
    assert sobre["sealed"] is True
    assert sobre["credentials"] is None
    plano = json.dumps(sobre["sealed_section"])
    assert "ficticia" not in plano  # nada sensible en claro
    abierto = decrypt_sealed_section(priv, sobre["sealed_section"])
    assert abierto["credentials"] == {"clave": "ficticia"}
    assert set(abierto) >= {"credentials", "proxy", "captcha", "service"}


def test_otra_clave_no_abre_el_sobre(claves):
    """Sin la privada del worker dueño, el sobre es opaco."""
    from central_api.scheduler.dispatcher import build_envelope

    _, pub = claves
    otra_priv, _ = generate_sealed_keypair()
    trabajo = Job(
        id=new_job_id(), bot="x", operation="y", payload={},
        credentials={"clave": "ficticia"},
    )
    sobre = build_envelope(trabajo, WorkerEntry(node=_NODO, sealed_pubkey_pem=pub), "t")
    with pytest.raises(SealedEnvelopeError):
        decrypt_sealed_section(otra_priv, sobre["sealed_section"])


def test_presign_sellado_punta_a_punta(central_app):
    """presign con pubkey: sellado en el wire, abierto en el worker."""
    app, jid, priv = central_app
    cliente = TestClient(app, raise_server_exceptions=True)
    respuesta = cliente.post(
        f"/internal/v1/jobs/{jid}/artifacts/presign",
        json={"artifact_id": "a1", "content_type": "text/plain", "size_bytes": 10},
        headers={"X-Worker-Node": _NODO},
    )
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["sealed"] is True
    assert "upload_url" not in cuerpo
    assert "https://" not in json.dumps(cuerpo["sealed_section"])
    abierto = _pedir(
        _ClienteFalso(cuerpo), sealed_privkey=priv
    )
    assert abierto["upload_url"].startswith("https://")
    assert abierto["object_key"].startswith(f"jobs/{jid}/")


def test_presign_sin_pubkey_en_claro_con_bandera(central_app, monkeypatch):
    """Sin pubkey publicada: en claro pero marcado (modo desarrollo)."""
    from central_api.settings import get_settings

    app, jid, _priv = central_app
    WORKERS[_NODO].sealed_pubkey_pem = ""
    cliente = TestClient(app, raise_server_exceptions=True)
    respuesta = cliente.post(
        f"/internal/v1/jobs/{jid}/artifacts/presign",
        json={"artifact_id": "a1", "content_type": "text/plain", "size_bytes": 10},
        headers={"X-Worker-Node": _NODO},
    )
    assert respuesta.status_code == 201
    cuerpo = respuesta.json()
    assert cuerpo["sealed"] is False
    assert cuerpo["upload_url"].startswith("https://")


def test_presign_adulterado_falla_cerrado():
    """Sección sellada tocada: el worker no sube nada."""
    _priv, pub = generate_sealed_keypair()
    seccion = encrypt_sealed_section(pub, {"upload_url": "https://x/y"})
    seccion["blob_b64"] = "AAAA"
    with pytest.raises(PresignError):
        _pedir(
            _ClienteFalso({"sealed": True, "sealed_section": seccion}),
            sealed_privkey=_priv,
        )


def test_presign_sellado_sin_clave_falla_cerrado():
    """Respuesta sellada pero sin privada a mano: no se inventa nada."""
    _priv, pub = generate_sealed_keypair()
    seccion = encrypt_sealed_section(pub, {"upload_url": "https://x/y"})
    with pytest.raises(PresignError):
        _pedir(_ClienteFalso({"sealed": True, "sealed_section": seccion}))


def test_reinicio_rota_el_par_y_la_central_actualiza(monkeypatch):
    """Cada arranque genera un par nuevo; el re-registro lo publica."""
    from central_api.main import create_app
    from central_api.settings import get_settings
    from central_api.store import WORKERS

    monkeypatch.setenv("WORKER_NODES", _NODO)
    get_settings.cache_clear()
    try:
        priv1, pub1 = generate_sealed_keypair()
        priv2, pub2 = generate_sealed_keypair()
        assert pub1 != pub2  # par fresco por arranque, nunca reutilizado
        app = create_app()
        cliente = TestClient(app, raise_server_exceptions=True)
        base = {
            "advertised_url": f"http://{_NODO}",
            "capacity": 5,
            "sealed_pubkey_pem": pub1,
        }
        assert cliente.post("/internal/v1/workers/register", json=base).status_code < 400
        assert WORKERS[_NODO].sealed_pubkey_pem == pub1
        base["sealed_pubkey_pem"] = pub2  # reinicio con par nuevo
        assert cliente.post("/internal/v1/workers/register", json=base).status_code < 400
        assert WORKERS[_NODO].sealed_pubkey_pem == pub2
        # Lo sellado para el par viejo ya no abre con el nuevo...
        seccion = encrypt_sealed_section(pub1, {"clave": "x"})
        with pytest.raises(SealedEnvelopeError):
            decrypt_sealed_section(priv2, seccion)
        # ...y lo nuevo abre con lo nuevo.
        seccion = encrypt_sealed_section(pub2, {"clave": "x"})
        assert decrypt_sealed_section(priv2, seccion) == {"clave": "x"}
    finally:
        WORKERS.pop(_NODO, None)
        get_settings.cache_clear()
