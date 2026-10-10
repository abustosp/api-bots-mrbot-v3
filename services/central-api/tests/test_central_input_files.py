"""Bucket uploads: clave por bot/cuit/archivo y entrega al worker."""

from __future__ import annotations

import sys
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
    clave = clave_upload(USER_ID, "controladores_fiscales", "20-12345678-9", "../F8010.001.pem")
    assert clave == f"{USER_ID}/controladores_fiscales/20123456789/F8010.001.pem"
    assert ".." not in clave_upload(USER_ID, "x", None, "..").split("/")


def test_ticket_en_bucket_uploads_y_job_con_archivos(monkeypatch) -> None:
    monkeypatch.setattr(uploads_api, "get_settings", lambda: AJUSTES)
    app = create_app()
    app.dependency_overrides[api_dependencies.require_api_principal] = lambda: PRINCIPAL
    try:
        cliente = TestClient(app)
        r = cliente.post(
            "/api/v3/uploads",
            json={
                "bot": "controladores_fiscales", "operacion": "presentar",
                "cuit_representado": "20123456789", "filename": "F8010.001.pem",
                "size_bytes": 10,
            },
        )
        assert r.status_code == 201
        cuerpo = r.json()
        assert cuerpo["bucket"] == "uploads"
        assert cuerpo["object_key"].endswith("/controladores_fiscales/20123456789/F8010.001.pem")
        assert "/uploads/" in cuerpo["upload_url"]

        # Sin referencias validas el job se rechaza.
        malo = cliente.post(
            "/api/v3/controladores-fiscales/carga",
            json={"cuit_representante": "20123456789", "clave": "x", "archivos": ["otra/clave.pem"]},
        )
        assert malo.status_code == 422

        ok = cliente.post(
            "/api/v3/controladores-fiscales/carga",
            json={"cuit_representante": "20123456789", "clave": "x", "archivos": [cuerpo["object_key"]]},
        )
        assert ok.status_code == 202, ok.text
    finally:
        app.dependency_overrides.clear()


def test_preparar_entrada_entrega_nombres_y_urls_get(monkeypatch) -> None:
    import central_api.input_files as mod

    monkeypatch.setattr(mod, "get_settings", lambda: AJUSTES)
    clave = f"{USER_ID}/controladores_fiscales/20123456789/F8010.001.pem"
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
    clave = f"{USER_ID}/vep_archivo/20123456789/vep.txt"
    payload, archivos = preparar_entrada(
        "vep_archivo", "generar", {"archivo_key": clave, "medio_pago": "link"}
    )
    assert "archivo_key" not in payload and payload["medio_pago"] == "link"
    assert archivos[0]["campo"] == "archivo_b64" and archivos[0]["modo"] == "b64"
    assert "/uploads/" in archivos[0]["url"]
    # Sin clave: el base64 inline sigue funcionando sin cambios.
    igual, vacio = preparar_entrada("vep_archivo", "generar", {"archivo_b64": "QQ=="})
    assert igual == {"archivo_b64": "QQ=="} and vacio == []


def test_vep_archivo_acepta_key_de_uploads_y_rechaza_ajena(monkeypatch) -> None:
    monkeypatch.setattr(uploads_api, "get_settings", lambda: AJUSTES)
    app = create_app()
    app.dependency_overrides[api_dependencies.require_api_principal] = lambda: PRINCIPAL
    try:
        cliente = TestClient(app)
        r = cliente.post(
            "/api/v3/uploads",
            json={"bot": "vep_archivo", "operacion": "generar",
                  "cuit_representado": "20123456789", "filename": "vep.txt", "size_bytes": 5},
        )
        clave = r.json()["object_key"]
        base = {"cuit_inicio_sesion": "20123456789", "contrasena": "x", "medio_pago": "link"}
        ok = cliente.post("/api/v3/vep/carga", json={**base, "archivo_key": clave})
        assert ok.status_code == 202, ok.text
        ajena = cliente.post("/api/v3/vep/carga", json={**base, "archivo_key": "otro/x.txt"})
        assert ajena.status_code == 422
    finally:
        app.dependency_overrides.clear()
