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


class _BotonQueCuenta:
    def __init__(self, estado: dict[str, Any]) -> None:
        self._estado = estado

    async def click(self, **_: Any) -> None:
        self._estado["clics"] += 1


class _PortadaConBloqueo:
    """Portada AGIP falsa que responde bloqueada las veces indicadas.

    ``bloqueos[i]`` dice si la página viva lleva el marcador del perímetro
    antes del clic ``i``. Cuenta navegaciones, esperas y clics.
    """

    def __init__(self, bloqueos: list[bool]) -> None:
        self._bloqueos = list(bloqueos)
        self.estado: dict[str, Any] = {"gotos": [], "esperas": [], "clics": 0}

    async def goto(self, url: str, **_: Any) -> None:
        self.estado["gotos"].append(url)

    async def wait_for_timeout(self, milisegundos: int) -> None:
        self.estado["esperas"].append(milisegundos)

    async def wait_for_function(self, *_: Any, **__: Any) -> None:
        return None

    async def evaluate(self, *_: Any) -> bool:
        return self._bloqueos.pop(0) if self._bloqueos else False

    def get_by_role(self, *_: Any, **__: Any) -> _BotonQueCuenta:
        return _BotonQueCuenta(self.estado)


PORTADA_AGIP = "https://claveciudad.agip.gob.ar/"


def test_portada_sin_bloqueo_no_reintenta() -> None:
    pagina = _PortadaConBloqueo([False])
    sesion = _sesion_agip(pagina)
    asyncio.run(sesion.login(PORTADA_AGIP))
    asyncio.run(sesion._abrir_login_de_portada())
    assert pagina.estado["gotos"] == [PORTADA_AGIP]
    assert pagina.estado["esperas"] == []
    assert pagina.estado["clics"] == 1


def test_portada_bloqueada_se_reintenta_y_recupera() -> None:
    pagina = _PortadaConBloqueo([True, False])
    sesion = _sesion_agip(pagina)
    asyncio.run(sesion.login(PORTADA_AGIP))
    asyncio.run(sesion._abrir_login_de_portada())
    # Un reintento: espera creciente y navegación nueva antes del clic.
    assert pagina.estado["esperas"] == [5_000]
    assert pagina.estado["gotos"] == [PORTADA_AGIP, PORTADA_AGIP]
    assert pagina.estado["clics"] == 1


def test_portada_bloqueada_persistente_reporta_bloqueo_externo() -> None:
    pagina = _PortadaConBloqueo([True, True, True, True, True])
    sesion = _sesion_agip(pagina)
    asyncio.run(sesion.login(PORTADA_AGIP))
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(sesion._abrir_login_de_portada())
    assert error.value.diagnostic_code == "agip_waf_blocked"
    assert pagina.estado["esperas"] == [5_000, 10_000, 15_000, 15_000]
    assert pagina.estado["gotos"] == [PORTADA_AGIP] * 5
    assert pagina.estado["clics"] == 0


def test_reintento_de_portada_avisa_al_job_para_conservar_la_lease() -> None:
    pagina = _PortadaConBloqueo([True, True, False])
    avisos: list[tuple[int, int, int]] = []

    async def _avisar(intento: int, total: int, espera_ms: int) -> None:
        avisos.append((intento, total, espera_ms))

    sesion = AgipSession(
        SimpleNamespace(clave="secreto-ficticio"),
        page=pagina,
        avisar_reintento=_avisar,
    )
    asyncio.run(sesion.login(PORTADA_AGIP))
    asyncio.run(sesion._abrir_login_de_portada())
    # Un aviso por cada espera, con el tope de intentos y el backoff vigente.
    assert avisos == [(1, 5, 5_000), (2, 5, 10_000)]
    assert pagina.estado["clics"] == 1


class _PaginaServicioFiltrada:
    """Servicio que queda en la página del filtro perimetral de AGIP."""

    async def wait_for_selector(self, selector: str, **_: Any) -> None:
        if selector == "#fechaDesdeCo":
            raise TimeoutError("Timeout 60000ms exceeded")
        return None

    async def evaluate(self, *_: Any) -> bool:
        return True


def test_servicio_filtrado_no_se_reporta_como_timeout_del_sitio() -> None:
    sesion = _sesion_agip(_PaginaServicioFiltrada())
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(sesion._open_service(CUIT_FICTICIO))
    assert error.value.diagnostic_code == "agip_waf_blocked"


class _PaginaSinResultados:
    """Espera de resultados que nunca se cumple."""

    def __init__(self, bloqueada: bool) -> None:
        self._bloqueada = bloqueada

    async def wait_for_function(self, *_: Any, **__: Any) -> None:
        raise TimeoutError("Timeout 60000ms exceeded")

    async def evaluate(self, *_: Any, **__: Any) -> bool:
        return self._bloqueada


