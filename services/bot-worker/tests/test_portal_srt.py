from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.srt.plugin import _configurar_captcha_srt
from bot_worker.runtime.portals.srt import SrtPortal


class _Locator:
    def __init__(self, page: "_Page", selector: str) -> None:
        self.page = page
        self.selector = selector
        self.first = self

    async def count(self) -> int:
        if "LoguearRespresentado" in self.selector:
            return int(not self.page.representado)
        if self.selector == '[id="11"]':
            return int(self.page.representado)
        if self.selector == "#txtCuilCuit":
            return int(self.page.consulta_abierta)
        return 0

    async def is_visible(self, **_: object) -> bool:
        return bool(await self.count())

    async def click(self, **_: object) -> None:
        if "LoguearRespresentado" in self.selector:
            self.page.eventos.append("representado")
            self.page.representado = True
        elif self.selector == '[id="11"]':
            self.page.eventos.append("consulta")
            self.page.consulta_abierta = True
            self.page.url = "https://eservicios.srt.gob.ar/Consultas/Alicuotas/Default.aspx"
        else:
            raise AssertionError(f"click inesperado: {self.selector}")


class _Page:
    def __init__(self, *, con_enlace: bool = True) -> None:
        self.url = "https://eservicios.srt.gob.ar/Servicios.aspx"
        self.eventos: list[str] = []
        self.representado = False
        self.consulta_abierta = False
        self.con_enlace = con_enlace
        self.goto_calls: list[str] = []

    def locator(self, selector: str, **_: object) -> _Locator:
        if selector == '[id="11"]' and not self.con_enlace:
            return _LocatorSinResultado(self, selector)
        return _Locator(self, selector)

    def get_by_role(self, role: str, **kwargs: object) -> _Locator:
        return _Locator(self, f"role={role} {kwargs}")

    async def wait_for_load_state(self, *_: object, **__: object) -> None:
        return None

    async def wait_for_url(self, *_: object, **__: object) -> None:
        return None

    async def wait_for_timeout(self, *_: object, **__: object) -> None:
        return None

    async def goto(self, url: str, **_: object) -> None:
        self.goto_calls.append(url)


class _LocatorSinResultado(_Locator):
    async def count(self) -> int:
        return 0


def test_preparar_selecciona_representado_y_sigue_enlace_interno_en_orden() -> None:
    async def escenario() -> None:
        page = _Page()
        portal = SrtPortal(page)
        portal._esperar = AsyncMock()  # type: ignore[method-assign]

        await portal.preparar()

        assert page.eventos == ["representado", "consulta"]
        assert page.url.endswith("/Consultas/Alicuotas/Default.aspx")
        assert page.goto_calls == []

    asyncio.run(escenario())


def test_no_navega_directo_si_el_enlace_del_portal_no_esta() -> None:
    async def escenario() -> None:
        page = _Page(con_enlace=False)
        portal = SrtPortal(page)
        portal._esperar = AsyncMock()  # type: ignore[method-assign]
        portal._primero_visible = AsyncMock(return_value=None)  # type: ignore[method-assign]

        with pytest.raises(TargetUnavailableError) as error:
            await portal.abrir_consulta()

        assert error.value.diagnostic_code == "srt_query_link_missing"
        assert page.goto_calls == []

    asyncio.run(escenario())


def test_plugin_conecta_capmonster_srt_y_no_el_perfil_arca() -> None:
    portal = SrtPortal(_Page())
    resolvedor_arca = object()
    resolvedor_srt = object()

    class _Factory:
        _solvers = {"arca": resolvedor_arca, "srt": resolvedor_srt}

    _configurar_captcha_srt(portal, _Factory())

    assert portal._captcha is resolvedor_srt


# --- Paridad V1: estado por CUIT y lote que sigue ante un fallo -------------


def _lote_srt(consultas_por_cuit: dict[str, object], cuits: list[str]) -> tuple[dict, list]:
    from types import SimpleNamespace

    from bot_worker.bots.srt.plugin import SrtPlugin

    class _Servicio:
        async def consultar_cuit(self, cuit: str) -> dict:
            valor = consultas_por_cuit[cuit]
            if isinstance(valor, Exception):
                raise valor
            return valor  # type: ignore[return-value]

    class _Store:
        def resolve(self, nombre: str):  # pragma: no cover - ruta temporal
            import tempfile
            from pathlib import Path

            return Path(tempfile.mkdtemp()) / nombre

        async def upload(self, artifact_id: str, nombre: str) -> dict:
            return {"sha256": "abc", "size_bytes": 1}

    async def _no_op(*_: object, **__: object) -> None:
        return None

    async def _correr() -> tuple[dict, list]:
        _, entrada = await SrtPlugin().validate({
            "operacion": "consultar_alicuotas",
            "cuits_consulta": cuits,
            "subir_json": False,
        })
        runtime = SimpleNamespace(
            cancellation=SimpleNamespace(raise_if_cancelled=_no_op),
            event_sink=SimpleNamespace(progress=_no_op),
            artifact_store=_Store(),
        )
        datos, _, errores = await SrtPlugin()._consultar_lote(_Servicio(), entrada, runtime)
        return datos, errores

    return asyncio.run(_correr())


def test_un_cuit_con_error_no_corta_el_lote() -> None:
    from bot_worker.bots.errors import TargetUnavailableError

    datos, errores = _lote_srt(
        {
            "20123456789": {"status": "OK", "message": None, "data": [{"t": 1}]},
            "27111222333": TargetUnavailableError(
                "no se pudo ejecutar la consulta en el portal SRT",
                diagnostic_code="srt_query_failed",
            ),
            "23444555666": {"status": "SIN_AFILIACION_VIGENTE", "message": "x", "data": None},
        },
        ["20123456789", "27111222333", "23444555666"],
    )

    # Los tres CUIT quedan en el resultado: el del medio como ERROR.
    estados = [(c["cuit"], c["status"]) for c in datos["consultas"]]
    assert estados == [
        ("20123456789", "OK"),
        ("27111222333", "ERROR"),
        ("23444555666", "SIN_AFILIACION_VIGENTE"),
    ]
    assert datos["consultas_ok"] == 2
    assert datos["consultas_error"] == 1
    assert len(errores) == 1
    assert "srt_query_failed" in errores[0].internal_diagnostic


def test_lote_sin_fallos_no_reporta_errores() -> None:
    datos, errores = _lote_srt(
        {"20123456789": {"status": "OK", "message": None, "data": []}},
        ["20123456789"],
    )

    assert errores == []
    assert datos["consultas_error"] == 0
    assert datos["consultas_ok"] == 1
