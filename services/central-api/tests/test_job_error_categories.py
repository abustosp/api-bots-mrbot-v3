import pytest

from central_api.api.dependencies import _project_job_error, _project_operation


@pytest.mark.parametrize("categoria", [
    "ENVELOPE_INVALID", "CREDENTIALS_REJECTED", "CAPTCHA_UNSOLVABLE",
    "TARGET_UNAVAILABLE", "DEADLINE_EXCEEDED", "CHROMIUM_CRASHED", "WORKER_OOM",
    "ARTIFACT_UPLOAD_FAILED", "CANCELLATION_REQUESTED", "INTERNAL",
])
def test_cada_categoria_del_worker_tiene_mensaje_propio(categoria):
    mensajes, detalle = _project_job_error({"error_code": categoria}, "ERROR", {})
    assert detalle["error_code"] == categoria.lower()
    assert detalle["error_code"] != "unexpected"
    assert mensajes and detalle["cause"] and detalle["message"]
    assert "chromium" not in (detalle["cause"] + detalle["message"]).lower()


def test_codigo_desconocido_cae_en_unexpected():
    _, detalle = _project_job_error({"error_code": "algo_raro"}, "ERROR", {})
    assert detalle["error_code"] == "unexpected"


def test_apoc_publica_operation_legacy():
    assert _project_operation("apoc", "consultar") == ("consultar", "consulta")


def test_vep_ccma_publica_operation_legacy_como_v2():
    assert _project_operation("vep_ccma", "generar") == ("generar", "consulta")


def test_siper_sin_banderas_consulta_detalle_y_categorias_como_v2():
    from central_api.security.credentials import normalize_v2_payload

    n = normalize_v2_payload("siper", "consultar", {
        "cuit_representante": "20941773797", "clave": "x",
        "detalle_minio": False, "categorias_minio": False,
    })
    assert n["incluir_detalle"] and n["incluir_categorias"]
    assert n["subir"] is False
    assert n["representado_cuit"] == "20941773797"
