"""Variante del bot AGIP con pantalla virtual (Xvfb) y el atributo ``pv``.

Cubre tres piezas: la elección por ``pv`` en el registro, el esquema de entrada
(``null``/ausente no cambian nada) y las opciones de lanzamiento de la fábrica
cuando el manifiesto pide pantalla virtual. Todo con dobles: no abre navegadores
ni Xvfb reales.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

import pytest

from bot_worker.bots import registry
from bot_worker.bots.retper_iibb_agip.schema import RetperIibbAgipConsultarInput
from bot_worker.runtime import browser as browser_mod

BASE = "retper_iibb_agip"
VARIANTE = "retper_iibb_agip_xvfb"

PAYLOAD_MINIMO = {
    # ``operacion`` no va: el plugin la separa antes de validar el modelo.
    "representado_cuit": "30716123150",
    "denominacion": "EMPRESA",
    "periodo_desde": "202506",
    "periodo_hasta": "202601",
}


@pytest.mark.parametrize(
    "valor, esperado",
    [
        (None, False),
        (False, False),
        (True, True),
        ("", False),
        ("false", False),
        ("true", True),
        ("1", True),
        ("si", True),
    ],
)
def test_vp_se_interpreta_solo_con_verdadero_explicito(valor: Any, esperado: bool) -> None:
    payload = {} if valor is None and valor is not False else {"pv": valor}
    if valor is None:
        payload = {"pv": None}
    assert registry.pide_pantalla_virtual(payload) is esperado


def test_vp_ausente_o_payload_raro_no_activa_la_variante() -> None:
    assert registry.pide_pantalla_virtual({}) is False
    assert registry.pide_pantalla_virtual(None) is False
    assert registry.pide_pantalla_virtual("pv") is False


def test_la_variante_esta_registrada_y_solo_cambia_el_modo() -> None:
    base = registry.get_plugin(BASE)
    variante = registry.get_plugin(VARIANTE)
    assert base is not None and variante is not None
    assert variante.manifest.nombre == VARIANTE
    assert variante.manifest.pantalla_virtual is True
    assert base.manifest.pantalla_virtual is False
    # Mismo bot para el catálogo: mismas operaciones, mismo costo y mismo esquema.
    assert variante.manifest.operaciones == base.manifest.operaciones
    assert variante.manifest.esquema_entrada == base.manifest.esquema_entrada
    assert variante.manifest.costo_creditos_sugerido == base.manifest.costo_creditos_sugerido
    assert variante.manifest.hosts_permitidos == base.manifest.hosts_permitidos


@pytest.mark.parametrize(
    "payload, esperado",
    [
        ({}, BASE),
        ({"pv": False}, BASE),
        ({"pv": None}, BASE),
        ({"pv": True}, VARIANTE),
        ({"pv": "true"}, VARIANTE),
    ],
)
def test_eleccion_del_plugin_por_payload(payload: dict, esperado: str) -> None:
    plugin = registry.get_plugin_para_payload(BASE, payload)
    assert plugin is not None and plugin.manifest.nombre == esperado


def test_si_la_variante_no_existe_se_usa_el_bot_normal() -> None:
    plugin = registry.get_plugin_para_payload("liquidacion_granos", {"pv": True})
    assert plugin is not None and plugin.manifest.nombre == "liquidacion_granos"


@pytest.mark.parametrize(
    "datos, esperado",
    [
        ({}, False),
        ({"pv": None}, False),
        ({"pv": False}, False),
        ({"pv": True}, True),
        ({"pv": "true"}, True),
    ],
)
def test_el_esquema_acepta_vp_sin_romper_el_default(datos: dict, esperado: bool) -> None:
    entrada = RetperIibbAgipConsultarInput.model_validate({**PAYLOAD_MINIMO, **datos})
    assert entrada.pv is esperado


def test_un_campo_desconocido_sigue_rechazandose() -> None:
    with pytest.raises(ValueError):
        RetperIibbAgipConsultarInput.model_validate({**PAYLOAD_MINIMO, "otro": 1})


def test_display_libre_es_un_display_valido() -> None:
    display = browser_mod._display_libre()
    assert display.startswith(":") and display[1:].isdigit()


def test_asegurar_display_respeta_el_entorno(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISPLAY", ":77")
    assert browser_mod._asegurar_display() == ":77"


def _playwright_falso(monkeypatch: pytest.MonkeyPatch, capturado: dict[str, Any]) -> None:
    import test_browser_stealth as dobles  # el directorio de tests está en sys.path

    dobles._playwright_falso(monkeypatch, capturado)


@pytest.mark.parametrize("pantalla_virtual", [False, True])
def test_la_fabrica_lanza_con_o_sin_display(
    monkeypatch: pytest.MonkeyPatch, pantalla_virtual: bool
) -> None:
    capturado: dict[str, Any] = {}
    _playwright_falso(monkeypatch, capturado)
    monkeypatch.delenv("DISPLAY", raising=False)
    if pantalla_virtual:
        # No se levanta Xvfb en los tests: se dobla el display.
        monkeypatch.setattr(browser_mod, "_asegurar_display", lambda: ":88")
    fabrica = browser_mod.PlaywrightBrowserFactory(pantalla_virtual=pantalla_virtual)

    async def abrir() -> None:
        async with fabrica.new_context() as (_browser, _context):
            pass

    asyncio.run(abrir())

    launch = capturado["launch"]
    if pantalla_virtual:
        assert launch["headless"] is False
        assert launch["env"]["DISPLAY"] == ":88"
        assert os.path.isabs(launch["env"]["XDG_CONFIG_HOME"])
    else:
        assert launch["headless"] is True
        assert "env" not in launch
