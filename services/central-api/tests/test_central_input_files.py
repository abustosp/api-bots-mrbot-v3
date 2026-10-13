"""Bucket uploads: clave por bot/cuit/archivo y entrega al worker."""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from central_api.api import dependencies as api_dependencies  # noqa: E402
from central_api.api import uploads as uploads_api  # noqa: E402
from central_api.api.uploads import clave_upload  # noqa: E402
from central_api.input_files import preparar_entrada  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.security.principals import ApiPrincipal  # noqa: E402

USER_ID = "11111111-1111-4111-8111-111111111111"
PRINCIPAL = ApiPrincipal(user_id=USER_ID, key_id="test-key")
AJUSTES = SimpleNamespace(
    object_storage_endpoint="http://minio:9000",
    object_storage_public_endpoint="http://127.0.0.1:9000",
    object_storage_region="us-east-1",
    object_storage_bucket="mrbot",
    object_storage_uploads_bucket="uploads",
    object_storage_access_key="test-access",
    object_storage_secret_key="not-a-real-secret",
    uploads_retention_seconds=3600,
)


def test_clave_upload_por_usuario_bot_cuit_y_archivo() -> None:
    clave = clave_upload(USER_ID, "controladores_fiscales", "20-12345678-6", "../F8010.001.pem")
    assert clave == f"{USER_ID}/controladores_fiscales/20123456786/F8010.001.pem"
    assert ".." not in clave_upload(USER_ID, "x", None, "..").split("/")


def test_ticket_en_bucket_uploads_y_job_con_archivos(monkeypatch) -> None:
    monkeypatch.setattr(uploads_api, "get_settings", lambda: AJUSTES)
    monkeypatch.setattr(uploads_api, "db_configurado", lambda: False)
    monkeypatch.setattr(uploads_api, "object_exists", lambda **kwargs: True)
    monkeypatch.setattr(uploads_api, "get_object_bytes", lambda **kwargs: b"pem content")
    app = create_app()
    app.dependency_overrides[api_dependencies.require_api_principal] = lambda: PRINCIPAL
    try:
        cliente = TestClient(app)
        r = cliente.post(
            "/api/v3/uploads",
            json={
                "bot": "controladores_fiscales", "operacion": "presentar",
                "cuit_representado": "20123456786", "filename": "F8010.001.pem",
                "size_bytes": 10,
            },
        )
        assert r.status_code == 201
        cuerpo = r.json()
        assert cuerpo["bucket"] == "uploads"
        assert cuerpo["object_key"].endswith("/controladores_fiscales/20123456786/F8010.001.pem")
        assert "/uploads/" in cuerpo["upload_url"]

        # Sin referencias validas el job se rechaza.
        malo = cliente.post(
            "/api/v3/controladores-fiscales/carga",
            json={"cuit_representante": "20123456786", "clave": "x", "archivos": ["otra/clave.pem"]},
        )
        assert malo.status_code == 422

        ok = cliente.post(
            "/api/v3/controladores-fiscales/carga",
            json={"cuit_representante": "20123456786", "clave": "x", "archivos": [cuerpo["object_key"]]},
        )
        assert ok.status_code == 202, ok.text
    finally:
        app.dependency_overrides.clear()


def test_preparar_entrada_entrega_nombres_y_urls_get(monkeypatch) -> None:
    import central_api.input_files as mod

    monkeypatch.setattr(mod, "get_settings", lambda: AJUSTES)
    clave = f"{USER_ID}/controladores_fiscales/20123456786/F8010.001.pem"
    payload, archivos = preparar_entrada(
        "controladores_fiscales", "presentar", {"archivos": [clave, clave]}
    )
    assert payload["archivos"] == ["F8010.001.pem", "1_F8010.001.pem"]
    assert all("X-Amz-Signature=" in a["url"] and "/uploads/" in a["url"] for a in archivos)
    sin, vacio = preparar_entrada("ccma", "consultar", {"x": 1})
    assert sin == {"x": 1} and vacio == []


def test_preparar_entrada_key_reemplaza_b64_por_input_file(monkeypatch) -> None:
    import central_api.input_files as mod

    monkeypatch.setattr(mod, "get_settings", lambda: AJUSTES)
    clave = f"{USER_ID}/vep_archivo/20123456786/vep.txt"
    payload, archivos = preparar_entrada(
        "vep_archivo", "generar", {"archivo_key": clave, "medio_pago": "link"}
    )
    assert "archivo_key" not in payload and payload["medio_pago"] == "link"
    assert archivos[0]["campo"] == "archivo_path"
    assert "modo" not in archivos[0]
    assert "/uploads/" in archivos[0]["url"]
    igual, vacio = preparar_entrada("vep_archivo", "generar", {"medio_pago": "link"})
    assert igual == {"medio_pago": "link"} and vacio == []


def test_ticket_de_portal_iva_importar_no_se_reutiliza_en_gestionar(monkeypatch) -> None:
    clave = f"{USER_ID}/portal_iva/20123456786/ventas.txt"
    monkeypatch.setattr(uploads_api, "db_configurado", lambda: False)
    monkeypatch.setattr(uploads_api, "get_settings", lambda: AJUSTES)
    uploads_api.TICKETS.clear()
    uploads_api.TICKETS[clave] = {
        "owner": USER_ID,
        "bot": "portal_iva",
        "operation": "importar",
        "field": "ventas_txt",
        "filename": "ventas.txt",
        "content_type": "text/plain",
        "size_bytes": 4,
        "sha256": None,
        "expires_at": datetime.now(timezone.utc) + timedelta(minutes=10),
        "confirmado": True,
    }

    async def comprobar():
        import pytest

        with pytest.raises(LookupError, match="archivo no encontrado en uploads"):
            await uploads_api.resolver_archivos(
                USER_ID,
                "portal_iva",
                [clave],
                operation="gestionar",
                expected_fields={clave: "ventas_txt"},
            )

    try:
        asyncio.run(comprobar())
    finally:
        uploads_api.TICKETS.clear()


def test_vep_archivo_acepta_key_de_uploads_y_rechaza_ajena(monkeypatch) -> None:
    monkeypatch.setattr(uploads_api, "get_settings", lambda: AJUSTES)
    monkeypatch.setattr(uploads_api, "db_configurado", lambda: False)
    monkeypatch.setattr(uploads_api, "object_exists", lambda **kwargs: True)
    monkeypatch.setattr(uploads_api, "get_object_bytes", lambda **kwargs: b"vep content")
    app = create_app()
    app.dependency_overrides[api_dependencies.require_api_principal] = lambda: PRINCIPAL
    try:
        cliente = TestClient(app)
        r = cliente.post(
            "/api/v3/uploads",
            json={"bot": "vep_archivo", "operacion": "generar",
                  "campo": "archivo", "cuit_representado": "20123456786",
                  "filename": "vep.txt", "size_bytes": 11},
        )
        clave = r.json()["object_key"]
        base = {"cuit_inicio_sesion": "20123456786", "contrasena": "x", "medio_pago": "link"}
        ok = cliente.post("/api/v3/vep/carga", json={**base, "archivo_key": clave})
        assert ok.status_code == 202, ok.text
        ajena = cliente.post("/api/v3/vep/carga", json={**base, "archivo_key": "otro/x.txt"})
        assert ajena.status_code == 422
    finally:
        app.dependency_overrides.clear()
