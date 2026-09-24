"""Pruebas de aceptación para documentación, compatibilidad y custodia V2."""

from __future__ import annotations

import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.main import create_app  # noqa: E402
from central_api.security.credentials import (  # noqa: E402
    normalize_v2_payload,
    normalize_payload_and_credentials,
)
from central_api.security.rsa_credentials import (  # noqa: E402
    decrypt_configured_credential,
)
from central_api.settings import get_settings  # noqa: E402
from central_api.store import JOBS  # noqa: E402


def _private_pem() -> str:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _encrypted(private_pem: str, value: str) -> str:
    private = serialization.load_pem_private_key(private_pem.encode(), password=None)
    blob = private.public_key().encrypt(
        value.encode(),
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=None,
        ),
    )
    return base64.b64encode(blob).decode()


def test_documentacion_publica_y_admin_son_superficies_distintas(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "admin-contract-token")
    get_settings.cache_clear()
    try:
        cliente = TestClient(create_app())
        publico = cliente.get("/openapi.json")
        assert publico.status_code == 200
        public_paths = set(publico.json()["paths"])
        assert public_paths
        assert all(path.startswith("/api/v3") for path in public_paths)
        assert not any(path.startswith("/internal") for path in public_paths)
        assert not any(path.startswith("/admin") for path in public_paths)

        admin_docs = cliente.get("/admin/docs")
        assert admin_docs.status_code == 200
        assert "/admin/openapi.json" in admin_docs.text
        assert cliente.get("/admin/openapi.json").status_code == 401
        admin = cliente.get(
            "/admin/openapi.json",
            headers={"Authorization": "Bearer admin-contract-token"},
        )
        assert admin.status_code == 200
        admin_paths = set(admin.json()["paths"])
        assert "/internal/v1/workers/register" in admin_paths
        assert "/admin/executions" in admin_paths
        assert "/admin/records" in admin_paths
        assert "/api/v3/bots/{bot}/{operacion}" in admin_paths
        assert admin.headers["cache-control"] == "private, no-store"
    finally:
        get_settings.cache_clear()


def test_cuerpo_v2_plano_separa_credencial_y_normaliza_aliases(monkeypatch) -> None:
    private_pem = _private_pem()
    monkeypatch.setenv("RSA_PRIVATE_KEY", private_pem)
    get_settings.cache_clear()
    try:
        normalized = normalize_v2_payload(
            "ccma",
            "consultar",
            {
                "cuit_representado": "20123456789",
                "movimientos": True,
                "pdf": True,
                "desde": "01/08/2026",
                "hasta": "31/08/2026",
                "proxy_request": {"host": "ignored"},
            },
        )
        assert normalized["representado_cuit"] == "20123456789"
        assert normalized["incluir_movimientos"] is True
        assert normalized["incluir_pdf"] is True
        assert "proxy_request" not in normalized
        encrypted = _encrypted(private_pem, "secreto-v2")
        payload, credentials, ciphertext, fingerprint = normalize_payload_and_credentials(
            {
                "cuit_representado": "20123456789",
                "desde": "01/08/2026",
                "clave_encriptada": encrypted,
            },
            require_encryption=True,
        )
        assert "clave_encriptada" not in payload
        assert credentials["clave"] == "secreto-v2"
        assert ciphertext and ciphertext != encrypted
        assert fingerprint
        assert decrypt_configured_credential(ciphertext) == "secreto-v2"
    finally:
        get_settings.cache_clear()


def test_alias_v2_plano_crea_registro_sin_credencial_en_payload(monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RSA_PRIVATE_KEY", raising=False)
    get_settings.cache_clear()
    JOBS.clear()
    try:
        cliente = TestClient(create_app())
        response = cliente.post(
            "/api/v3/ccma/consulta",
            headers={"Idempotency-Key": "v2-flat-credential-contract"},
            json={
                "cuit_representado": "20123456789",
                "movimientos": True,
                "pdf": True,
                "clave": "secreto-solo-memoria",
            },
        )
        assert response.status_code == 202
        job = JOBS[response.json()["job_id"]]
        assert "representado_cuit" in job.payload
        assert "clave" not in job.payload
        assert "cuit_representado" not in job.payload
        assert job.payload["incluir_movimientos"] is True
        assert job.credentials["clave"] == "secreto-solo-memoria"
    finally:
        JOBS.clear()
        get_settings.cache_clear()


def test_admin_registra_ejecuciones_y_revela_solo_con_auth(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "admin-record-token")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RSA_PRIVATE_KEY", raising=False)
    get_settings.cache_clear()
    JOBS.clear()
    try:
        cliente = TestClient(create_app())
        created = cliente.post(
            "/api/v3/ccma/consulta",
            headers={"Idempotency-Key": "admin-record-contract"},
            json={"representado_cuit": "20123456789", "clave": "admin-secret"},
        )
        job_id = created.json()["job_id"]
        assert cliente.get(f"/admin/jobs/{job_id}/credentials").status_code == 401
        headers = {"Authorization": "Bearer admin-record-token"}
        records = cliente.get("/admin/executions", headers=headers)
        assert records.status_code == 200
        assert records.json()["records"][0]["job"]["id"] == job_id
        revealed = cliente.get(f"/admin/jobs/{job_id}/credentials", headers=headers)
        assert revealed.status_code == 200
        assert revealed.json()["credentials"]["clave"] == "admin-secret"
        assert revealed.headers["cache-control"] == "private, no-store"
    finally:
        JOBS.clear()
        get_settings.cache_clear()


def test_admin_records_separa_tablas_y_no_expone_urls_de_minio(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "admin-table-token")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RSA_PRIVATE_KEY", raising=False)
    get_settings.cache_clear()
    JOBS.clear()
    try:
        cliente = TestClient(create_app())
        created = cliente.post(
            "/api/v3/ccma/consulta",
            headers={"Idempotency-Key": "admin-table-contract"},
            json={
                "cuit_representado": "20123456789",
                "clave": "table-secret",
            },
        )
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        headers = {"Authorization": "Bearer admin-table-token"}
        response = cliente.get("/admin/records?tabla=all", headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert set(body["tables"]) == {
            "jobs",
            "job_results",
            "job_artifacts",
            "job_events",
        }
        job_record = next(row for row in body["tables"]["jobs"] if row["id"] == job_id)
        assert job_record["credentials"]["available"] is True
        assert "clave" in job_record["credentials"]["fields"]
        assert "credential_ciphertext" not in job_record
        assert all("url" not in key.lower() for key in job_record)
        assert cliente.get(
            "/admin/records?tabla=invalid", headers=headers
        ).status_code == 400
    finally:
        JOBS.clear()
        get_settings.cache_clear()
