from __future__ import annotations

import asyncio
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.errors import CredentialsRejectedError, TargetUnavailableError
from bot_worker.bots.retper_iibb_misiones import plugin as plugin_module
from bot_worker.bots.retper_iibb_misiones.plugin import RetperIibbMisionesPlugin
from bot_worker.bots.retper_iibb_misiones.session import (
    MisionesSession,
    _codigo_rechazo_login,
)

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


MENSAJE_USUARIO = "El nombre de usuario introducido no es correcto."
MENSAJE_USUARIO_CLAVE = (
    "El nombre de usuario o la contraseña introducidos no son correctos"
)


class _LocatorMisiones:
    """Locator mínimo: cuenta y texto configurables por selector."""

    def __init__(self, *, texto: str = "", cuenta: int = 0) -> None:
        self._texto = texto
        self._cuenta = cuenta

    async def count(self) -> int:
        return self._cuenta

    async def fill(self, *_: Any, **__: Any) -> None:
        return None

    async def click(self, *_: Any, **__: Any) -> None:
        return None

    async def is_visible(self) -> bool:
        return False

    async def inner_text(self) -> str:
        return self._texto

    async def text_content(self) -> str:
        return self._texto

    @property
    def first(self) -> "_LocatorMisiones":
        return self

    def nth(self, *_: Any) -> "_LocatorMisiones":
        return self


class _PaginaMisiones:
    """Página ATM falsa: expone formulario, cuerpo con mensaje y sin menús."""

    def __init__(self, texto_cuerpo: str) -> None:
        self._texto = texto_cuerpo

    def _cuenta(self, selector: str) -> int:
        if selector == "input[type='password']":
            return 1
        if selector == "button:has-text('INGRESE CON CLAVE FISCAL')":
            return 1
        return 0

    def locator(self, selector: str) -> _LocatorMisiones:
        if selector == "body":
            return _LocatorMisiones(texto=self._texto, cuenta=1)
        return _LocatorMisiones(cuenta=self._cuenta(selector))

    def get_by_role(self, role: str, **kwargs: Any) -> _LocatorMisiones:
        nombre = getattr(kwargs.get("name"), "pattern", "")
        if role == "button" and "INGRESE" in nombre:
            return _LocatorMisiones(cuenta=1)
        if role == "textbox":
            return _LocatorMisiones(cuenta=1)
        return _LocatorMisiones()

    async def evaluate(self, *_: Any, **__: Any) -> bool:
        return False

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None


def _sesion_misiones(pagina: Any) -> MisionesSession:
    return MisionesSession(
        SimpleNamespace(cuit_representante=CUIT_FICTICIO, clave="clave-ficticia"),
        page=pagina,
        context=object(),
    )


def test_codigo_de_rechazo_distingue_usuario_de_usuario_clave() -> None:
    assert _codigo_rechazo_login(MENSAJE_USUARIO) == "misiones_login_usuario_incorrecto"
    assert (
        _codigo_rechazo_login(MENSAJE_USUARIO_CLAVE)
        == "misiones_login_usuario_o_clave_incorrectos"
    )
    assert _codigo_rechazo_login("Bienvenido a la extranet") is None


def test_rechazo_de_atm_se_clasifica_aunque_falle_el_envio() -> None:
    """El diálogo de ATM bloquea el clic del botón: igual es rechazo, no caída."""
    sesion = _sesion_misiones(_PaginaMisiones(MENSAJE_USUARIO))
    with pytest.raises(CredentialsRejectedError) as error:
        asyncio.run(sesion.ingresar())
    assert error.value.diagnostic_code == "misiones_login_usuario_incorrecto"
    assert MENSAJE_USUARIO not in str(error.value)


def test_rechazo_tardio_no_se_reporta_como_menu_ingresos_brutos() -> None:
    """Si el mensaje aparece después de networkidle, gana el rechazo."""

    class _SesionTardia(MisionesSession):
        async def _enviar_login(self) -> bool:
            return True

        async def _codigo_error_de_login(self) -> str | None:
            self.chequeos = getattr(self, "chequeos", 0) + 1
            return None if self.chequeos < 2 else "misiones_login_usuario_o_clave_incorrectos"

    sesion = _SesionTardia(
        SimpleNamespace(cuit_representante=CUIT_FICTICIO, clave="clave-ficticia"),
        page=_PaginaMisiones(MENSAJE_USUARIO_CLAVE),
        context=object(),
    )
    with pytest.raises(CredentialsRejectedError) as error:
        asyncio.run(sesion.ingresar())
    assert error.value.diagnostic_code == "misiones_login_usuario_o_clave_incorrectos"


def test_sin_mensaje_de_rechazo_el_menu_faltante_sigue_siendo_caida_del_sitio() -> None:
    sesion = _sesion_misiones(_PaginaMisiones("Extranet ATM"))
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(sesion.ingresar())
    assert error.value.diagnostic_code == "misiones_login_submit_unavailable"


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
