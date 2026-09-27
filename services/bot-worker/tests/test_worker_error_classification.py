"""La central recibe la categoría del error tipado, no INTERNAL genérico."""

from __future__ import annotations

import pytest
from bot_worker.bots.errors import (
    Categoria,
    InvalidInputError,
    TargetUnavailableError,
)
from bot_worker.main import _resultado_error_de_bot
from bot_worker.runtime.arca_login import ArcaLoginError


@pytest.mark.parametrize(
    ("error", "categoria", "tipo", "diagnostico"),
    [
        (
            TargetUnavailableError("fallo externo con detalle privado"),
            Categoria.TARGET_UNAVAILABLE,
            "TargetUnavailableError",
            "target_unavailable_unclassified",
        ),
        (
            InvalidInputError("entrada inválida con detalle privado"),
            Categoria.ENVELOPE_INVALID,
            "InvalidInputError",
            None,
        ),
    ],
)
def test_error_tipado_conserva_categoria_y_no_expone_diagnostico(
    error, categoria: str, tipo: str, diagnostico: str | None
) -> None:
    codigo, resultado = _resultado_error_de_bot(error)

    esperado = {"result": "ERROR", "data": {}, "internal": tipo}
    if diagnostico:
        esperado["diagnostic_code"] = diagnostico
    assert codigo == categoria
    assert resultado == esperado
    assert "detalle privado" not in str(resultado)


def test_diagnostic_code_fijo_llega_a_la_central() -> None:
    codigo, resultado = _resultado_error_de_bot(
        TargetUnavailableError("html del sitio", diagnostic_code="represented_cuit_not_selectable")
    )
    assert codigo == Categoria.TARGET_UNAVAILABLE
    assert resultado["diagnostic_code"] == "represented_cuit_not_selectable"
    assert "html del sitio" not in str(resultado)


def test_login_rechazado_conserva_su_codigo() -> None:
    _, resultado = _resultado_error_de_bot(
        ArcaLoginError("pantalla con datos", diagnostic_code="arca_login_still_on_auth_page")
    )
    assert resultado["diagnostic_code"] == "arca_login_still_on_auth_page"


@pytest.mark.parametrize(
    "malo",
    ["Texto Libre con espacios", "clave=secreta123", "<html>", "x" * 80, ""],
)
def test_diagnostic_code_que_no_es_identificador_se_descarta(malo: str) -> None:
    _, resultado = _resultado_error_de_bot(TargetUnavailableError("x", diagnostic_code=malo))
    assert "diagnostic_code" not in resultado


def test_tipo_de_la_causa_se_reporta_sin_su_mensaje() -> None:
    class TimeoutError(Exception):  # imita playwright._impl._errors.TimeoutError
        pass

    try:
        try:
            raise TimeoutError("Locator 'cell' con datos del sitio y cuit 20123456789")
        except TimeoutError as causa:
            raise TargetUnavailableError("x", diagnostic_code="ccma_unclassified_exception") from causa
    except TargetUnavailableError as error:
        _, resultado = _resultado_error_de_bot(error)
    assert resultado["cause"] == "TimeoutError"
    assert "20123456789" not in str(resultado) and "Locator" not in str(resultado)


def test_sin_causa_no_agrega_campo() -> None:
    _, resultado = _resultado_error_de_bot(TargetUnavailableError("x"))
    assert "cause" not in resultado
