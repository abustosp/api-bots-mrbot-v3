from __future__ import annotations

import asyncio
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.errors import CredentialsRejectedError
from bot_worker.bots.retper_iibb_misiones import plugin as plugin_module
from bot_worker.bots.retper_iibb_misiones.plugin import RetperIibbMisionesPlugin
from bot_worker.bots.retper_iibb_misiones.session import MisionesSession

CUIT_FICTICIO = "20123456789"


def test_sesion_misiones_abre_url_dgr_sin_arca() -> None:
    class _Page:
        def __init__(self) -> None:
            self.visitas: list[str] = []

        async def goto(self, url: str, **_: Any) -> None:
            self.visitas.append(url)

    page = _Page()
    session = MisionesSession(
        SimpleNamespace(cuit_representante=CUIT_FICTICIO, clave="clave-ficticia"),
        page=page,
        context=object(),
    )
    url = "https://extranet.atmisiones.gob.ar/Extranet/index.php"
    asyncio.run(session.login(url))
    assert page.visitas == [url]


def test_ingresar_exige_credenciales_fiscales() -> None:
    session = MisionesSession(
        SimpleNamespace(cuit_representante="", clave=""),
        page=object(),
        context=object(),
    )
    with pytest.raises(CredentialsRejectedError):
        asyncio.run(session.ingresar())


def test_mensaje_de_usuario_incorrecto_se_reconoce_como_rechazo() -> None:
    class _Locator:
        def __init__(self, text: str = "") -> None:
            self._text = text

        async def inner_text(self) -> str:
            return self._text

        async def count(self) -> int:
            return 0

    class _Page:
        def locator(self, selector: str) -> _Locator:
            if selector == "body":
                return _Locator("El nombre de usuario introducido no es correcto")
            return _Locator()

    session = MisionesSession(
        SimpleNamespace(cuit_representante=CUIT_FICTICIO, clave="clave-ficticia"),
        page=_Page(),
        context=object(),
    )
    assert asyncio.run(session._hay_error_de_login()) is True


def test_esquema_acepta_denominacion_normalizada_por_central() -> None:
    plugin = RetperIibbMisionesPlugin()
    validado = asyncio.run(
        plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": CUIT_FICTICIO,
                "representado_nombre": "EMPRESA FICTICIA",
                "periodo_desde": "202601",
                "periodo_hasta": "202602",
                "subir_archivo": False,
            }
        )
    )
    assert validado[1].denominacion == "EMPRESA FICTICIA"


def test_execute_usa_contexto_comun_y_sesion_dgr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    llamadas: dict[str, Any] = {}

    class _SesionMisionesFake:
        def __init__(self, credentials: Any, **kwargs: Any) -> None:
            llamadas["credentials"] = credentials
            llamadas["session_args"] = kwargs

        async def login(self, url: str) -> None:
            llamadas["url"] = url

        async def ingresar(self) -> None:
            llamadas["ingresar"] = True

        async def consultar_retper(
            self, *, desde_site: str, hasta_site: str, destino_xlsx: Path
        ) -> None:
            llamadas["periodos"] = (desde_site, hasta_site)
            destino_xlsx.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(destino_xlsx, "w") as libro:
                libro.writestr("xl/workbook.xml", "<workbook/>")

        async def generar_pdf(self, *, destino_pdf: Path) -> None:
            destino_pdf.write_bytes(b"%PDF-" + b"x" * 160)

    class _Context:
        async def new_page(self) -> object:
            return object()

    class _BrowserFactory:
        @asynccontextmanager
        async def new_context(self):
            llamadas["factory"] = "new_context"
            yield object(), _Context()

        def arca_session(self, **_: Any) -> None:
            raise AssertionError("Misiones no debe iniciar una sesion ARCA")

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
        credentials=SimpleNamespace(cuit_representante=CUIT_FICTICIO, clave="secreto-ficticio"),
        cancellation=_Cancellation(),
        event_sink=_Sink(),
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        browser_factory=_BrowserFactory(),
        proxy=None,
        artifact_store=_ArtifactStore(),
    )
    monkeypatch.setattr(plugin_module, "MisionesSession", _SesionMisionesFake)
    plugin = RetperIibbMisionesPlugin()
    entrada = asyncio.run(
        plugin.validate(
            {
                "representado_cuit": CUIT_FICTICIO,
                "denominacion": "EMPRESA FICTICIA",
                "periodo_desde": "202601",
                "periodo_hasta": "202602",
                "subir_archivo": False,
            }
        )
    )

    resultado = asyncio.run(plugin.execute(entrada, runtime))

    assert resultado.result == "OK"
    assert llamadas["factory"] == "new_context"
    assert llamadas["url"] == "https://extranet.atmisiones.gob.ar/Extranet/index.php"
    assert llamadas["ingresar"] is True
    assert llamadas["periodos"] == ("2026/01", "2026/02")
    assert len(resultado.artifacts) == 0
    assert (tmp_path / resultado.data["archivos"][0]).is_file()
    assert (tmp_path / resultado.data["archivos"][1]).read_bytes().startswith(b"%PDF")