def test_resultados_filtrados_se_reportan_como_bloqueo_externo() -> None:
    sesion = _sesion_agip(_PaginaSinResultados(True))
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(sesion._esperar_resultados())
    assert error.value.diagnostic_code == "agip_waf_blocked"


def test_resultados_sin_bloqueo_siguen_siendo_timeout_del_sitio() -> None:
    sesion = _sesion_agip(_PaginaSinResultados(False))
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(sesion._esperar_resultados())
    assert error.value.diagnostic_code == "agip_results_timeout"


def test_cuit_representado_invalido_no_llega_al_portal() -> None:
    sesion = AgipSession(SimpleNamespace(clave="secreto-ficticio"), page=object())
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(sesion.ingresar(usuario="cuenta@example.invalid", cuit_representado="123"))
    assert error.value.diagnostic_code == "agip_represented_cuit_invalid"


class _SelectorSinOpcion:
    """Select de representados donde el CUIT pedido no existe."""

    @property
    def first(self) -> "_SelectorSinOpcion":
        return self

    async def wait_for(self, **_: Any) -> None:
        return None

    async def select_option(self, **_: Any) -> None:
        raise TimeoutError("no existe una opción con ese value")


class _PaginaSinRepresentado:
    def locator(self, *_: Any, **__: Any) -> _SelectorSinOpcion:
        return _SelectorSinOpcion()


def test_cuit_no_representado_se_reporta_como_no_seleccionable() -> None:
    sesion = _sesion_agip(_PaginaSinRepresentado())
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(sesion._select_contributor(CUIT_FICTICIO))
    assert error.value.diagnostic_code == "agip_represented_cuit_not_selectable"


class _BotonAgip:
    def __init__(self, registro: dict[str, Any] | None = None) -> None:
        self._registro = registro

    async def click(self, **kwargs: Any) -> None:
        if self._registro is not None:
            self._registro.update(kwargs)


class _PaginaSubmitLogin:
    """Simula el POST de ClaveCiudad devolviendo el estado indicado."""

    def __init__(self, estado: dict[str, Any]) -> None:
        self._estado = estado
        self.click_kwargs: dict[str, Any] = {}

    def get_by_role(self, *_: Any, **__: Any) -> _BotonAgip:
        return _BotonAgip(self.click_kwargs)

    async def wait_for_function(self, *_: Any, **__: Any) -> None:
        return None

    async def evaluate(self, *_: Any, **__: Any) -> dict[str, Any]:
        return dict(self._estado)


def _sesion_agip(page: Any) -> AgipSession:
    return AgipSession(SimpleNamespace(clave="secreto-ficticio"), page=page)


def test_rechazo_inline_de_clave_agip_es_error_de_credenciales() -> None:
    pagina = _PaginaSubmitLogin(
        {"representados": False, "errorEmail": "", "errorPassword": ""}
    )
    with pytest.raises(CredentialsRejectedError) as error:
        asyncio.run(_sesion_agip(pagina)._submit_login())
    assert error.value.diagnostic_code == "agip_representados_missing"


def test_cuil_sin_cuenta_agip_se_clasifica_como_cuenta_inexistente() -> None:
    pagina = _PaginaSubmitLogin(
        {
            "representados": False,
            "errorEmail": "No existe una cuenta registrada con este CUIL.",
            "errorPassword": "",
        }
    )
    with pytest.raises(CredentialsRejectedError) as error:
        asyncio.run(_sesion_agip(pagina)._submit_login())
    assert error.value.diagnostic_code == "agip_account_not_found"
    # El mensaje del portal no viaja en el error: solo el codigo fijo.
    assert "No existe" not in str(error.value)
    assert "CUIL" not in str(error.value)


def test_clave_incorrecta_agip_se_clasifica_como_clave_rechazada() -> None:
    pagina = _PaginaSubmitLogin(
        {
            "representados": False,
            "errorEmail": "",
            "errorPassword": "La contraseña es incorrecta.",
        }
    )
    with pytest.raises(CredentialsRejectedError) as error:
        asyncio.run(_sesion_agip(pagina)._submit_login())
    assert error.value.diagnostic_code == "agip_password_rejected"
    assert "incorrecta" not in str(error.value)


def test_login_agip_con_representados_no_reporta_rechazo() -> None:
    pagina = _PaginaSubmitLogin(
        {"representados": True, "errorEmail": "", "errorPassword": ""}
    )
    asyncio.run(_sesion_agip(pagina)._submit_login())


