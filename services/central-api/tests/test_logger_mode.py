"""Modo logger en la central: bandera estricta y presign oculto al bucket logger."""

import pytest
from fastapi import HTTPException

from central_api.api.bots import _logger_activo
from central_api.internal.uploads import clave_logger


def test_solo_true_booleano():
    assert _logger_activo(True)
    for valor in ("true", 1, "1", None, False, "si"):
        assert not _logger_activo(valor)


def test_clave_logger_es_el_tar_gz_del_job():
    assert clave_logger("j1", "j1.tar.gz") == "j1.tar.gz"
    for malo in ("../x", "/abs", "j2.tar.gz", "j1/har/a.har", "j1.tar.gz/x"):
        with pytest.raises(HTTPException):
            clave_logger("j1", malo)


def test_presign_logger_no_esta_en_openapi():
    import json

    from central_api import main
    from central_api.internal import uploads

    rutas = {getattr(r, "path", ""): r for r in uploads.router.routes}
    assert "/jobs/{job_id}/logger/presign" in rutas
    assert rutas["/jobs/{job_id}/logger/presign"].include_in_schema is False
    for privado in (False, True):
        esquema = main._documentation_app(include_private=privado).openapi()
        assert "logger" not in json.dumps(esquema).lower()


def test_solicitud_logger_cifra_las_claves(monkeypatch):
    from central_api.scheduler import dispatcher
    from central_api.security import rsa_credentials
    from central_api.store import Job

    monkeypatch.setattr(
        rsa_credentials, "encrypt_configured_credential", lambda v: "ENC(" + v[::-1] + ")"
    )
    job = Job(
        id="j1", bot="arba", operation="descargar", payload={"anio": "2026", "logger": True},
        credentials={"cuit_representante": "20111111112", "clave": "Secreta123"},
    )
    pedido = dispatcher._logger_request(job)
    assert pedido["payload"]["anio"] == "2026"
    assert pedido["credentials_encrypted_rsa_oaep"]["cuit_representante"] == "20111111112"
    assert "Secreta123" not in str(pedido)
