"""Contrato de aliases V2 para la API central V3."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api.bot_compat import BOT_ROUTE_ALIASES  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.store import JOBS  # noqa: E402


def test_todos_los_aliases_de_bots_exponen_triptico_de_job() -> None:
    """Cada ruta V2 de bot queda detrás del submitter central V3."""
    paths = set(create_app().openapi()["paths"])

    assert len(BOT_ROUTE_ALIASES) == 36
    for route_path, _bot, _operation in BOT_ROUTE_ALIASES:
        assert f"/api/v3{route_path}" in paths
        assert f"/api/v3{route_path}/{{job_id}}" in paths
        assert f"/api/v3{route_path}/cancelar/{{job_id}}" in paths


def test_alias_json_crea_job_y_consulta_estado_por_la_misma_central() -> None:
    cliente = TestClient(create_app())
    clave = "compat-ccma-test-001"

    creado = cliente.post(
        "/api/v3/ccma/consulta",
        headers={"Idempotency-Key": clave},
        json={"representado_cuit": "20123456789", "periodo": "202608"},
    )

    assert creado.status_code == 202
    job_id = creado.json()["job_id"]
    assert job_id in JOBS
    assert JOBS[job_id].bot == "ccma"
    assert JOBS[job_id].operation == "consultar"

    estado = cliente.get(f"/api/v3/ccma/consulta/{job_id}")
    assert estado.status_code == 200
    assert estado.json()["job_id"] == job_id
    assert estado.json()["bot"] == "ccma"


def test_alias_reproduce_idempotencia_y_conflicto() -> None:
    cliente = TestClient(create_app())
    clave = "compat-siper-test-001"
    ruta = "/api/v3/siper/consulta"

    primero = cliente.post(
        ruta,
        headers={"Idempotency-Key": clave},
        json={"representado_cuit": "20123456789", "periodo": "202608"},
    )
    repetido = cliente.post(
        ruta,
        headers={"Idempotency-Key": clave},
        json={"representado_cuit": "20123456789", "periodo": "202608"},
    )
    conflicto = cliente.post(
        ruta,
        headers={"Idempotency-Key": clave},
        json={"representado_cuit": "27222222222", "periodo": "202608"},
    )

    assert primero.status_code == 202
    assert repetido.status_code == 202
    assert repetido.json()["job_id"] == primero.json()["job_id"]
    assert conflicto.status_code == 409


def test_apoc_get_historico_se_adapta_a_job_idempotente() -> None:
    cliente = TestClient(create_app())

    respuesta = cliente.get("/api/v3/apoc/consulta/20123456789")

    assert respuesta.status_code == 202
    assert respuesta.json()["job_id"] in JOBS
    assert respuesta.json()["status"] == "PENDIENTE"


def test_alias_no_proxifica_archivos_y_ofrece_flujo_presignado() -> None:
    cliente = TestClient(create_app())

    respuesta = cliente.post(
        "/api/v3/portal_iva/carga",
        files={"archivo": ("ventas.txt", b"contenido", "text/plain")},
        headers={"Idempotency-Key": "compat-portal-file-001"},
    )

    assert respuesta.status_code == 422
    assert "uploads" in respuesta.json()["detail"]["message"]


def test_aliases_libros_iva_y_ddjj_se_mapean_a_operaciones_canonicas_v3() -> None:
    """Las dos descargas legacy se despachan por el plugin productivo V3."""
    from central_api.api.bot_compat import BOT_ROUTE_ALIASES

    esperado = {
        ("/libros_iva/consulta", "libros_portal_iva", "descargar_libros"),
        ("/libros_iva/ddjj", "libros_portal_iva", "descargar_ddjj"),
    }
    assert esperado.issubset(set(BOT_ROUTE_ALIASES))

    cliente = TestClient(create_app())
    payload = {
        "representado_cuit": "20123456789",
        "periodo_desde": "202501",
        "periodo_hasta": "202503",
        "denominacion": "CONTRIBUYENTE DE EJEMPLO",
        "incluir_json": True,
        "subir_archivos": False,
    }
    for path, operation, idempotency_key in (
        ("/api/v3/libros_iva/consulta", "descargar_libros", "legacy-libros-001"),
        ("/api/v3/libros_iva/ddjj", "descargar_ddjj", "legacy-ddjj-001"),
    ):
        response = cliente.post(
            path,
            headers={"Idempotency-Key": idempotency_key},
            json=payload,
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert job_id in JOBS
        assert JOBS[job_id].bot == "libros_portal_iva"
        assert JOBS[job_id].operation == operation