def test_clicks_de_login_agip_no_esperan_la_navegacion() -> None:
    class _PaginaPortada:
        def __init__(self) -> None:
            self.click_kwargs: dict[str, Any] = {}

        def get_by_role(self, *_: Any, **__: Any) -> _BotonAgip:
            return _BotonAgip(self.click_kwargs)

        async def evaluate(self, *_: Any, **__: Any) -> bool:
            return False

        async def wait_for_function(self, *_: Any, **__: Any) -> None:
            return None

    portada = _PaginaPortada()
    asyncio.run(_sesion_agip(portada)._open_login_gateway())
    assert portada.click_kwargs.get("no_wait_after") is True

    submit = _PaginaSubmitLogin(
        {"representados": True, "errorEmail": "", "errorPassword": ""}
    )
    asyncio.run(_sesion_agip(submit)._submit_login())
    assert submit.click_kwargs.get("no_wait_after") is True


def test_click_de_login_agip_que_expira_es_timeout_del_sitio() -> None:
    class _BotonQueExpira:
        async def click(self, **_: Any) -> None:
            raise TimeoutError("Timeout 30000ms exceeded")

    class _Pagina:
        def get_by_role(self, *_: Any, **__: Any) -> _BotonQueExpira:
            return _BotonQueExpira()

        async def wait_for_function(self, *_: Any, **__: Any) -> None:
            return None

        async def evaluate(self, *_: Any, **__: Any) -> dict[str, Any]:
            return {}

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(_sesion_agip(_Pagina())._submit_login())
    assert error.value.diagnostic_code == "agip_login_timeout"


def test_avisar_reintento_reporta_progreso_y_no_tumba_la_corrida() -> None:
    class _SinkRegistrador:
        def __init__(self) -> None:
            self.eventos: list[dict[str, Any]] = []

        async def progress(self, **kwargs: Any) -> None:
            self.eventos.append(kwargs)

    class _SinkQueFalla:
        async def progress(self, **_: Any) -> None:
            raise RuntimeError("transporte caido")

    sink = _SinkRegistrador()
    aviso = plugin_module._avisar_reintento(SimpleNamespace(event_sink=sink))
    asyncio.run(aviso(2, 5, 10_000))
    assert sink.eventos == [
        {
            "phase": "LOGIN",
            "percent": 10,
            "message": "Reintentando acceso AGIP 2/5 tras 10s de espera",
        }
    ]

    # Un sink roto no puede hacer fallar el reintento.
    aviso_roto = plugin_module._avisar_reintento(SimpleNamespace(event_sink=_SinkQueFalla()))
    asyncio.run(aviso_roto(1, 5, 5_000))


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
        def __init__(self, credentials: Any, *, page: Any, **kwargs: Any) -> None:
            invocaciones["credentials"] = credentials
            invocaciones["page"] = page
            invocaciones["avisar_reintento"] = kwargs.get("avisar_reintento")

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


_ERROR_CREDENCIALES = CredentialsRejectedError(
    "AGIP rechazo el usuario", diagnostic_code="agip_user_not_found"
)


class _SesionAgipQueFalla:
    """Sesion AGIP falsa que falla los primeros N intentos y despues responde.

    Cada intento del plugin crea una instancia nueva (contexto nuevo), asi que
    el contador de intentos vive en la clase.
    """

    intentos = 0
    fallos: list[Any] = []

    def __init__(self, credentials: Any, *, page: Any, **_: Any) -> None:
        type(self).intentos += 1
        self._numero = type(self).intentos

    async def login(self, url: str) -> None:
        error = type(self).fallos[self._numero - 1] if self._numero <= len(type(self).fallos) else None
        if error is not None:
            raise error

    async def ingresar(self, usuario: str, cuit_representado: str) -> None:
        return None

    async def consultar_retper(self, *, desde_mmyyyy: str, hasta_mmyyyy: str, destino: Path) -> None:
        destino.write_bytes(b"zip de prueba")

    async def close(self) -> None:
        return None


def _runtime_reintento(
    tmp_path: Path, *, deadline: Any = None, avisos: list[tuple[int, int, int]] | None = None
) -> Any:
    """Runtime minimo para ejercitar el reintento de sesion del plugin."""

    class _Context:
        async def new_page(self) -> object:
            return object()

    class _BrowserFactory:
        def __init__(self) -> None:
            self.contextos = 0

        @asynccontextmanager
        async def new_context(self):
            self.contextos += 1
            yield object(), _Context()

    class _ArtifactStore:
        def resolve(self, name: str) -> Path:
            return tmp_path / name

    class _Sink:
        async def progress(self, **kwargs: Any) -> None:
            mensaje = str(kwargs.get("message", ""))
            if avisos is not None and "Reintentando consulta AGIP" in mensaje:
                avisos.append(mensaje)

    class _Cancellation:
        async def raise_if_cancelled(self) -> None:
            return None

    return SimpleNamespace(
        credentials=SimpleNamespace(cuit_representante="", clave="secreto-ficticio"),
        cancellation=_Cancellation(),
        event_sink=_Sink(),
        deadline=deadline or SimpleNamespace(remaining_seconds=lambda: 600),
        browser_factory=_BrowserFactory(),
        proxy=None,
        artifact_store=_ArtifactStore(),
    )


