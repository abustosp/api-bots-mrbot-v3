"""Selección de representado del portal Libro de Sueldos Digital.

Con más de un representado el servicio abre en ``IndexContribuyente.aspx``
(combo ``ddlCUIT`` + botón ``Aceptar``). Estos tests fijan la resolución de la
opción pedida, la preselección del sitio cuando no se pide un CUIT y el fallo
cerrado cuando el CUIT no está en la lista o falta el botón de confirmación.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from fakes_playwright import Pagina

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals.libro_sueldos_digital import (
    REPRESENTADO_ACEPTAR_SELECTOR,
    REPRESENTADO_SELECT_SELECTOR,
    LibroSueldosDigitalPortal,
)

OPCIONES: list[dict[str, str]] = [
    {
        "value": "30650940667",
        "label": "30-65094066-7 - BUSTOS Y HOPE SOCIEDAD DE HECHO",
    },
    {"value": "33712370829", "label": "33-71237082-9 - KM 0 S.A"},
]


def _portal(
    *,
    opciones: list[dict[str, str]] | None = None,
    con_selector: bool = True,
    con_aceptar: bool = True,
) -> tuple[LibroSueldosDigitalPortal, Pagina]:
    pagina = Pagina(
        url="https://serviciossegsoc.afip.gob.ar/librodesueldos/app/login/IndexContribuyente.aspx"
    )
    if con_selector:
        pagina.poner(f"loc:{REPRESENTADO_SELECT_SELECTOR}")
        pagina.evaluar = lambda _script, _arg: (
            OPCIONES if opciones is None else opciones
        )
    if con_aceptar:
        pagina.poner(f"loc:{REPRESENTADO_ACEPTAR_SELECTOR}")
    portal = LibroSueldosDigitalPortal(
        pagina, service_name="LIBRO DE SUELDOS DIGITAL"
    )
    return portal, pagina


def test_sin_selector_no_interviene() -> None:
    portal, pagina = _portal(con_selector=False)

    assert asyncio.run(portal.seleccionar_representado("30650940667")) == ""
    assert pagina.eventos == []


def test_selecciona_el_cuit_pedido_y_confirma() -> None:
    portal, pagina = _portal()

    elegido = asyncio.run(portal.seleccionar_representado("33-71237082-9"))

    assert elegido == "33712370829"
    assert (
        "select_option",
        f"loc:{REPRESENTADO_SELECT_SELECTOR}",
        "33712370829",
    ) in pagina.eventos
    assert pagina.clicks(f"loc:{REPRESENTADO_ACEPTAR_SELECTOR}") == 1


def test_sin_cuit_pedido_toma_la_preseleccion_del_sitio() -> None:
    portal, pagina = _portal()

    elegido = asyncio.run(portal.seleccionar_representado(""))

    assert elegido == "30650940667"
    assert (
        "select_option",
        f"loc:{REPRESENTADO_SELECT_SELECTOR}",
        "30650940667",
    ) in pagina.eventos
    assert pagina.clicks(f"loc:{REPRESENTADO_ACEPTAR_SELECTOR}") == 1


def test_cuit_ausente_falla_sin_confirmar() -> None:
    portal, pagina = _portal()

    with pytest.raises(TargetUnavailableError) as info:
        asyncio.run(portal.seleccionar_representado("30111111118"))

    assert info.value.diagnostic_code == "represented_cuit_not_selectable"
    assert pagina.clicks(f"loc:{REPRESENTADO_ACEPTAR_SELECTOR}") == 0


def test_selector_sin_opciones_falla() -> None:
    portal, pagina = _portal(opciones=[])

    with pytest.raises(TargetUnavailableError) as info:
        asyncio.run(portal.seleccionar_representado(""))

    assert info.value.diagnostic_code == "represented_cuit_not_selectable"
    assert pagina.eventos == []


def test_falta_boton_aceptar_falla_cerrado() -> None:
    portal, _ = _portal(con_aceptar=False)

    with pytest.raises(TargetUnavailableError) as info:
        asyncio.run(portal.seleccionar_representado("30650940667"))

    assert info.value.diagnostic_code == "represented_cuit_not_confirmed"


def test_el_portal_expone_empleador_actual() -> None:
    from bot_worker.runtime.portals.libro_sueldos_digital import (
        EMPLEADOR_CUIT_SELECTOR,
        EMPLEADOR_DENOMINACION_SELECTOR,
    )

    pagina = Pagina(url="https://serviciossegsoc.afip.gob.ar/librodesueldos/app/Contribuyente/Index.aspx")
    pagina.poner(f"loc:{EMPLEADOR_CUIT_SELECTOR}", texto="30-65094066-7")
    pagina.poner(f"loc:{EMPLEADOR_DENOMINACION_SELECTOR}", texto="BUSTOS Y HOPE SOCIEDAD DE HECHO")
    portal = LibroSueldosDigitalPortal(pagina)

    empleador: dict[str, Any] = asyncio.run(portal.empleador_actual())

    assert empleador == {
        "cuit": "30-65094066-7",
        "denominacion": "BUSTOS Y HOPE SOCIEDAD DE HECHO",
    }
