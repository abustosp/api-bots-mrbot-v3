from __future__ import annotations

import asyncio
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.mis_retenciones import MisRetencionesPortal, _IMPUESTOS

CUIT_FICTICIO = "20123456789"


class _Loc:
    def __init__(self, texto: str = "", *, visible: bool = True, checked: bool = False) -> None:
        self.texto = texto
        self.visible = visible
        self.checked = checked

    @property
    def first(self) -> _Loc:
        return self

    async def count(self) -> int:
        return int(self.visible)

    async def text_content(self) -> str:
        return self.texto

    async def is_visible(self, **_: Any) -> bool:
        return self.visible

    async def wait_for(self, **_: Any) -> None:
        return None

    async def click(self, **_: Any) -> None:
        return None

    async def is_checked(self) -> bool:
        return self.checked

    async def check(self, **_: Any) -> None:
        self.checked = True

    async def uncheck(self, **_: Any) -> None:
        self.checked = False


class _Page:
    url = "https://portal.invalid/"

    def __init__(self) -> None:
        self.nav = _Loc(CUIT_FICTICIO)

    def locator(self, selector: str, **_: Any) -> _Loc:
        if selector in {
            "#navBarMisRetenciones-relationCuil",
            "#navBarMisRetenciones",
        }:
            return self.nav
        return _Loc(visible=False)

    def get_by_text(self, *_: Any, **__: Any) -> _Loc:
        return _Loc(visible=False)

    def get_by_role(self, *_: Any, **__: Any) -> _Loc:
        return _Loc(visible=False)

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


def test_portal_registrado_y_seleccion_de_representado_activo() -> None:
    assert portal_para("mis_retenciones") is MisRetencionesPortal
    portal = MisRetencionesPortal(_Page())
    asyncio.run(portal.seleccionar_representado(CUIT_FICTICIO))
    assert portal._representado_cuit == CUIT_FICTICIO


def test_cuit_invalido_no_intenta_navegar() -> None:
    portal = MisRetencionesPortal(_Page())
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.seleccionar_representado("123"))
    assert error.value.diagnostic_code == "represented_cuit_invalid"


def test_nombre_impuesto_v2_se_resuelve_por_codigo() -> None:
    assert _IMPUESTOS["217"] == "217 - SICORE-IMPTO.A LAS GANANCIAS"
