"""Regression coverage for V1 file utilities and result-backed log projection."""

from __future__ import annotations

import sys
from pathlib import Path
from zipfile import ZipFile
from io import BytesIO

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api import bot_logs  # noqa: E402
from central_api.api.bot_compat import BOT_ROUTE_ALIASES  # noqa: E402
from central_api.input_files import INPUT_FILE_FIELDS  # noqa: E402
from central_api.main import create_app  # noqa: E402


def test_vep_txt_and_model_are_local_v1_file_endpoints() -> None:
    client = TestClient(create_app())
    response = client.post(
        "/api/v3/vep/txt",
        json={
            "fecha": "20261008",
            "header": {"cuit": "20123456786"},
            "registros": [{
                "nro_form": "20001", "cod_tipo_pago": "1",
                "contribuyente_cuit": "20123456786", "concepto": "019",
                "sub_concepto": "001", "periodo_fiscal": "202610",
                "obligacion_impuesto": "011", "importe": 123.45,
            }],
        },
    )
    assert response.status_code == 200
    assert response.text.startswith("012012345678620001001000030030002")
    assert "attachment; filename=\"F20001.cuit.20123456786.fecha.20261008.nro.001de001.txt\"" == response.headers["content-disposition"]

    model = client.get("/api/v3/vep/modelo")
    assert model.status_code == 200
    assert model.headers["content-type"].startswith("application/vnd.openxmlformats")
    with ZipFile(BytesIO(model.content)) as workbook:
        assert "xl/worksheets/sheet1.xml" in workbook.namelist()


def test_cuit_checksum_and_result_fields_are_projected_without_worker_text() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v3/utilidades/cuit/20123456786").json()["valido"] is True
    assert client.get("/api/v3/utilidades/cuit/20123456789").status_code == 400

    spec = next(item for item in bot_logs.LOG_SPECS if item.path == "/libros_iva/logs")
    row = bot_logs._Row(
        job_id="job-1", bot="libros_portal_iva", operation="descargar_libros",
        status="COMPLETO", created_at=bot_logs.datetime.now(bot_logs.timezone.utc),
        data={"periodos_descargados": ["202601"], "periodos_error": ["202602"]},
        artifacts=[{"filename": "libros.zip"}],
    )
    projected = bot_logs.project_log(row, spec)
    assert projected["periodos_descargados"] == ["202601"]
    assert projected["periodos_error"] == ["202602"]


def test_each_documented_alias_example_is_accepted_by_central() -> None:
    client = TestClient(create_app())
    schema = client.app.openapi()
    for index, (path, bot, operation) in enumerate(BOT_ROUTE_ALIASES):
        if (bot, operation) in INPUT_FILE_FIELDS:
            # Requiere object_key previas de /uploads: ver test_central_input_files.
            continue
        example = schema["paths"][f"/api/v3{path}"]["post"]["requestBody"]["content"]["application/json"]["examples"]["consulta_habitual"]["value"]
        response = client.post(
            f"/api/v3{path}", json=example,
            headers={"Idempotency-Key": f"documented-alias-{index:02d}"},
        )
        assert response.status_code == 202, (path, response.text)