def _entrada_agip() -> Any:
    plugin = RetperIibbAgipPlugin()
    return asyncio.run(
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


def test_sesion_reintenta_tras_ventana_bloqueada_y_termina_ok(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    esperas: list[int] = []

    async def _esperar(ms: int) -> None:
        esperas.append(ms)

    _SesionAgipQueFalla.intentos = 0
    _SesionAgipQueFalla.fallos = [
        TargetUnavailableError("bloqueo", diagnostic_code="agip_waf_blocked"),
        TargetUnavailableError(
            "servicio", diagnostic_code="agip_service_unavailable"
        ),
    ]
    monkeypatch.setattr(plugin_module, "AgipSession", _SesionAgipQueFalla)
    monkeypatch.setattr(plugin_module, "_esperar", _esperar)
    avisos: list[tuple[int, int, int]] = []
    runtime = _runtime_reintento(tmp_path, avisos=avisos)
    plugin = RetperIibbAgipPlugin()

    resultado = asyncio.run(plugin.execute(_entrada_agip(), runtime))

    assert resultado.result == "OK"
    assert _SesionAgipQueFalla.intentos == 3
    # Un contexto nuevo por intento y el backoff entre intentos (sin dormir).
    assert runtime.browser_factory.contextos == 3
    assert esperas == [5_000, 10_000]
    assert len(avisos) == 2


def test_sesion_agotada_reporta_el_ultimo_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    esperas: list[int] = []

    async def _esperar(ms: int) -> None:
        esperas.append(ms)

    _SesionAgipQueFalla.intentos = 0
    _SesionAgipQueFalla.fallos = [
        TargetUnavailableError(
            f"servicio {indice}", diagnostic_code="agip_service_unavailable"
        )
        for indice in range(len(plugin_module._ESPERAS_SESION_MS))
    ]
    monkeypatch.setattr(plugin_module, "AgipSession", _SesionAgipQueFalla)
    monkeypatch.setattr(plugin_module, "_esperar", _esperar)
    runtime = _runtime_reintento(tmp_path)
    plugin = RetperIibbAgipPlugin()

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(plugin.execute(_entrada_agip(), runtime))

    assert error.value.diagnostic_code == "agip_service_unavailable"
    assert _SesionAgipQueFalla.intentos == len(plugin_module._ESPERAS_SESION_MS)
    assert len(esperas) == len(plugin_module._ESPERAS_SESION_MS) - 1


def test_sesion_sin_deadline_no_reintenta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def _esperar(ms: int) -> None:
        raise AssertionError("no debe esperar sin deadline")

    restantes = iter((600, 0))

    _SesionAgipQueFalla.intentos = 0
    _SesionAgipQueFalla.fallos = [
        TargetUnavailableError("bloqueo", diagnostic_code="agip_waf_blocked")
    ]
    monkeypatch.setattr(plugin_module, "AgipSession", _SesionAgipQueFalla)
    monkeypatch.setattr(plugin_module, "_esperar", _esperar)
    runtime = _runtime_reintento(
        tmp_path, deadline=SimpleNamespace(remaining_seconds=lambda: next(restantes))
    )
    plugin = RetperIibbAgipPlugin()

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(plugin.execute(_entrada_agip(), runtime))

    assert error.value.diagnostic_code == "agip_waf_blocked"
    assert _SesionAgipQueFalla.intentos == 1


def test_error_no_reintentable_no_se_reintenta(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def _esperar(ms: int) -> None:
        raise AssertionError("no debe reintentar un rechazo de credenciales")

    _SesionAgipQueFalla.intentos = 0
    _SesionAgipQueFalla.fallos = [_ERROR_CREDENCIALES]
    monkeypatch.setattr(plugin_module, "AgipSession", _SesionAgipQueFalla)
    monkeypatch.setattr(plugin_module, "_esperar", _esperar)
    runtime = _runtime_reintento(tmp_path)
    plugin = RetperIibbAgipPlugin()

    with pytest.raises(CredentialsRejectedError):
        asyncio.run(plugin.execute(_entrada_agip(), runtime))

    assert _SesionAgipQueFalla.intentos == 1
