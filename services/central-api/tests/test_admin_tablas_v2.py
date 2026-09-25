"""Paridad de Admin/tables con la V2: mismas tablas en PostgreSQL.

Oráculo independiente: los 28 ``__tablename__`` de ``app/models/logs_*.py``
de la V2. La revisión 0016 debe reponerlos como vistas
``consulta_*_logs`` sobre ``bot_jobs_*`` sin columnas de secreto ni de
infraestructura, y el explorador debe resolverlos por nombre.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.admin import jobs as admin_jobs  # noqa: E402
from central_api.main import create_app  # noqa: E402
from central_api.settings import get_settings  # noqa: E402

# Los 28 logs de consulta de la V2 (app/models/logs_*.py).
TABLAS_V2 = (
    "consulta_aportes_en_linea_logs",
    "consulta_ccma_logs",
    "consulta_certificado_mipyme_logs",
    "consulta_controladores_fiscales_logs",
    "consulta_declaracion_en_linea_logs",
    "consulta_facturometro_logs",
    "consulta_hacienda_logs",
    "consulta_libros_iva_logs",
    "consulta_liquidacion_granos_logs",
    "consulta_mc_logs",
    "consulta_mis_facilidades_logs",
    "consulta_mis_retenciones_logs",
    "consulta_mis_retenciones_iva_simple_logs",
    "consulta_moa_logs",
    "consulta_pago_devoluciones_logs",
    "consulta_portal_iva_logs",
    "consulta_portal_iva_carga_logs",
    "consulta_rcel_logs",
    "consulta_retenciones_percepciones_iibb_agip_logs",
    "consulta_retenciones_percepciones_iibb_arba_logs",
    "consulta_retenciones_percepciones_iibb_misiones_logs",
    "consulta_sct_logs",
    "consulta_sct_compensaciones_logs",
    "consulta_sifere_logs",
    "consulta_siper_logs",
    "consulta_srt_logs",
    "consulta_vep_logs",
    "consulta_vep_ccma_logs",
)

EXTRAS_VISTA = ("bot", "operation", "resultado", "request_payload", "response_payload", "created_at")

# Proyecciones que jamás deben existir en una vista de log.
COLUMNAS_PROHIBIDAS = (
    "clave",
    "clave_representante",
    "clave_encriptada",
    "contrasena",
    "password",
    "request_proxy",
    "request_carga_minio",
    "request_excel",
    "request_csv",
    "request_pdf",
    "archivo_path",
    "response_ruta_archivo",
    "object_key",
)


def _migracion_0016():
    ruta = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0016_legacy_log_views.py"
    )
    spec = importlib.util.spec_from_file_location("mig_0016", ruta)
    assert spec is not None and spec.loader is not None
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


@pytest.fixture
def admin(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "admin-tablas-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_revision_0016_cubre_las_28_tablas_v2() -> None:
    mig = _migracion_0016()
    assert mig.revision == "0016"
    assert mig.down_revision == "0015"
    vistas = {vista: (bot, columnas) for vista, bot, columnas in mig.LEGACY_VIEWS}
    assert set(vistas) == set(TABLAS_V2)
    from central_api.api.bots import CATALOGUE

    bots_conocidos = {str(item["bot"]) for item in CATALOGUE}
    for vista, (bot, columnas) in vistas.items():
        assert bot in bots_conocidos, vista
        assert columnas
        assert "job_id" in columnas and "status" in columnas
        assert not (set(columnas) & set(COLUMNAS_PROHIBIDAS)), vista


def test_vistas_sin_proyeccion_de_secretos() -> None:
    mig = _migracion_0016()
    for vista, bot, columnas in mig.LEGACY_VIEWS:
        sql = mig._vista_sql(vista, bot, columnas)
        assert f"FROM bot_jobs_{bot} AS t" in sql
        for prohibida in COLUMNAS_PROHIBIDAS:
            assert prohibida not in sql, f"{vista}: {prohibida}"
        for columna in columnas:
            assert f"AS {columna}" in sql, f"{vista}: {columna}"


def test_catalogo_expone_vistas_legacy() -> None:
    catalogo = admin_jobs._table_catalog()
    legacy = [e for e in catalogo if e["kind"] == "legacy"]
    assert {e["name"] for e in legacy} == set(TABLAS_V2)
    assert "consulta_retenciones_percepciones_iibb_arba_logs" in {
        e["name"] for e in legacy
    }
    mig = _migracion_0016()
    mapa = {vista: (bot, columnas) for vista, bot, columnas in mig.LEGACY_VIEWS}
    for entrada in legacy:
        bot, columnas = mapa[entrada["name"]]
        assert entrada["bot"] == bot
        assert entrada["physical_name"] == entrada["name"]
        assert entrada["columns"] == [*columnas, *EXTRAS_VISTA]
        resuelta = admin_jobs._table_entry(entrada["name"])
        assert resuelta is not None and resuelta["kind"] == "legacy"


def test_records_resuelve_tabla_legacy_sin_db(admin) -> None:
    from central_api.store import JOBS, Job

    trabajo = Job(
        id="018f0000-0000-7000-8000-000000000001",
        bot="mis_comprobantes",
        operation="consulta",
        payload={"fecha_desde": "01/08/2026"},
        status="COMPLETO",
    )
    JOBS[trabajo.id] = trabajo
    try:
        cliente = TestClient(create_app())
        respuesta = cliente.get(
            "/admin/records?tabla=consulta_mc_logs",
            headers={"Authorization": "Bearer admin-tablas-token"},
        )
        assert respuesta.status_code == 200
        cuerpo = respuesta.json()
        assert cuerpo["tabla_resuelta"] == "consulta_mc_logs"
        assert cuerpo["catalogo"]["kind"] == "legacy"
        assert cuerpo["fuente"] == "memoria"
        assert cuerpo["bot_sections"][0]["bot"] == "mis_comprobantes"

        invalida = cliente.get(
            "/admin/records?tabla=no_existe",
            headers={"Authorization": "Bearer admin-tablas-token"},
        )
        assert invalida.status_code == 400
    finally:
        JOBS.pop(trabajo.id, None)


def test_panel_lista_tablas_v2(admin) -> None:
    cliente = TestClient(create_app())
    respuesta = cliente.get("/admin/")
    assert respuesta.status_code == 200
    cuerpo = respuesta.text
    assert "Tablas V1/V2 por bot (consulta_*_logs)" in cuerpo
    assert "Tablas V1/V2" in cuerpo

    catalogo = cliente.get(
        "/admin/table-catalog",
        headers={"Authorization": "Bearer admin-tablas-token"},
    )
    assert catalogo.status_code == 200
    tablas = catalogo.json()["tables"]
    legacy = [t for t in tablas if t["kind"] == "legacy"]
    assert len(legacy) == 28
    assert "consulta_mc_logs (V1/V2)" in {t["label"] for t in legacy}
    assert "consulta_srt_logs (V1/V2)" in {t["label"] for t in legacy}
