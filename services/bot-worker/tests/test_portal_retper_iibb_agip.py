from __future__ import annotations

import asyncio
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.errors import CredentialsRejectedError, TargetUnavailableError
from bot_worker.bots.retper_iibb_agip import plugin as plugin_module
from bot_worker.bots.retper_iibb_agip.plugin import RetperIibbAgipPlugin
from bot_worker.bots.retper_iibb_agip.session import (
    AgipSession,
    _empaquetar_descargas,
    _periodo_valido,
)

CUIT_FICTICIO = "20123456789"


def test_periodo_agip_y_empaquetado_de_descargas(tmp_path: Path) -> None:
    assert _periodo_valido("01/2026")
    assert not _periodo_valido("13/2026")
    assert not _periodo_valido("202601")

    origen = tmp_path / "reporte.txt"
    origen.write_bytes(b"dato AGIP\n")
    destino = tmp_path / "reporte.zip"
    _empaquetar_descargas([origen], destino)

    assert zipfile.is_zipfile(destino)
    with zipfile.ZipFile(destino) as paquete:
        assert paquete.namelist() == ["reporte.txt"]
        assert paquete.read("reporte.txt") == b"dato AGIP\n"


def test_no_empaqueta_sin_archivos(tmp_path: Path) -> None:
    with pytest.raises(TargetUnavailableError) as error:
        _empaquetar_descargas([], tmp_path / "vacio.zip")
    assert error.value.diagnostic_code == "agip_download_missing"


def test_sesion_agip_abre_url_sin_dependencia_de_arca() -> None:
    class _Page:
        def __init__(self) -> None:
            self.visited: list[str] = []

        async def goto(self, url: str, **_: Any) -> None:
            self.visited.append(url)

    page = _Page()
    sesion = AgipSession(SimpleNamespace(clave="secreto-ficticio"), page=page)
    asyncio.run(sesion.login("https://claveciudad.agip.gob.ar/"))
    assert page.visited == ["https://claveciudad.agip.gob.ar/"]


def test_bloqueo_waf_se_reporta_como_bloqueo_externo() -> None:
    class _Page:
        async def evaluate(self, *_: Any) -> bool:
            return True

    sesion = AgipSession(SimpleNamespace(clave="secreto-ficticio"), page=_Page())
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(sesion._open_login_gateway())
    assert error.value.diagnostic_code == "agip_waf_blocked"


def test_rechazo_inline_de_clave_agip_es_error_de_credenciales() -> None:
    class _Boton:
        async def click(self) -> None:
            return None

    class _Page:
        def get_by_role(self, *_: Any, **__: Any) -> _Boton:
            return _Boton()

        async def wait_for_function(self, *_: Any, **__: Any) -> None:
            return None

        async def evaluate(self, *_: Any, **__: Any) -> dict[str, bool]:
            return {"representados": False, "error": True}

    sesion = AgipSession(SimpleNamespace(clave="secreto-ficticio"), page=_Page())
    with pytest.raises(CredentialsRejectedError):
        asyncio.run(sesion._submit_login())


def test_validate_incluye_usuario_agip_sin_mezclarlo_con_credenciales() -> None:
    plugin = RetperIibbAgipPlugin()
    validado = asyncio.run(
        plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": CUIT_FICTICIO,
                "usuario": "cuenta@example.invalid",
                "denominacion": "EMPRESA FICTICIA",
                "periodo_desde": "202601",
                "periodo_hasta": "202602",
                "subir_archivo": False,
            }
        )
    )
    assert validado[1].usuario == "cuenta@example.invalid"
    assert validado[1].periodo_desde == "202601"
    assert plugin.manifest.artefactos_produce[0].content_types == ("application/zip",)


def test_execute_usa_contexto_comun_y_sesion_agip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invocaciones: dict[str, Any] = {}

    class _SesionAgipFake:
        def __init__(self, credentials: Any, *, page: Any) -> None:
            invocaciones["credentials"] = credentials
            invocaciones["page"] = page

        async def login(self, url: str) -> None:
            invocaciones["url"] = url

        async def ingresar(self, usuario: str, cuit_representado: str) -> None:
            invocaciones["login"] = (usuario, cuit_representado)

        async def consultar_retper(
            self, desde_mmyyyy: str, hasta_mmyyyy: str, destino: Path
        ) -> None:
            invocaciones["consulta"] = (desde_mmyyyy, hasta_mmyyyy)
            destino.write_bytes(b"zip de prueba")

        async def close(self) -> None:
            invocaciones["closed"] = True

    class _Context:
        async def new_page(self) -> object:
            return object()

    class _BrowserFactory:
        @asynccontextmanager
        async def new_context(self):
            invocaciones["factory"] = "new_context"
            yield object(), _Context()

    class _ArtifactStore:
        def resolve(self, name: str) -> Path:
            return tmp_path / name

    class _Sink:
        async def progress(self, **_: Any) -> None:
            return None

    class _Cancellation:
        async def raise_if_cancelled(self) -> None:
            return None

    runtime = SimpleNamespace(
        credentials=SimpleNamespace(cuit_representante="", clave="secreto-ficticio"),
        cancellation=_Cancellation(),
        event_sink=_Sink(),
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        browser_factory=_BrowserFactory(),
        proxy=None,
        artifact_store=_ArtifactStore(),
    )
    monkeypatch.setattr(plugin_module, "AgipSession", _SesionAgipFake)
    plugin = RetperIibbAgipPlugin()
    entrada = asyncio.run(
        plugin.validate(
            {
                "representado_cuit": CUIT_FICTICIO,
                "usuario": "cuenta@example.invalid",
                "denominacion": "EMPRESA FICTICIA",
                "periodo_desde": "202601",
                "periodo_hasta": "202601",
                "subir_archivo": False,
            }
        )
    )
    resultado = asyncio.run(plugin.execute(entrada, runtime))

    assert resultado.result == "OK"
    assert invocaciones["factory"] == "new_context"
    assert invocaciones["login"] == ("cuenta@example.invalid", CUIT_FICTICIO)
    assert invocaciones["consulta"] == ("01/2026", "01/2026")
    assert invocaciones["closed"] is True
    assert Path(runtime.artifact_store.resolve(resultado.data["archivo"])).is_file()
