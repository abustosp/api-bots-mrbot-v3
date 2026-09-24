"""Pruebas de aceptación para documentación, compatibilidad y custodia V2."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api.bots import CATALOGUE  # noqa: E402
from central_api.admin import jobs as admin_jobs  # noqa: E402
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


def _assert_no_storage_urls(value) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            lowered = str(key).lower()
            assert "url" not in lowered
            assert "object_key" not in lowered
            _assert_no_storage_urls(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_storage_urls(nested)


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
        assert "/admin/table-catalog" in admin_paths
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
        assert cliente.get("/admin/records?tabla=all").status_code == 401
        assert cliente.get(
            "/admin/records?tabla=all",
            headers={"Authorization": "Bearer incorrecto"},
        ).status_code == 403
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
        sections = body["bot_sections"]
        assert len(sections) == len(CATALOGUE)
        assert sum(len(section["operaciones"]) for section in sections) == 42
        ccma = next(section for section in sections if section["bot"] == "ccma")
        assert ccma["tablas"] == ["jobs", "job_results", "job_artifacts", "job_events"]
        assert "consulta_ccma_logs" in ccma["tablas_legacy"]
        assert ccma["total"] == 1
        ccma_record = ccma["records"][0]
        assert "request" in ccma_record
        assert "response" in ccma_record
        assert "table-secret" not in response.text
        assert all(
            "url" not in key.lower()
            for row in body["tables"]["job_artifacts"]
            for key in row
        )
        _assert_no_storage_urls(body)
        filtered = cliente.get("/admin/records?tabla=all&bot=ccma", headers=headers)
        assert filtered.status_code == 200
        filtered_sections = filtered.json()["bot_sections"]
        assert len(filtered_sections) == 1
        assert filtered_sections[0]["bot"] == "ccma"
        assert cliente.get(
            "/admin/records?tabla=invalid", headers=headers
        ).status_code == 400
        # Un filtro mal formado no puede devolver una página sin filtrar como si
        # fuera válida: el identificador se valida antes de tocar la base.
        mal_formado = cliente.get(
            "/admin/records?tabla=all&job_id=no-es-uuid", headers=headers
        )
        assert mal_formado.status_code == 400
        assert mal_formado.json()["detail"] == "job_id con formato inválido"
        valido = cliente.get(
            f"/admin/records?tabla=all&job_id={job_id}", headers=headers
        )
        assert valido.status_code == 200
        assert [row["id"] for row in valido.json()["tables"]["jobs"]] == [job_id]
    finally:
        JOBS.clear()
        get_settings.cache_clear()


def test_admin_table_catalogo_y_tabla_virtual_por_bot(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "admin-table-catalog-token")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("RSA_PRIVATE_KEY", raising=False)
    get_settings.cache_clear()
    JOBS.clear()
    try:
        cliente = TestClient(create_app())
        headers = {"Authorization": "Bearer admin-table-catalog-token"}
        assert cliente.get("/admin/table-catalog").status_code == 401
        catalog = cliente.get("/admin/table-catalog", headers=headers)
        assert catalog.status_code == 200
        body = catalog.json()
        assert body["canonical_tables"] == [
            "jobs", "job_results", "job_artifacts", "job_events"
        ]
        assert len(body["bot_tables"]) == len(CATALOGUE)
        assert next(item for item in body["bot_tables"] if item["name"] == "bot:ccma")["legacy"] == [
            "consulta_ccma_logs"
        ]
        ccma_table = next(item for item in body["bot_tables"] if item["name"] == "bot:ccma")
        assert ccma_table["physical_name"] == "bot_jobs_ccma"
        assert "request_payload" in ccma_table["columns"]
        assert "response_payload" in ccma_table["columns"]
        assert "credential_ciphertext" not in ccma_table["columns"]

        for suffix in ("one", "two"):
            created = cliente.post(
                "/api/v3/ccma/consulta",
                headers={"Idempotency-Key": f"admin-bot-table-{suffix}"},
                json={"representado_cuit": "20123456789", "clave": "catalog-secret"},
            )
            assert created.status_code == 202
        for job in JOBS.values():
            job.result = {
                "download_url": "https://storage.example/fallback?token=secret",
                "object_key": "private/fallback/object",
                "api_token": "fallback-plaintext-secret",
                "message": "Archivo en s3://bucket/private/file.pdf",
            }

        selected = cliente.get(
            "/admin/records?tabla=bot:ccma&limit=1",
            headers=headers,
        )
        assert selected.status_code == 200
        selected_body = selected.json()
        assert selected_body["tabla_resuelta"] == "bot:ccma"
        assert selected_body["catalogo"]["kind"] == "bot"
        assert selected_body["has_more"] is True
        assert len(selected_body["bot_sections"]) == 1
        assert selected_body["bot_sections"][0]["bot"] == "ccma"
        assert len(selected_body["records"]) == 1
        assert "request" in selected_body["records"][0]
        assert "response" in selected_body["records"][0]
        assert "https://" not in selected.text
        assert "s3://" not in selected.text
        assert "object_key" not in selected.text
        assert "fallback-plaintext-secret" not in selected.text

        next_page = cliente.get(
            "/admin/records?tabla=bot:ccma&limit=1&offset=1&operacion=consultar",
            headers=headers,
        )
        assert next_page.status_code == 200
        assert next_page.json()["has_more"] is False
        assert len(next_page.json()["records"]) == 1
        assert cliente.get(
            "/admin/records?tabla=bot:ccma&bot=siper", headers=headers
        ).status_code == 400
        assert cliente.get(
            "/admin/records?tabla=jobs&limit=1", headers=headers
        ).json()["tabla_resuelta"] == "jobs"
    finally:
        JOBS.clear()
        get_settings.cache_clear()


def test_admin_records_consulta_tabla_fisica_por_bot_con_filtros_y_redaccion(monkeypatch) -> None:
    """El endpoint usa el nombre allowlistado y no devuelve secretos ni URLs."""
    monkeypatch.setenv("ADMIN_TOKEN", "admin-physical-table-token")
    get_settings.cache_clear()
    column_names = [*admin_jobs.BOT_TABLE_COLUMNS, "credential_ciphertext"]
    query_log = []

    class FakeResult:
        def __init__(self, *, scalar_rows=None, row_mappings=None, scalar=None):
            self._scalar_rows = scalar_rows
            self._row_mappings = row_mappings
            self._scalar = scalar

        def scalars(self):
            return self

        def all(self):
            return self._scalar_rows if self._scalar_rows is not None else self._row_mappings

        def scalar_one(self):
            return self._scalar

        def mappings(self):
            return self

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, statement, params=None):
            sql = str(statement)
            query_log.append((sql, dict(params or {})))
            if "information_schema.columns" in sql:
                return FakeResult(scalar_rows=column_names)
            if "COUNT(*)" in sql:
                return FakeResult(scalar=3)
            return FakeResult(row_mappings=[{
                "job_id": "87cbd2c2-6995-47ca-ae70-cd40b72307a8",
                "user_id": "daaaaf72-7cee-4ce8-9ef4-7c47a5f173cb",
                "usuario_email": "cliente@example.com",
                "worker_id": "worker-test",
                "bot": "ccma",
                "operation": "consultar",
                "status": "COMPLETO",
                "result": "ok",
                "priority": 5,
                "attempts": 1,
                "max_attempts": 3,
                "app_version": "test",
                "protocol_version": "v3",
                "cancel_reason": None,
                "cancelled_by": None,
                "error_code": None,
                "request_payload": {
                    "representado_cuit": "20123456789",
                    "password": "plain-table-secret",
                },
                "response_payload": {
                    "items": [{"token": "plain-response-secret", "total": 1}],
                },
                "response_summary": {"count": 1},
                "response_received_at": "2026-09-24T00:00:00+00:00",
                "credential_metadata": {"fields": ["password"]},
                "created_at": "2026-09-24T00:00:00+00:00",
                "assigned_at": None,
                "started_at": None,
                "finished_at": None,
                "updated_at": "2026-09-24T00:00:00+00:00",
                "artifact_names": ["respuesta.pdf"],
                "artifact_metadata": [{
                    "filename": "respuesta.pdf",
                    "object_key": "private/jobs/respuesta.pdf",
                    "download_url": "https://storage.example/signed?token=secret",
                    "nota": "Ver s3://storage.example/private/descarga",
                }],
                "credential_ciphertext": "never-select-this-column",
            }])

    monkeypatch.setattr(admin_jobs, "db_configurado", lambda: True)
    monkeypatch.setattr(admin_jobs, "nueva_sesion", lambda: FakeSession())
    try:
        cliente = TestClient(create_app())
        response = cliente.get(
            "/admin/records?tabla=bot:ccma&limit=1&offset=1"
            "&operacion=consultar&estado=COMPLETO&usuario=cliente%40example.com"
            "&q=needle",
            headers={"Authorization": "Bearer admin-physical-table-token"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["physical_table"] is True
        assert body["fuente"] == "postgresql"
        assert body["tabla_resuelta"] == "bot:ccma"
        assert body["total"] == 3
        assert body["has_more"] is True
        assert body["next_offset"] == 2
        assert len(body["records"]) == 1
        assert body["columns"] == list(admin_jobs.BOT_TABLE_COLUMNS)
        assert body["display_columns"] == [*admin_jobs.BOT_TABLE_COLUMNS, "usuario_email"]
        row = body["records"][0]
        assert row["usuario_email"] == "cliente@example.com"
        assert row["request_payload"]["representado_cuit"] == "20123456789"
        assert row["request_payload"]["password"] == "[REDACTED]"
        assert row["response_payload"]["items"][0]["token"] == "[REDACTED]"
        # La metadata de credencial se abre a los nombres de campo usados: son
        # los que ya expone la vista canónica, nunca el material sellado.
        assert row["credential_metadata"] == {"fields": ["password"]}
        assert "never-select-this-column" not in json.dumps(body)
        _assert_no_storage_urls(body)
        assert "plain-table-secret" not in response.text
        assert "plain-response-secret" not in response.text
        assert "never-select-this-column" not in response.text
        assert "https://" not in response.text
        assert "s3://" not in response.text
        assert "object_key" not in response.text

        physical_sql = [sql for sql, _ in query_log]
        assert any('public."bot_jobs_ccma"' in sql for sql in physical_sql)
        assert all("credential_ciphertext" not in sql for sql in physical_sql)
        data_queries = [(sql, params) for sql, params in query_log if "COUNT(*)" in sql or "LIMIT :limit" in sql]
        assert data_queries
        assert all(params.get("operacion") == "consultar" for _, params in data_queries)
        assert all(params.get("estado") == "COMPLETO" for _, params in data_queries)
        assert all(params.get("q") == "%needle%" for _, params in data_queries)
        assert all(params.get("bot") == "ccma" for _, params in data_queries)
        assert all(params.get("offset") == 1 for sql, params in data_queries if "LIMIT :limit" in sql)
    finally:
        get_settings.cache_clear()
