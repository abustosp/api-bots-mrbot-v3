from __future__ import annotations

import pytest

from central_api.repositories.base import RepositoryError, assert_no_secretos


@pytest.mark.parametrize(
    "payload",
    [
        {"credentials": {"clave": "redacted"}},
        {"options": [{"PASSWORD": "redacted"}]},
        {"nested": {"token_proveedor": "redacted"}},
        {"items": [{"upload": {"presigned_url": "https://example.invalid"}}]},
    ],
)
def test_assert_no_secretos_rechaza_claves_sensibles_anidadas(payload: dict) -> None:
    with pytest.raises(RepositoryError, match="contiene"):
        assert_no_secretos(payload, "request_payload")


def test_assert_no_secretos_acepta_payload_anidado_no_sensible() -> None:
    assert_no_secretos(
        {"filters": [{"desde": "2026-01-01"}], "include": {"pdf": True}},
        "request_payload",
    )


def test_assert_no_secretos_acepta_payload_vacio_y_none() -> None:
    assert_no_secretos({}, "request_payload")
    assert_no_secretos(None, "request_payload")


def test_sin_nul_limpia_textos_y_claves_anidadas() -> None:
    from central_api.repositories.jobs import _sin_nul

    sucio = {"a\x00": ["x\x00y", {"k": "PK\x03\x04\x00"}], "n": 3}
    assert _sin_nul(sucio) == {"a": ["xy", {"k": "PK\x03\x04"}], "n": 3}
