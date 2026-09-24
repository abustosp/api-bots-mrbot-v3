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
