"""Clasificación de errores de plugin: un defecto propio no es caída del sitio."""
from __future__ import annotations

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.main import _resultado_error_de_bot


def test_metodo_de_servicio_inexistente_se_reporta_como_defecto_interno() -> None:
    """Un plugin que llama a una API no implementada no debe culpar al portal."""
    try:
        try:
            raise AttributeError("'ArcaServicePage' object has no attribute 'listar_planes'")
        except AttributeError as exc:
            raise TargetUnavailableError(
                "falla del organismo: AttributeError",
                diagnostic_code="target_unavailable_unclassified",
            ) from exc
    except TargetUnavailableError as fallo:
        categoria, resultado = _resultado_error_de_bot(fallo)

    assert categoria == "INTERNAL"
    assert resultado["diagnostic_code"] == "plugin_service_api_mismatch"
    assert resultado["cause"] == "AttributeError"
    assert "listar_planes" not in str(resultado)


def test_timeout_del_portal_sigue_siendo_destino_no_disponible() -> None:
    try:
        try:
            raise TimeoutError("sin respuesta")
        except TimeoutError as exc:
            raise TargetUnavailableError(
                "timeout del sitio del organismo",
                diagnostic_code="target_unavailable_unclassified",
            ) from exc
    except TargetUnavailableError as fallo:
        categoria, resultado = _resultado_error_de_bot(fallo)

    assert categoria == "TARGET_UNAVAILABLE"
    assert resultado["cause"] == "TimeoutError"


def test_diagnostico_especifico_del_plugin_se_conserva() -> None:
    try:
        try:
            raise AttributeError("detalle interno")
        except AttributeError as exc:
            raise TargetUnavailableError(
                "representado no seleccionable",
                diagnostic_code="represented_cuit_not_selectable",
            ) from exc
    except TargetUnavailableError as fallo:
        categoria, resultado = _resultado_error_de_bot(fallo)

    assert categoria == "TARGET_UNAVAILABLE"
    assert resultado["diagnostic_code"] == "represented_cuit_not_selectable"
