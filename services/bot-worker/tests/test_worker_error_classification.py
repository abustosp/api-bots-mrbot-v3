"""La central recibe la categoría del error tipado, no INTERNAL genérico."""

from __future__ import annotations

import pytest
from bot_worker.bots.errors import (
    Categoria,
    InvalidInputError,
    TargetUnavailableError,
)
from bot_worker.main import _resultado_error_de_bot


@pytest.mark.parametrize(
    ("error", "categoria", "tipo"),
    [
        (
            TargetUnavailableError("fallo externo con detalle privado"),
            Categoria.TARGET_UNAVAILABLE,
            "TargetUnavailableError",
        ),
        (
            InvalidInputError("entrada inválida con detalle privado"),
            Categoria.ENVELOPE_INVALID,
            "InvalidInputError",
        ),
    ],
)
def test_error_tipado_conserva_categoria_y_no_expone_diagnostico(
    error, categoria: str, tipo: str
) -> None:
    codigo, resultado = _resultado_error_de_bot(error)

    assert codigo == categoria
    assert resultado == {
        "result": "ERROR",
        "data": {},
        "internal": tipo,
    }
    assert "detalle privado" not in str(resultado)
