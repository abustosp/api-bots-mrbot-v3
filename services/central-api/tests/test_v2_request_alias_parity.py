"""Acceptance checks that replay successful V2 request bodies through V3 aliases."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from central_api.api.bot_compat import BOT_ROUTE_ALIASES
from central_api.api.bot_schemas import get_request_model, normalize_v2_request_body
from central_api.main import create_app

V2_REQUESTS = Path(
    "/home/abp/Desktop/Proyectos Python/Scripts/Mr bot/api/BD-api-v1/requests-data"
)
_FIXTURE_MODULE = {
    "compensaciones": "sct",
    "carga_portal_iva": "portal_iva",
    "mis_retenciones_iva_simple": "mis_retenciones",
    "arba": "retper_iibb_arba",
    "consulta_pagos_vep": "vep",
    "vep_archivo": "vep",
}


def _ok_bodies(bot: str) -> list[tuple[str, dict[str, Any]]]:
    module = _FIXTURE_MODULE.get(bot, bot)
    path = V2_REQUESTS / module / f"{module}.jsonl"
    if not path.is_file():
        return []
    bodies = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except (TypeError, ValueError):
            continue
        body = record.get("body")
        if record.get("expected") == "ok" and isinstance(body, dict):
            bodies.append((str(record.get("path") or ""), body))
    return bodies


def _fixture_body(bot: str, operation: str) -> tuple[dict[str, Any], str]:
    """Use a successful recorded body, filling only fields absent in old captures."""
    model = get_request_model(bot, operation)
    examples = model.model_json_schema().get("examples") or [{}]
    example = dict(examples[0])
    fields = model.model_fields
    credential_aliases = ("clave", "clave_representante", "contrasena", "clave_encriptada")
    candidates: list[tuple[int, dict[str, Any]]] = []
    for source_path, source in _ok_bodies(bot):
        candidate = dict(source)
        for name, field in fields.items():
            if field.is_required() and name not in candidate:
                alternate = next((candidate[key] for key in credential_aliases if key in candidate), None)
                if name in credential_aliases and alternate is not None:
                    candidate[name] = alternate
                elif name in example:
                    candidate[name] = example[name]
        try:
            parsed = model.model_validate(candidate).model_dump()
            normalize_v2_request_body(bot, operation, parsed)
        except (ValueError, TypeError):
            continue
        score = len(set(source) & set(fields))
        path_words = [word for word in source_path.lower().replace("-", "_").split("/") if word]
        score += sum(2 for word in path_words if word in {bot, operation})
        candidates.append((score, candidate))
    if candidates:
        return max(candidates, key=lambda entry: entry[0])[1], "jsonl"
    return example, "openapi-example-fallback"


@pytest.mark.parametrize(
    ("alias_path", "bot", "operation"),
    BOT_ROUTE_ALIASES,
    ids=[path.strip("/").replace("/", "-") for path, _, _ in BOT_ROUTE_ALIASES],
)
def test_v2_successful_request_body_is_accepted_by_v3_alias(
    monkeypatch: pytest.MonkeyPatch,
    alias_path: str,
    bot: str,
    operation: str,
) -> None:
    from central_api.api import bot_routes

    body, source = _fixture_body(bot, operation)
    submitted: list[dict[str, Any]] = []

    async def fake_submit_job(**kwargs: Any) -> JSONResponse:
        submitted.append(kwargs)
        return JSONResponse(
            status_code=202,
            content={"success": True, "job_id": "v2-parity-job", "status": "PENDIENTE"},
        )

    async def no_storage(*args: Any, **kwargs: Any) -> dict[str, Any]:
        # File input persistence is tested separately. Here only request parsing
        # and historical validation are under test.
        return args[2]

    monkeypatch.setattr(bot_routes, "submit_job", fake_submit_job)
    monkeypatch.setattr(bot_routes, "externalizar_archivos", no_storage)
    response = TestClient(create_app()).post(
        f"/api/v3{alias_path}",
        headers={"Idempotency-Key": f"v2-request-parity-{alias_path.strip('/').replace('/', '-') }"},
        json=body,
    )

    assert response.status_code == 202, (alias_path, source, response.text)
    assert len(submitted) == 1
    assert submitted[0]["bot"] == bot
    assert submitted[0]["operacion"] == operation


def test_apoc_successful_v2_get_fixture_maps_to_v3_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """APOC was a GET in V2, so it has no JSON body to replay."""
    from central_api.api import bot_compat

    fixture = V2_REQUESTS / "apoc" / "apoc.jsonl"
    records = [json.loads(line) for line in fixture.read_text(encoding="utf-8").splitlines()]
    success = next(record for record in records if record.get("expected") == "ok")
    assert success.get("body") is None
    called: list[dict[str, Any]] = []

    async def fake_submit_job(**kwargs: Any) -> JSONResponse:
        called.append(kwargs)
        return JSONResponse(
            status_code=202,
            content={"success": True, "job_id": "apoc-v2-parity", "status": "PENDIENTE"},
        )

    monkeypatch.setattr(bot_compat, "submit_job", fake_submit_job)
    response = TestClient(create_app()).get("/api/v3/apoc/consulta/20123456786")
    assert response.status_code == 202, response.text
    assert called and called[0]["bot"] == "apoc"
    assert called[0]["operacion"] == "consultar"


def _post_alias(monkeypatch: pytest.MonkeyPatch, path: str, body: dict[str, Any], key: str):
    from central_api.api import bot_routes

    submitted: list[dict[str, Any]] = []

    async def fake_submit_job(**kwargs: Any) -> JSONResponse:
        submitted.append(kwargs)
        return JSONResponse(status_code=202, content={"success": True, "job_id": "j", "status": "PENDIENTE"})

    async def no_storage(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return args[2]

    monkeypatch.setattr(bot_routes, "submit_job", fake_submit_job)
    monkeypatch.setattr(bot_routes, "externalizar_archivos", no_storage)
    response = TestClient(create_app()).post(
        f"/api/v3{path}", headers={"Idempotency-Key": key}, json=body
    )
    return response, submitted


def test_portal_iva_conserva_descartar_borrador_falso(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = {
        "cuit_representante": "20123456786",
        "clave_representante": "clave",
        "periodo": "012026",
        "descartar_borrador": False,
    }
    response, submitted = _post_alias(monkeypatch, "/portal_iva/consulta", body, "portal-iva-draft")
    assert response.status_code == 202, response.text
    assert submitted[0]["payload"].get("descartar_borrador") is False


def test_portal_iva_accepts_canonical_sales_and_purchases_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from central_api.security.credentials import normalize_v2_payload

    body = {
        "cuit_representante": "20123456786",
        "clave_representante": "clave-de-prueba",
        "cuit_representado": "20123456786",
        "denominacion": "Empresa de prueba",
        "periodo": "202610",
        "descarga_ventas": True,
        "descarga_compras": True,
        "incluir_json": True,
        "subir_csv": True,
    }
    response, submitted = _post_alias(
        monkeypatch, "/portal_iva/consulta", body, "portal-iva-canonical-download-flags"
    )
    assert response.status_code == 202, response.text
    payload = normalize_v2_payload("portal_iva", "descargar", submitted[0]["payload"])
    assert payload["descarga_ventas"] is True
    assert payload["descarga_compras"] is True


def test_portal_iva_historical_csv_flags_remain_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from central_api.security.credentials import normalize_v2_payload

    body = {
        "cuit_representante": "20123456786",
        "clave_representante": "clave-de-prueba",
        "cuit_representado": "20123456786",
        "denominacion": "Empresa de prueba",
        "periodo": "202610",
        "descarga_csv_ventas": True,
        "descarga_csv_compras": True,
        "subir_csv": True,
    }
    response, submitted = _post_alias(
        monkeypatch, "/portal_iva/consulta", body, "portal-iva-historical-download-flags"
    )
    assert response.status_code == 202, response.text
    payload = normalize_v2_payload("portal_iva", "descargar", submitted[0]["payload"])
    assert payload["descarga_ventas"] is True
    assert payload["descarga_compras"] is True


def test_portal_iva_carga_deriva_cuit_representado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body, _ = _fixture_body("carga_portal_iva", "cargar")
    body = {k: v for k, v in body.items() if k not in {"cuit_representado", "representado_cuit"}}
    response, submitted = _post_alias(monkeypatch, "/portal_iva/carga", body, "portal-iva-carga")
    assert response.status_code == 202, response.text
    payload = submitted[0]["payload"]
    assert payload.get("cuit_representado")


@pytest.mark.parametrize(
    ("alias_path", "bot", "operation"),
    BOT_ROUTE_ALIASES,
    ids=[path.strip("/").replace("/", "-") for path, _, _ in BOT_ROUTE_ALIASES],
)
def test_payload_enviado_por_el_alias_lo_valida_el_worker(
    monkeypatch: pytest.MonkeyPatch,
    alias_path: str,
    bot: str,
    operation: str,
) -> None:
    """El payload que la central entrega al worker no puede traer ``None`` en
    opcionales no enviados: el worker valida con tipos estrictos y los rechaza.
    (La validación completa contra el worker se hace con tools/compat_runner.)"""
    body, _source = _fixture_body(bot, operation)
    response, submitted = _post_alias(monkeypatch, alias_path, body, "worker-valida")
    assert response.status_code == 202, response.text
    payload = submitted[0]["payload"]
    assert [k for k, v in payload.items() if v is None] == [], payload
