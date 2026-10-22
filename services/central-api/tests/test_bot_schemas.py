"""Contract checks for the public per-bot request schemas."""

from __future__ import annotations

import sys
import json
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
CENTRAL_SRC = REPO_ROOT / "services" / "central-api" / "src"
WORKER_SRC = REPO_ROOT / "services" / "bot-worker" / "src"
sys.path.insert(0, str(WORKER_SRC))
sys.path.insert(0, str(CENTRAL_SRC))

from central_api.api.bot_schemas import get_openapi_examples, get_request_model  # noqa: E402
from central_api.api.bots import CATALOGUE, OPERATIONS  # noqa: E402
from bot_worker.schemas import (  # noqa: E402
    get_openapi_examples as worker_get_openapi_examples,
    get_request_model as worker_get_request_model,
)
from mrbot_contracts.request_fields import V2_OPERATION_FIELDS  # noqa: E402


PAIRS = [
    (str(item["bot"]), str(operation))
    for item in CATALOGUE
    for operation in item["operaciones"]
]


def test_catalogue_pairs_have_operation_specific_fields_and_realistic_examples() -> None:
    assert set(PAIRS) == set(OPERATIONS)
    assert len(PAIRS) == 46

    for bot, operation in PAIRS:
        model = get_request_model(bot, operation)
        assert issubclass(model, BaseModel)
        assert "credentials" not in model.model_fields
        assert all(field.description for field in model.model_fields.values())
        if (bot, operation) in V2_OPERATION_FIELDS and (bot, operation) not in {
            # These are taken directly from the live V2 request classes. The
            # compact worker contract deliberately models file references and
            # omits the portal draft flag, which are not the public request DTO.
            ("portal_iva", "descargar"),
            ("vep_archivo", "generar"),
        }:
            assert tuple(model.model_fields) == V2_OPERATION_FIELDS[(bot, operation)]

        examples = get_openapi_examples(bot, operation)
        assert len(examples) == 1
        assert all({"summary", "description", "value"} <= set(item) for item in examples.values())
        assert set(examples["consulta_habitual"]["value"]) == set(model.model_fields)
        assert len(model.model_json_schema()["examples"]) == 1

        documented = json.dumps(examples, ensure_ascii=False).lower()
        assert "valor-de-ejemplo" not in documented
        assert "archivo-ejemplo" not in documented
        assert "nombre_archivo" not in documented

        # All documented payload examples are also valid model instances.
        for item in examples.values():
            model.model_validate(item["value"])


def test_models_keep_operation_specific_types_and_validations() -> None:
    individual = get_request_model("consulta_cuit", "consultar")
    bulk = get_request_model("consulta_cuit", "consultar_masivo")
    assert individual is not bulk
    assert individual.model_fields["cuit"].annotation is str
    assert bulk.model_fields["cuits"].annotation == list[str]

    apoc = get_request_model("apoc", "consultar")
    apoc.model_validate(
        {
            "cuit": "20123456789",
            "cuit_representante": "20123456789",
            "clave": "DEMO_NO_USAR",
        }
    )
    with pytest.raises(ValidationError):
        apoc.model_validate(
            {
                "cuit": "no-es-cuit",
                "cuit_representante": "20123456789",
                "clave": "DEMO_NO_USAR",
            }
        )

    comprobantes = get_request_model("mis_comprobantes", "consulta")
    assert list(comprobantes.model_fields) == [
        "clave_encriptada", "desde", "hasta", "cuit_inicio_sesion",
        "representado_nombre", "representado_cuit", "contrasena",
        "descarga_emitidos", "descarga_recibidos", "puntos_venta_emitidos",
        "puntos_venta_recibidos", "carga_minio", "carga_json", "timeout_mc",
        "proxy_request",
    ]
    assert comprobantes.model_fields["descarga_emitidos"].annotation is bool
    assert comprobantes.model_fields["puntos_venta_emitidos"].annotation == list[str] | None

    vep_archivo = get_request_model("vep_archivo", "generar")
    assert "archivo_nombre" not in vep_archivo.model_fields
    assert list(vep_archivo.model_fields) == [
        "clave_encriptada", "cuit_inicio_sesion", "medio_pago", "contrasena",
        "minio_upload", "eliminar_descargas", "proxy_request",
    ]
    assert "descartar_borrador" in get_request_model("portal_iva", "descargar").model_fields


def test_unknown_bot_operation_uses_permissive_documented_fallback() -> None:
    model = get_request_model("bot_futuro", "operacion_nueva")
    assert issubclass(model, BaseModel)
    assert model.model_config["extra"] == "allow"
    assert not model.model_fields
    assert len(get_openapi_examples("bot_futuro", "operacion_nueva")) == 1
    assert model.model_validate(
        {"cuit_representante": "20123456789", "clave": "DEMO", "campo_futuro": True}
    ).model_dump()["campo_futuro"] is True


def test_worker_schema_is_independent_and_examples_match_declared_fields() -> None:
    # This is deliberately a cross-service check: the worker package is imported
    # from its own src tree and never imports central_api internally.
    worker_schema_source = (WORKER_SRC / "bot_worker" / "schemas" / "__init__.py").read_text(
        encoding="utf-8"
    )
    assert "from central_api" not in worker_schema_source
    assert "import central_api" not in worker_schema_source

    for bot, operation in PAIRS:
        public_model = get_request_model(bot, operation)
        worker_model = worker_get_request_model(bot, operation)
        assert public_model.model_json_schema()["type"] == "object"
        assert worker_model.model_json_schema()["type"] == "object"
        for examples, model in (
            (get_openapi_examples(bot, operation), public_model),
            (worker_get_openapi_examples(bot, operation), worker_model),
        ):
            assert len(examples) == 1
            for example in examples.values():
                value = example["value"]
                assert set(value) == set(model.model_fields)
                model.model_validate(value)


def test_normalizacion_conserva_campos_que_el_plugin_si_usa() -> None:
    from central_api.security.credentials import normalize_v2_payload

    filtros = [{"periodo": "04/2024", "impuesto": "011"}]
    vep = normalize_v2_payload(
        "vep_ccma", "generar",
        {"filtro_impuestos": filtros, "filtro_intereses": filtros, "medio_pago": "qr"},
    )
    assert vep["filtro_impuestos"] == filtros
    assert vep["filtro_intereses"] == filtros
    mc = normalize_v2_payload("mis_comprobantes", "consultar", {"timeout_mc": 90})
    assert mc["timeout_mc"] == 90
    # Los bots que no usan esos campos siguen descartandolos.
    otro = normalize_v2_payload("ccma", "consultar", {"filtro_impuestos": filtros, "timeout_mc": 5})
    assert "filtro_impuestos" not in otro and "timeout_mc" not in otro
