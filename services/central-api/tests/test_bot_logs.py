"""Contrato de los 27 endpoints ``POST .../logs`` (historial por bot)."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api import bot_logs  # noqa: E402
from central_api.api.bot_compat import BOT_ROUTE_ALIASES  # noqa: E402
from central_api.api.dependencies import (  # noqa: E402
    JOB_META,
    require_api_principal,
)
from central_api.main import create_app  # noqa: E402
from central_api.security.principals import ApiPrincipal  # noqa: E402
from central_api.store import JOBS, Job  # noqa: E402

USER = "logs-user"
OTHER = "logs-other"
SECRET = "super-secreto-clave-fiscal"

EXPECTED_PATHS = {
    "/aportes-en-linea/logs", "/ccma/logs", "/certificado-mipyme/logs",
    "/controladores-fiscales/logs", "/declaracion-en-linea/logs",
    "/facturometro/logs", "/hacienda/logs", "/libros_iva/logs",
    "/liquidacion_granos/logs", "/mis_comprobantes/logs",
    "/mis_facilidades/logs", "/mis_retenciones/logs", "/moa/logs",
    "/pago_devoluciones/logs", "/portal_iva/logs", "/portal_iva/carga/logs",
    "/rcel/logs", "/retenciones_percepciones_iibb/agip/logs",
    "/retenciones_percepciones_iibb/arba/logs",
    "/retenciones_percepciones_iibb/misiones/logs", "/sct/logs",
    "/sct/compensaciones/logs", "/sifere/logs", "/siper/logs",
    "/srt/alicuotas/logs", "/vep-ccma/logs", "/vep/logs",
}


@pytest.fixture(autouse=True)
def _clean_stores():
    JOBS.clear()
    JOB_META.clear()
    yield
    JOBS.clear()
    JOB_META.clear()


def _client(user_id: str = USER) -> TestClient:
    app = create_app()
    app.dependency_overrides[require_api_principal] = lambda: ApiPrincipal(
        user_id=user_id, key_id="logs-test-key"
    )
    return TestClient(app)


def _job(
    job_id: str, bot: str, operation: str, *, status: str = "COMPLETO",
    owner: str = USER, payload: dict | None = None,
    context: dict | None = None, created: datetime | None = None,
    result: dict | None = None, error: dict | None = None,
    credentials: dict | None = None,
) -> Job:
    job = Job(
        id=job_id, bot=bot, operation=operation, payload=payload or {},
        credentials=credentials or {},
        credential_metadata={"fields": ["clave"], "context": context or {}},
        status=status, result=result,
        created_at=created or datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
    )
    JOBS[job_id] = job
    JOB_META[job_id] = {
        "owner": owner, "started_at": None, "finished_at": None,
        "cancel_reason": None, "cancelled_by": None, "error": error,
        "files": [], "data": None, "result": None,
    }
    return job


def test_declarative_table_covers_the_27_v1_log_paths() -> None:
    assert {spec.path for spec in bot_logs.LOG_SPECS} == EXPECTED_PATHS
    assert len(bot_logs.LOG_SPECS) == 27
    paths = create_app().openapi()["paths"]
    for path in EXPECTED_PATHS:
        assert f"/api/v3{path}" in paths
        assert "post" in paths[f"/api/v3{path}"]


def test_table_targets_match_bot_route_aliases_and_v3_prefixes() -> None:
    alias_bots = {bot for _path, bot, _op in BOT_ROUTE_ALIASES}
    alias_ops = {(bot, op) for _path, bot, op in BOT_ROUTE_ALIASES}
    for spec in bot_logs.LOG_SPECS:
        for bot, operations in spec.targets:
            assert bot in alias_bots, spec.path
            for operation in operations or ():
                # mis_comprobantes conserva ``consulta`` como alias histórico.
                assert (
                    (bot, operation) in alias_ops
                    or operation == "consulta"
                    or (bot, operation) == ("libros_portal_iva", "descargar_libros")
                )
    # Cada path /logs comparte prefijo con su ruta de consulta V3.
    prefixes = {path.rpartition("/")[0] for path, _b, _o in BOT_ROUTE_ALIASES}
    # ``/portal_iva/carga`` es a la vez ruta de operación y prefijo del log.
    prefixes |= {path for path, _b, _o in BOT_ROUTE_ALIASES}
    for spec in bot_logs.LOG_SPECS:
        assert spec.path.rpartition("/")[0] in prefixes, spec.path


@pytest.mark.parametrize("path", sorted(EXPECTED_PATHS))
def test_every_path_requires_auth(path: str) -> None:
    """Sin dependency override, un esquema inválido siempre responde 401."""
    client = TestClient(create_app())
    response = client.post(
        f"/api/v3{path}", json={}, headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 401


def test_status_mapping_and_pending_state_v3() -> None:
    _job("0190-a", "ccma", "consultar", status="COMPLETO",
         result={"result": "OK"})
    _job("0190-b", "ccma", "consultar", status="FALLIDO",
         error={"error_code": "navigation"})
    _job("0190-c", "ccma", "consultar", status="CANCELADO")
    for live in ("PENDIENTE", "ASIGNADO", "CORRIENDO"):
        _job(f"0190-{live}", "ccma", "consultar", status=live)
    response = _client().post("/api/v3/ccma/logs", json={})
    assert response.status_code == 200
    by_id = {r["id"]: r for r in response.json()["registros"]}
    assert by_id["0190-a"]["status"] == "success"
    assert by_id["0190-a"]["estado_v3"] == "COMPLETO"
    assert by_id["0190-a"]["resultado"] == "OK"
    assert by_id["0190-b"]["status"] == "error"
    assert by_id["0190-b"]["error_code"] == "navigation"
    assert by_id["0190-b"]["error_message"]
    assert by_id["0190-c"]["status"] == "error"
    assert by_id["0190-c"]["estado_v3"] == "CANCELADO"
    for live in ("PENDIENTE", "ASIGNADO", "CORRIENDO"):
        assert by_id[f"0190-{live}"]["status"] == "pending"
        assert by_id[f"0190-{live}"]["estado_v3"] == live


def test_only_own_jobs_and_only_matching_bot_operation() -> None:
    _job("own", "ccma", "consultar")
    _job("foreign", "ccma", "consultar", owner=OTHER)
    _job("otherbot", "sct", "consultar")
    ids = [r["id"] for r in
           _client().post("/api/v3/ccma/logs", json={}).json()["registros"]]
    assert ids == ["own"]
    assert _client(OTHER).post(
        "/api/v3/ccma/logs", json={}).json()["registros"][0]["id"] == "foreign"


def test_date_filters_hasta_is_inclusive_and_format_validated() -> None:
    base = datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)
    _job("antes", "sct", "consultar", created=base - timedelta(days=1))
    _job("dia", "sct", "consultar", created=base + timedelta(hours=23))
    _job("despues", "sct", "consultar", created=base + timedelta(days=2))
    client = _client()
    body = {"Fecha_desde": "20260910", "Fecha_hasta": "20260910"}
    ids = [r["id"] for r in
           client.post("/api/v3/sct/logs", json=body).json()["registros"]]
    assert ids == ["dia"]
    all_ids = {r["id"] for r in client.post(
        "/api/v3/sct/logs", json={"Fecha_desde": "", "Fecha_hasta": None}
    ).json()["registros"]}
    assert all_ids == {"antes", "dia", "despues"}
    bad = client.post("/api/v3/sct/logs", json={"Fecha_desde": "2026-09-10"})
    assert bad.status_code == 400
    assert bad.json()["detail"]["error_code"] == "validation"


def test_cuit_filters_representante_representado() -> None:
    _job("m1", "ccma", "consultar",
         payload={"representado_cuit": "20111111112"},
         context={"cuit_representante": "20999999990"})
    _job("m2", "ccma", "consultar",
         payload={"representado_cuit": "20222222223"},
         context={"cuit_representante": "20999999990"})
    client = _client()
    got = client.post("/api/v3/ccma/logs", json={
        "Cuit_representante": "20-99999999-0",
        "Cuit_representado": "20111111112"}).json()["registros"]
    assert [r["id"] for r in got] == ["m1"]
    assert got[0]["cuit_representado"] == "20111111112"
    assert got[0]["cuit_representante"] == "20999999990"
    none = client.post("/api/v3/ccma/logs",
                       json={"Cuit_representante": "27000000001"})
    assert none.json()["registros"] == []


def test_siper_ignores_cuit_representado_and_has_no_such_field() -> None:
    _job("s1", "siper", "consultar",
         context={"cuit_representante": "20999999990"},
         payload={"representado_cuit": "20111111112"})
    client = _client()
    got = client.post("/api/v3/siper/logs", json={
        "Cuit_representado": "20000000000"}).json()["registros"]
    assert [r["id"] for r in got] == ["s1"]
    assert "cuit_representado" not in got[0]
    assert "Cuit_representado" not in create_app().openapi()["components"][
        "schemas"]["Logs_request_siper_logs"]["properties"]


def test_agip_filters_only_representado_and_exposes_usuario() -> None:
    _job("a1", "retper_iibb_agip", "consultar",
         payload={"cuit_representado": "20111111112", "desde": "202401",
                  "hasta": "202402", "denominacion": "ACME SA"},
         context={"usuario": "20999999990"})
    client = _client()
    path = "/api/v3/retenciones_percepciones_iibb/agip/logs"
    # Cuit_representante no existe en AGIP: se ignora.
    assert len(client.post(path, json={
        "Cuit_representante": "1"}).json()["registros"]) == 1
    assert client.post(path, json={
        "Cuit_representado": "20111111113"}).json()["registros"] == []
    row = client.post(path, json={
        "Cuit_representado": "20111111112"}).json()["registros"][0]
    assert row["usuario"] == "20999999990"
    assert row["periodo_desde"] == "202401"
    assert row["periodo_hasta"] == "202402"
    assert row["denominacion"] == "ACME SA"


def test_arba_uses_cuit_filter() -> None:
    _job("r1", "arba", "descargar",
         payload={"representado_cuit": "20111111112", "periodo": "202401"})
    _job("r2", "arba", "descargar",
         payload={"representado_cuit": "20222222223", "periodo": "202402"})
    path = "/api/v3/retenciones_percepciones_iibb/arba/logs"
    got = _client().post(path, json={"Cuit": "20111111112"}).json()["registros"]
    assert [r["id"] for r in got] == ["r1"]
    assert got[0]["cuit"] == "20111111112"
    assert got[0]["periodo"] == "202401"


def test_vep_and_vep_ccma_add_medio_pago_filter() -> None:
    _job("v1", "vep_archivo", "generar",
         payload={"medio_pago": "link"},
         context={"cuit_representante": "20999999990"})
    _job("v2", "vep_archivo", "generar", payload={},
         context={"cuit_representante": "20999999990"})
    _job("c1", "vep_ccma", "generar",
         payload={"medio_pago": "xn_group", "cuit_representado": "20111111112"},
         context={"cuit_representante": "20999999990"})
    client = _client()
    link = client.post("/api/v3/vep/logs",
                       json={"Medio_pago": "link"}).json()["registros"]
    assert [r["id"] for r in link] == ["v1"]
    default = client.post("/api/v3/vep/logs", json={
        "Medio_pago": "internet_banking"}).json()["registros"]
    assert [r["id"] for r in default] == ["v2"]
    ccma = client.post("/api/v3/vep-ccma/logs", json={
        "Medio_pago": "xn_group", "Cuit_representado": "20111111112",
    }).json()["registros"]
    assert [r["id"] for r in ccma] == ["c1"]
    assert ccma[0]["medio_pago"] == "xn_group"
    assert client.post("/api/v3/vep-ccma/logs", json={
        "Medio_pago": "link"}).json()["registros"] == []


def test_srt_representado_like_over_csv() -> None:
    _job("t1", "srt", "consultar_alicuotas",
         payload={"cuits_consulta": ["20111111112", "27222222223"]},
         context={"cuit_representante": "20999999990"})
    _job("t2", "srt", "consultar_alicuotas",
         payload={"cuits_consulta": ["30333333339"]},
         context={"cuit_representante": "20999999990"})
    client = _client()
    got = client.post("/api/v3/srt/alicuotas/logs",
                      json={"Cuit_representado": "27-22222222-3"}
                      ).json()["registros"]
    assert [r["id"] for r in got] == ["t1"]
    assert got[0]["cuits_consultados"] == "20111111112,27222222223"
    # LIKE: coincidencia parcial como en V1.
    like = client.post("/api/v3/srt/alicuotas/logs",
                       json={"Cuit_representado": "3333333"}).json()
    assert [r["id"] for r in like["registros"]] == ["t2"]


def test_response_never_leaks_credentials_or_raw_payload() -> None:
    _job("sec", "ccma", "consultar", status="FALLIDO",
         payload={"representado_cuit": "20111111112", "clave": SECRET,
                  "archivo_b64": "AAAA" * 50, "nota_interna": SECRET,
                  "credentials": {"clave": SECRET}},
         context={"cuit_representante": "20999999990", "clave": SECRET},
         credentials={"clave": SECRET},
         result={"result": "ERROR", "token": SECRET},
         error={"error_code": "navigation",
                "message": f"Traceback {SECRET}",
                "detail": SECRET})
    for spec in bot_logs.LOG_SPECS:
        bot, operations = spec.targets[0]
        JOBS["sec"].bot = bot
        JOBS["sec"].operation = (operations or ("consultar",))[0]
        response = _client().post(f"/api/v3{spec.path}", json={})
        assert response.status_code == 200, spec.path
        assert SECRET not in response.text, spec.path
        assert "archivo_b64" not in response.text
        assert "nota_interna" not in response.text
        assert "Traceback" not in response.text
        for record in response.json()["registros"]:
            assert "credentials" not in record and "clave" not in record


def test_unsafe_error_code_is_dropped_and_message_is_public_class() -> None:
    _job("e1", "ccma", "consultar", status="FALLIDO",
         error={"error_code": "<script>alert(1)</script>",
                "message": "boom"})
    row = _client().post("/api/v3/ccma/logs", json={}).json()["registros"][0]
    assert row["error_code"] is None
    assert row["error_message"] == bot_logs.CLASS_MESSAGES["unexpected"]


def test_limit_and_newest_first_ordering() -> None:
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    for index in range(5):
        _job(f"o{index}", "hacienda", "consultar",
             created=base + timedelta(days=index))
    rows = _client().post("/api/v3/hacienda/logs?limit=2",
                          json={}).json()["registros"]
    assert [r["id"] for r in rows] == ["o4", "o3"]


def test_empty_body_is_accepted_and_returns_empty_list() -> None:
    response = _client().post("/api/v3/moa/logs")
    assert response.status_code == 200
    assert response.json() == {"registros": []}


def test_principal_without_read_scope_gets_403() -> None:
    app = create_app()
    app.dependency_overrides[require_api_principal] = lambda: ApiPrincipal(
        user_id=USER, key_id="k", scopes=frozenset({"jobs:create"}))
    response = TestClient(app).post("/api/v3/ccma/logs", json={})
    assert response.status_code == 403


def test_db_branch_selects_explicit_columns_and_maps_rows(monkeypatch) -> None:
    """La rama PostgreSQL nunca pide ``credential_ciphertext``."""
    import uuid
    from types import SimpleNamespace

    from sqlalchemy import and_, or_, select

    from central_api.models.execution import Job as JobModel

    spec = next(s for s in bot_logs.LOG_SPECS if s.path == "/vep/logs")
    statement = bot_logs._db_statement(
        spec, bot_logs.LogFilters(), uuid.uuid4(), JobModel, and_, or_, select)
    sql = str(statement)
    assert "credential_ciphertext" not in sql
    assert "error_message" not in sql
    assert "request_payload" in sql

    item = SimpleNamespace(
        id=uuid.uuid4(), bot="ccma", operation="consultar", status="COMPLETO",
        result="OK", request_payload={"representado_cuit": "20111111112"},
        credential_metadata={"context": {"cuit_representante": "20999999990"}},
        created_at=datetime(2026, 9, 10, tzinfo=timezone.utc), error_code=None,
    )

    class _Result:
        def all(self):
            return [item]

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, _statement):
            return _Result()

    monkeypatch.setattr(bot_logs, "db_configurado", lambda: True)
    monkeypatch.setattr(bot_logs, "nueva_sesion", lambda: _Session())
    user = str(uuid.uuid4())
    response = _client(user).post("/api/v3/ccma/logs", json={
        "Cuit_representado": "20111111112"})
    assert response.status_code == 200
    row = response.json()["registros"][0]
    assert row["id"] == str(item.id)
    assert row["status"] == "success"
    assert row["cuit_representante"] == "20999999990"


def test_db_branch_failure_is_503_not_memory_fallback(monkeypatch) -> None:
    _job("mem", "ccma", "consultar")
    monkeypatch.setattr(bot_logs, "db_configurado", lambda: True)

    def boom():
        raise RuntimeError("db caída con detalle interno")

    monkeypatch.setattr(bot_logs, "nueva_sesion", boom)
    response = _client(str(__import__("uuid").uuid4())).post(
        "/api/v3/ccma/logs", json={})
    assert response.status_code == 503
    assert "detalle interno" not in response.text
    assert "mem" not in response.text
