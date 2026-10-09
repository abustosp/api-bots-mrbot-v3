"""Regresion de la apertura del servicio SIFERE verificada contra el portal real.

Dos defectos solo visibles en vivo (09/10/2026):
1. El portal recorta el titulo de la tarjeta ("... SIFERE WEB - Cons..."), por
   lo que buscar el nombre completo nunca coincidia.
2. La regla que fuerza https en COMARB, instalada antes de buscar la tarjeta,
   impedia que el catalogo del portal se hidratara (0 enlaces).
"""

from __future__ import annotations

import asyncio
import re

from bot_worker.runtime.arca_login import ArcaSession

TITULO_RECORTADO = "Convenio Multilateral – SIFERE WEB - Cons..."


def test_patron_sifere_acepta_titulo_recortado_y_rechaza_ddjj() -> None:
    patron = ArcaSession._PATRONES_SERVICIO_RECORTADO["sifere"]
    assert patron.search(TITULO_RECORTADO)
    assert patron.search("CONVENIO MULTILATERAL – SIFERE WEB - CONSULTAS")
    # La tarjeta de DDJJ es otro servicio y no debe abrirse por error.
    assert not patron.search("Convenio Multilateral – SIFERE WEB - DDJJ")
    assert not patron.search("CONVENIO MULTILATERAL - PADRÓN WEB")


class _Locator:
    def __init__(self, ok: bool) -> None:
        self._ok = ok

    async def count(self) -> int:
        return 1 if self._ok else 0

    def nth(self, _i: int) -> "_Locator":
        return self

    async def is_visible(self) -> bool:
        return True

    def filter(self, **_k: object) -> "_Locator":
        return self


class _Ctx:
    def __init__(self, titulo: str) -> None:
        self._titulo = titulo

    def _hit(self, m: re.Pattern[str]) -> _Locator:
        return _Locator(bool(m.search(self._titulo)))

    def get_by_role(self, _rol: str, name: re.Pattern[str]) -> _Locator:
        return _Locator(False)

    def get_by_text(self, m: re.Pattern[str], exact: bool = False) -> _Locator:
        return self._hit(m)

    def locator(self, _css: str) -> _Locator:
        return _Locator(False)


def test_find_service_con_patron_ubica_la_tarjeta_recortada() -> None:
    sesion = ArcaSession.__new__(ArcaSession)
    ctx = _Ctx(TITULO_RECORTADO)
    sesion._page_contexts = lambda: (ctx,)  # type: ignore[method-assign]
    patron = ArcaSession._PATRONES_SERVICIO_RECORTADO["sifere"]

    async def _buscar() -> tuple[object, object]:
        con = await sesion._find_service("CONVENIO MULTILATERAL – SIFERE WEB - CONSULTAS", patron)
        sin = await sesion._find_service("CONVENIO MULTILATERAL – SIFERE WEB - CONSULTAS")
        return con, sin

    con_patron, sin_patron = asyncio.run(_buscar())
    assert con_patron is not None
    assert sin_patron is None  # el nombre completo no coincide con el titulo recortado


def test_route_https_comarb_no_se_instala_antes_de_ubicar_la_tarjeta() -> None:
    import inspect

    fuente = inspect.getsource(ArcaSession.open_service)
    pos_ruta = fuente.index("await self._asegurar_route_https_comarb()")
    pos_busqueda = fuente.index("self._find_service(value, patron_servicio)")
    assert pos_ruta > pos_busqueda
