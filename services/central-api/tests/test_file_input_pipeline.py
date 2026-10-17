from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import urllib.error

from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api import bot_routes, dependencies as api_dependencies  # noqa: E402
from central_api.api import scrapping, utilities, vep_files, uploads as uploads_api  # noqa: E402
from central_api.admin import retention  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.security.principals import ApiPrincipal  # noqa: E402
from central_api.settings import get_settings  # noqa: E402
from central_api import storage  # noqa: E402
from central_api.store import JOBS, Job, utcnow  # noqa: E402

USER_ID = "11111111-1111-4111-8111-111111111111"
PRINCIPAL = ApiPrincipal(user_id=USER_ID, key_id="file-input-test")
SETTINGS = SimpleNamespace(
    object_storage_endpoint="http://storage.invalid:9000",
    object_storage_public_endpoint="http://storage.invalid:9000",
    object_storage_region="us-east-1",
    object_storage_bucket="jobs",
    object_storage_uploads_bucket="uploads",
    object_storage_access_key="unit-access",
    object_storage_secret_key="unit-secret",
    uploads_retention_seconds=3600,
)


def _route_test(monkeypatch):
    stored: dict[str, bytes] = {}
    submitted: list[dict] = []
    persisted: list[dict] = []

    def fake_exists(**kwargs):
        return kwargs["object_key"] in stored

    def fake_get(**kwargs):
        assert kwargs["bucket"] == "uploads"
        return stored[kwargs["object_key"]]

    async def fake_persist(ticket):
        persisted.append(dict(ticket))

    async def fake_submit(**kwargs):
        submitted.append(kwargs)
        return JSONResponse(
            status_code=202,
            content={
                "success": True,
                "job_id": "0190f0c0-7f5b-7b2e-9f7e-123456789abc",
                "status": "PENDIENTE",
            },
        )

    monkeypatch.setattr(uploads_api, "_persist_ticket", fake_persist)
    monkeypatch.setattr(uploads_api, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(uploads_api, "db_configurado", lambda: False)
    monkeypatch.setattr(uploads_api, "object_exists", fake_exists)
    monkeypatch.setattr(uploads_api, "get_object_bytes", fake_get)
    monkeypatch.setattr(bot_routes, "submit_job", fake_submit)
    uploads_api.TICKETS.clear()

    app = create_app()
    app.dependency_overrides[api_dependencies.require_api_principal] = lambda: PRINCIPAL
    return TestClient(app), stored, submitted, persisted, app


def _create_ticket(client, stored, *, bot, operation, field, filename, content, content_type):
    response = client.post(
        "/api/v3/uploads",
        json={
            "bot": bot,
            "operacion": operation,
            "campo": field,
            "cuit_representado": "20123456786",
            "filename": filename,
            "content_type": content_type,
            "size_bytes": len(content),
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # Simulate the client's direct upload into the bucket without network access.
    stored[body["object_key"]] = content
    return body


def test_vep_two_step_upload_reference_is_submitted_as_object_key(monkeypatch) -> None:
    client, stored, submitted, tickets, app = _route_test(monkeypatch)
    contenido = b"vep input"
    try:
        ticket = _create_ticket(
            client, stored, bot="vep_archivo", operation="generar", field="archivo",
            filename="lote.txt", content=contenido, content_type="text/plain",
        )
        response = client.post(
            "/api/v3/vep/carga",
            json={
                "cuit_inicio_sesion": "20-12345678-6",
                "contrasena": "secret",
                "medio_pago": "link",
                "archivo_key": ticket["object_key"],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202, response.text
    assert len(stored) == len(tickets) == 1
    assert tickets[0]["confirmed"] is True
    payload = submitted[0]["payload"]
    assert payload["archivo_key"] == ticket["object_key"]
    assert "archivo_b64" not in payload
    assert submitted[0]["credentials"]["contrasena"] == "secret"


def test_multipart_file_is_rejected_with_file_not_uploaded(monkeypatch) -> None:
    client, _stored, submitted, _tickets, app = _route_test(monkeypatch)
    try:
        response = client.post(
            "/api/v3/vep/carga",
            data={"cuit_inicio_sesion": "20123456786", "contrasena": "secret"},
            files={"archivo_txt": ("lote.txt", b"vep input", "text/plain")},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "file_not_uploaded"
    message = response.json()["detail"]["message"]
    assert "campo archivo_key" in message
    assert "POST /api/v3/uploads" in message
    assert '"archivo_key":"<object_key>"' in message
    assert submitted == []


def test_inline_file_content_fields_are_rejected_with_file_not_uploaded(monkeypatch) -> None:
    client, _stored, submitted, _tickets, app = _route_test(monkeypatch)
    try:
        response = client.post(
            "/api/v3/vep/carga",
            json={"archivo_b64": "QQ==", "medio_pago": "link"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "file_not_uploaded"
    assert "campo archivo_key" in response.json()["detail"]["message"]
    assert submitted == []


def test_unknown_base64_field_reports_the_operation_key_not_a_generic_field(monkeypatch) -> None:
    client, _stored, submitted, _tickets, app = _route_test(monkeypatch)
    try:
        response = client.post(
            "/api/v3/vep/carga",
            json={"content_b64": "QQ==", "medio_pago": "link"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "file_not_uploaded"
    assert "campo archivo_key" in response.json()["detail"]["message"]
    assert submitted == []


def test_carga_portal_iva_accepts_only_tickets_for_each_txt_field(monkeypatch) -> None:
    client, stored, submitted, _tickets, app = _route_test(monkeypatch)
    try:
        comprobantes = _create_ticket(
            client, stored, bot="carga_portal_iva", operation="cargar", field="liv_cbte",
            filename="ventas.txt", content=b"CBTE", content_type="text/plain",
        )
        alicuotas = _create_ticket(
            client, stored, bot="carga_portal_iva", operation="cargar", field="liv_alicuota",
            filename="ventas_alicuotas.txt", content=b"ALIC", content_type="text/plain",
        )
        response = client.post(
            "/api/v3/portal_iva/carga",
            json={
                "cuit_representante": "20123456786",
                "clave_representante": "secret",
                "periodo": "202609",
                "liv_cbte_key": comprobantes["object_key"],
                "liv_alicuota_key": alicuotas["object_key"],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202, response.text
    payload = submitted[0]["payload"]
    assert payload["liv_cbte_key"] == comprobantes["object_key"]
    assert payload["liv_alicuota_key"] == alicuotas["object_key"]
    assert not any("b64" in name.lower() or "base64" in name.lower() for name in payload)


def test_portal_iva_importar_and_gestionar_require_operation_scoped_uploads(monkeypatch) -> None:
    client, stored, submitted, _tickets, app = _route_test(monkeypatch)
    try:
        for operation in ("importar", "gestionar"):
            ventas = _create_ticket(
                client, stored, bot="portal_iva", operation=operation, field="ventas_txt",
                filename="ventas.txt", content=b"ventas", content_type="text/plain",
            )
            alicuotas = _create_ticket(
                client, stored, bot="portal_iva", operation=operation,
                field="ventas_alicuotas_txt", filename="ventas_alicuotas.txt",
                content=b"alicuotas", content_type="text/plain",
            )
            body = {
                "periodo": "202609",
                "representado_cuit": "20123456786",
                "ventas_txt_key": ventas["object_key"],
                "ventas_alicuotas_txt_key": alicuotas["object_key"],
            }
            if operation == "gestionar":
                body["descarga_ventas"] = True
            response = client.post(f"/api/v3/portal_iva/{operation}", json=body)
            assert response.status_code == 202, response.text
            payload = submitted[-1]["payload"]
            assert payload["ventas_txt_key"] == ventas["object_key"]
            assert payload["ventas_alicuotas_txt_key"] == alicuotas["object_key"]
    finally:
        app.dependency_overrides.clear()


def test_portal_iva_txt_inline_is_rejected_with_expected_key(monkeypatch) -> None:
    client, _stored, submitted, _tickets, app = _route_test(monkeypatch)
    try:
        response = client.post(
            "/api/v3/portal_iva/importar",
            json={"periodo": "202609", "ventas_txt": "contenido inline"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "file_not_uploaded"
    assert "campo ventas_txt_key" in response.json()["detail"]["message"]
    assert submitted == []


def test_ticket_with_missing_bucket_object_is_reported_as_deleted(monkeypatch) -> None:
    client, _stored, submitted, _tickets, app = _route_test(monkeypatch)
    try:
        ticket = client.post(
            "/api/v3/uploads",
            json={
                "bot": "vep_archivo", "operacion": "generar", "campo": "archivo",
                "filename": "lote.txt", "content_type": "text/plain", "size_bytes": 8,
            },
        ).json()
        response = client.post(
            "/api/v3/vep/carga",
            json={"archivo_key": ticket["object_key"], "medio_pago": "link"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "objeto eliminado del bucket" in response.json()["detail"]["message"]
    assert submitted == []


def test_object_key_without_ticket_is_reported_as_not_found(monkeypatch) -> None:
    client, _stored, submitted, _tickets, app = _route_test(monkeypatch)
    try:
        response = client.post(
            "/api/v3/vep/carga",
            json={
                "archivo_key": f"{USER_ID}/vep_archivo/sin_cuit/no-existe.txt",
                "medio_pago": "link",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert "archivo no encontrado en uploads" in response.json()["detail"]["message"]
    assert submitted == []


def test_retention_purge_deletes_only_expired_uploads(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "uploads-retention-test-token")
    get_settings.cache_clear()
    monkeypatch.setattr(retention, "db_configurado", lambda: False)
    monkeypatch.setattr(retention, "get_settings", lambda: SETTINGS)
    uploads_api.TICKETS.clear()
    expired_key = f"{USER_ID}/vep_archivo/20123456786/old.txt"
    fresh_key = f"{USER_ID}/vep_archivo/20123456786/fresh.txt"
    uploads_api.TICKETS.update({
        expired_key: {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
        fresh_key: {"expires_at": datetime.now(timezone.utc) + timedelta(hours=1)},
    })
    deleted: list[dict] = []

    def fake_delete(**kwargs):
        deleted.append(kwargs)

    monkeypatch.setattr(retention, "delete_object_bytes", fake_delete)
    client = TestClient(create_app())
    try:
        response = client.post(
            "/admin/retention/purge",
            headers={"Authorization": "Bearer uploads-retention-test-token"},
            json={"retention_days": 90, "dry_run": False, "motivo": "limpieza de archivos vencidos"},
        )
        remaining_tickets = set(uploads_api.TICKETS)
    finally:
        uploads_api.TICKETS.clear()
        get_settings.cache_clear()

    assert response.status_code == 200, response.text
    assert response.json()["uploads"] == {
        "considered": 1,
        "deleted": 1,
        "failed": 0,
        "has_more": False,
        "dry_run": False,
        "error_code": None,
    }
    assert len(deleted) == 1
    assert deleted[0]["bucket"] == "uploads"
    assert deleted[0]["object_key"] == expired_key
    assert fresh_key in remaining_tickets


def test_storage_delete_failure_is_reported_without_blocking_job_purge(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_TOKEN", "uploads-retention-test-token")
    get_settings.cache_clear()
    monkeypatch.setattr(retention, "db_configurado", lambda: False)
    monkeypatch.setattr(retention, "get_settings", lambda: SETTINGS)
    uploads_api.TICKETS.clear()
    expired_key = f"{USER_ID}/vep_archivo/20123456786/retry.txt"
    uploads_api.TICKETS[expired_key] = {
        "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)
    }
    job_id = "01900000-0000-7000-8000-000000000099"
    JOBS[job_id] = Job(
        id=job_id,
        bot="consulta_cuit",
        operation="consultar",
        payload={},
        status="COMPLETO",
        created_at=utcnow() - timedelta(days=120),
    )

    def fail_delete(**_kwargs):
        raise OSError("storage endpoint must not leak")

    monkeypatch.setattr(retention, "delete_object_bytes", fail_delete)
    client = TestClient(create_app())
    try:
        response = client.post(
            "/admin/retention/purge",
            headers={"Authorization": "Bearer uploads-retention-test-token"},
            json={"retention_days": 90, "dry_run": False, "motivo": "purga con storage temporalmente caído"},
        )
        failed_ticket_remains = expired_key in uploads_api.TICKETS
    finally:
        JOBS.pop(job_id, None)
        uploads_api.TICKETS.clear()
        get_settings.cache_clear()

    assert response.status_code == 200, response.text
    assert response.json()["affected_jobs"] == 1
    assert response.json()["uploads"]["failed"] == 1
    assert response.json()["uploads"]["error_code"] == "storage_unavailable"
    assert failed_ticket_remains
    assert "storage endpoint must not leak" not in response.text


def test_signed_storage_delete_uses_uploads_and_treats_missing_object_as_success(monkeypatch) -> None:
    captured = []

    class _Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_urlopen(request, timeout):
        captured.append((request, timeout))
        return _Response()

    monkeypatch.setattr(storage.urllib.request, "urlopen", fake_urlopen)
    storage.delete_object_bytes(
        endpoint="http://storage.invalid:9000",
        region="us-east-1",
        bucket="uploads",
        access_key="test-access",
        secret_key="test-secret",
        object_key="user/vep_archivo/20123456786/old file.txt",
    )
    request, timeout = captured.pop()
    assert request.get_method() == "DELETE"
    assert request.full_url.endswith("/uploads/user/vep_archivo/20123456786/old%20file.txt")
    assert request.get_header("X-amz-content-sha256") == hashlib.sha256(b"").hexdigest()
    assert "Credential=test-access/" in request.get_header("Authorization")
    assert "test-secret" not in request.get_header("Authorization")
    assert timeout == 30.0

    def missing_object(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", hdrs=None, fp=None)

    monkeypatch.setattr(storage.urllib.request, "urlopen", missing_object)
    storage.delete_object_bytes(
        endpoint="http://storage.invalid:9000",
        region="us-east-1",
        bucket="uploads",
        access_key="test-access",
        secret_key="test-secret",
        object_key="user/vep_archivo/20123456786/already-gone.txt",
    )


def test_sync_file_utilities_process_only_the_copy_read_from_uploads(monkeypatch) -> None:
    stored: dict[str, bytes] = {}
    tickets: list[dict] = []
    reads: list[str] = []

    def fake_get(**kwargs):
        assert kwargs["bucket"] == "uploads"
        assert kwargs["max_bytes"] == 52_428_800
        reads.append(kwargs["object_key"])
        assert kwargs["object_key"] in stored
        return stored[kwargs["object_key"]]

    def fake_exists(**kwargs):
        return kwargs["object_key"] in stored

    async def fake_persist(ticket):
        tickets.append(dict(ticket))

    monkeypatch.setattr(uploads_api, "get_settings", lambda: SETTINGS)
    monkeypatch.setattr(uploads_api, "object_exists", fake_exists)
    monkeypatch.setattr(uploads_api, "get_object_bytes", fake_get)
    monkeypatch.setattr(uploads_api, "_persist_ticket", fake_persist)
    monkeypatch.setattr(utilities, "_convert_pem", lambda content, filename: {"copy": content.decode()})
    seen_pdf: list[bytes] = []
    monkeypatch.setattr(utilities, "_pdf_text", lambda content: seen_pdf.append(content) or "invoice text")
    monkeypatch.setattr(scrapping, "_pdf_text", lambda content: seen_pdf.append(content) or "form text")
    seen_xlsx: list[bytes] = []
    monkeypatch.setattr(vep_files, "_xlsx_rows", lambda content: seen_xlsx.append(content) or [])
    monkeypatch.setattr(vep_files, "_zip_from_rows", lambda rows, date: b"zip-result")
    uploads_api.TICKETS.clear()

    app = create_app()
    app.dependency_overrides[api_dependencies.require_api_principal] = lambda: PRINCIPAL
    client = TestClient(app)

    def uploaded_key(*, bot: str, operation: str, field: str, filename: str, content: bytes, content_type: str) -> str:
        response = client.post(
            "/api/v3/uploads",
            json={
                "bot": bot,
                "operacion": operation,
                "campo": field,
                "cuit_representado": "20123456786",
                "filename": filename,
                "content_type": content_type,
                "size_bytes": len(content),
            },
        )
        assert response.status_code == 201, response.text
        object_key = response.json()["object_key"]
        stored[object_key] = b"read from uploads"
        return object_key

    try:
        pem_key = uploaded_key(
            bot="rcel", operation="descargar", field="pem_convertir",
            filename="entrada.pem", content=b"request pem", content_type="application/x-pem-file",
        )
        rcel_key = uploaded_key(
            bot="rcel", operation="descargar", field="rcel_extraer_texto",
            filename="factura.pdf", content=b"request invoice", content_type="application/pdf",
        )
        pdf_key = uploaded_key(
            bot="rcel", operation="descargar", field="scrapping_pdf",
            filename="f2002.pdf", content=b"request form", content_type="application/pdf",
        )
        xlsx_key = uploaded_key(
            bot="vep_archivo", operation="generar", field="archivo_excel",
            filename="lotes.xlsx", content=b"request xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        pem = client.post(
            "/api/v3/utilidades/pem/convertir",
            json={"object_key": pem_key},
        )
        rcel = client.post(
            "/api/v3/utilidades/rcel/extraer-texto",
            json={"object_key": rcel_key},
        )
        pdf = client.post(
            "/api/v3/scrapping/f2002/extraer",
            json={"object_key": pdf_key},
        )
        xlsx = client.post(
            "/api/v3/vep/zip",
            json={"object_key": xlsx_key},
        )
    finally:
        app.dependency_overrides.clear()
        uploads_api.TICKETS.clear()

    assert [pem.status_code, rcel.status_code, pdf.status_code, xlsx.status_code] == [200, 200, 200, 200]
    assert pem.json()["datos"] == {"copy": "read from uploads"}
    assert "B64" not in rcel.json()["extracted_data"][0]
    assert rcel.json()["extracted_data"][0]["AUX"] == "---"
    assert seen_pdf == [b"read from uploads", b"read from uploads"]
    assert seen_xlsx == [b"read from uploads"]
    assert xlsx.content == b"zip-result"
    assert len(tickets) == len(reads) == 4
    assert set(reads) == set(stored)
    assert all(ticket["object_key"] in stored for ticket in tickets)
    assert reads[0] == f"{USER_ID}/rcel/20123456786/entrada.pem"
    assert reads[1] == f"{USER_ID}/rcel/20123456786/factura.pdf"
    assert reads[2] == f"{USER_ID}/rcel/20123456786/f2002.pdf"
    assert reads[3] == f"{USER_ID}/vep_archivo/20123456786/lotes.xlsx"
    assert all(ticket["expires_at"] for ticket in tickets)


def test_signed_storage_get_is_bounded_and_uses_uploads_key(monkeypatch) -> None:
    captured: list = []

    class _Response:
        status = 200

        def __init__(self, body: bytes):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit: int) -> bytes:
            return self.body[:limit]

    def fake_urlopen(request, timeout):
        captured.append((request, timeout))
        return _Response(b"stored copy")

    monkeypatch.setattr(storage.urllib.request, "urlopen", fake_urlopen)
    content = storage.get_object_bytes(
        endpoint="http://storage.invalid:9000",
        region="us-east-1",
        bucket="uploads",
        access_key="test-access",
        secret_key="test-secret",
        object_key="user/rcel/sin_cuit/input.pdf",
        max_bytes=64,
    )
    request, timeout = captured.pop()
    assert content == b"stored copy"
    assert request.get_method() == "GET"
    assert request.full_url.endswith("/uploads/user/rcel/sin_cuit/input.pdf")
    assert "Credential=test-access/" in request.get_header("Authorization")
    assert "test-secret" not in request.get_header("Authorization")
    assert timeout == 30.0

    monkeypatch.setattr(storage.urllib.request, "urlopen", lambda request, timeout: _Response(b"too big"))
    try:
        storage.get_object_bytes(
            endpoint="http://storage.invalid:9000",
            region="us-east-1",
            bucket="uploads",
            access_key="test-access",
            secret_key="test-secret",
            object_key="user/rcel/sin_cuit/input.pdf",
            max_bytes=3,
        )
    except RuntimeError as exc:
        assert "excede" in str(exc)
    else:
        raise AssertionError("GET debe rechazar lecturas por encima del límite")


def test_sync_file_utility_openapi_documents_modes_sizes_and_recovery() -> None:
    schema = create_app().openapi()
    paths = (
        "/api/v3/utilidades/pem/convertir",
        "/api/v3/utilidades/rcel/extraer-texto",
        "/api/v3/scrapping/f2002/extraer",
        "/api/v3/scrapping/f731/extraer",
        "/api/v3/scrapping/f931/extraer",
        "/api/v3/vep/zip",
    )
    for path in paths:
        operation = schema["paths"][path]["post"]
        body = operation["requestBody"]["content"]
        assert "application/json" in body
        assert "multipart/form-data" not in body
        assert body["application/json"]["schema"]["required"] == ["object_key"]
        description = operation["description"]
        assert "50 MiB" in description or "52.428.800 bytes" in description
        assert "POST /api/v3/uploads" in description
        assert '"object_key":"<object_key>"' in description
        for status_code in ("413", "415", "422", "503"):
            assert status_code in operation["responses"]
            assert operation["responses"][status_code]["description"]


def test_openapi_documents_only_uploaded_file_references() -> None:
    schema = create_app().openapi()
    file_paths = (
        "/api/v3/uploads",
        "/api/v3/portal_iva/carga",
        "/api/v3/portal_iva/importar",
        "/api/v3/portal_iva/gestionar",
        "/api/v3/vep/carga",
        "/api/v3/controladores-fiscales/carga",
        "/api/v3/utilidades/pem/convertir",
        "/api/v3/utilidades/rcel/extraer-texto",
        "/api/v3/scrapping/f2002/extraer",
        "/api/v3/scrapping/f731/extraer",
        "/api/v3/scrapping/f931/extraer",
        "/api/v3/vep/zip",
    )

    def check_property_names(value: object) -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                if isinstance(name, str):
                    assert "b64" not in name.lower() and "base64" not in name.lower()
                check_property_names(child)
        elif isinstance(value, list):
            for child in value:
                check_property_names(child)

    for path in file_paths:
        operation = schema["paths"][path]["post"]
        content = operation["requestBody"]["content"]
        assert set(content) == {"application/json"}
        check_property_names(content["application/json"]["schema"])

    def check_documentation_strings(value: object, context: tuple[str, ...] = ()) -> None:
        if isinstance(value, dict):
            for name, child in value.items():
                if name == "clave_encriptada":
                    continue
                check_documentation_strings(child, context + (str(name),))
        elif isinstance(value, list):
            for child in value:
                check_documentation_strings(child, context)
        elif isinstance(value, str):
            lowered = value.lower()
            if "b64" not in lowered and "base64" not in lowered:
                return
            context_text = "/".join(context).lower()
            if "clave-publica" in context_text or "clavepublicarespuesta" in context_text:
                return
            assert "no se aceptan" in lowered and "archivo" in lowered, (context, value)

    check_documentation_strings(schema)


def test_legacy_empty_b64_fields_do_not_trigger_file_not_uploaded(monkeypatch) -> None:
    """Los clientes V1 mandan `b64`/`*_b64`/`carga_s3` vacíos junto al object_key."""
    client, _stored, submitted, _tickets, app = _route_test(monkeypatch)
    try:
        response = client.post(
            "/api/v3/vep/carga",
            json={
                "cuit_inicio_sesion": "20123456786",
                "contrasena": "secret",
                "medio_pago": "link",
                "archivo_b64": "",
                "b64": None,
                "carga_s3": False,
                "incluir_base64": False,
                "carga_b64": False,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 202, response.text
    assert len(submitted) == 1
    payload = submitted[0]["payload"]
    assert "archivo_key" not in payload


def test_legacy_non_empty_b64_field_is_still_rejected(monkeypatch) -> None:
    client, _stored, submitted, _tickets, app = _route_test(monkeypatch)
    try:
        response = client.post(
            "/api/v3/vep/carga",
            json={
                "cuit_inicio_sesion": "20123456786",
                "contrasena": "secret",
                "medio_pago": "link",
                "archivo_b64": "QQ==",
                "b64": None,
                "carga_s3": False,
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422
    assert response.json()["detail"]["error_code"] == "file_not_uploaded"
    assert submitted == []
