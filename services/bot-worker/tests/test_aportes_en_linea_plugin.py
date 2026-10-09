"""Pruebas del bot ``aportes_en_linea`` con fakes (port de V1 sin navegador).

V1 (``api-bots-mrbot/app/bot/aportes_en_linea_bot.py``) hace login, abre el
servicio ``APORTES EN LÍNEA``, clickea el botón ``INGRESAR`` (regex sin
distinguir mayúsculas, 10 s), y con ``expect_download`` + ``expect_popup`` baja
el ``Archivo Histórico``. El resultado trae ``archivo_historico_b64`` (si se
pidió) y la subida (``archivo_historico_minio`` en V1, slot prefirmado en V3)
con el nombre ``AEL - <cuit representado> - <AAAAMMDD>.xls``.
"""

from __future__ import annotations

import asyncio
import base64
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.aportes_en_linea.plugin import (
    AportesEnLineaPlugin,
    BOTON_INGRESAR,
    TOPE_BASE64_BYTES,
    nombre_archivo_historico,
)
from bot_worker.bots.errors import ArtifactUploadError, InvalidInputError
from bot_worker.runtime.context import (
    ArtifactStore,
    BotRuntime,
    CancellationToken,
    DeadlineBudget,
    FiscalCredentials,
)

CUIT = "20123456789"
XLS = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1historico"


class _Sink:
    def __init__(self) -> None:
        self.eventos: list[tuple[str, int]] = []

    async def progress(self, *, phase: str, percent: int, message: str) -> None:
        self.eventos.append((phase, percent))


class _Descarga:
    def __init__(self, page: "_Pagina", contenido: bytes) -> None:
        self._page = page
        self._contenido = contenido

    async def path(self) -> str | None:
        return None

    async def save_as(self, path: str) -> None:
        self._page.eventos.append("guardar")
        Path(path).write_bytes(self._contenido)


class _EsperaDescarga:
    def __init__(self, page: "_Pagina") -> None:
        self._page = page

    async def __aenter__(self) -> "_EsperaDescarga":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    @property
    async def value(self) -> _Descarga:
        return _Descarga(self._page, self._page.contenido)


class _Popup:
    async def close(self) -> None:
        return None


class _EsperaPopup:
    def __init__(self, page: "_Pagina") -> None:
        self._page = page

    async def __aenter__(self) -> "_EsperaPopup":
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    @property
    async def value(self) -> _Popup:
        return _Popup()


class _Boton:
    def __init__(self, page: "_Pagina", nombre: Any) -> None:
        self._page = page
        self._nombre = nombre

    async def click(self, **_kwargs: Any) -> None:
        self._page.eventos.append(self._nombre)
        self._page.clicks.append((self._nombre, _kwargs.get("timeout")))

    async def count(self) -> int:
        return 1

    @property
    def first(self) -> "_Boton":
        return self


class _Pagina:
    """Página mínima con la interfaz que usa el plugin."""

    def __init__(self, contenido: bytes = XLS) -> None:
        self.contenido = contenido
        self.eventos: list[Any] = []
        self.clicks: list[tuple[Any, Any]] = []

    def get_by_role(self, rol: str, name: Any = None) -> _Boton:
        return _Boton(self, name)

    async def wait_for_load_state(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def expect_download(self, **_kwargs: Any) -> _EsperaDescarga:
        self.eventos.append("expect_download")
        return _EsperaDescarga(self)

    def expect_popup(self, **_kwargs: Any) -> _EsperaPopup:
        self.eventos.append("expect_popup")
        return _EsperaPopup(self)


class _Sesion:
    def __init__(self, pagina: _Pagina) -> None:
        self.pagina = pagina
        self.servicios: list[str] = []

    async def login(self) -> None:
        return None

    async def open_service(self, servicio: str, **_kwargs: Any) -> _Pagina:
        self.servicios.append(servicio)
        return self.pagina


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self._sesion = sesion

    def arca_session(self, **_kwargs: Any) -> Any:
        sesion = self._sesion

        class _Contexto:
            async def __aenter__(self) -> _Sesion:
                return sesion

            async def __aexit__(self, *_args: Any) -> bool:
                return False

        return _Contexto()


class _Subidas:
    def __init__(self) -> None:
        self.rutas: list[tuple[str, str]] = []


def _runtime(
    tmp_path: Path, sesion: _Sesion, subidas: _Subidas, *, declarar: bool = True
) -> BotRuntime:
    async def presign(artifact_id: str, content_type: str, size_bytes: int) -> dict[str, Any]:
        return {"upload_url": "", "object_key": f"obj/{artifact_id}"}

    declarados = (
        {
            spec.nombre: (tuple(spec.content_types), spec.max_bytes)
            for spec in AportesEnLineaPlugin.manifest.artefactos_produce
        }
        if declarar
        else {}
    )
    store = ArtifactStore(work_dir=tmp_path, slots={}, presign=presign, declarados=declarados)
    original = store.upload

    async def upload_registrado(
        artifact_id: str, rel_path: str, client: Any = None
    ) -> dict[str, Any]:
        subidas.rutas.append((artifact_id, rel_path))
        return await original(artifact_id, rel_path, client)

    store.upload = upload_registrado  # type: ignore[method-assign]
    return BotRuntime(
        job_id="job-aelt",
        work_dir=tmp_path,
        deadline=DeadlineBudget(deadline_epoch=9_999_999_999),
        credentials=FiscalCredentials(cuit_representante=CUIT, clave="clave-ficticia"),
        proxy=None,
        artifact_store=store,
        event_sink=_Sink(),
        browser_factory=_Fabrica(sesion),  # type: ignore[arg-type]
        cancellation=CancellationToken(),
    )


def _validar(**extra: Any) -> Any:
    cuerpo: dict[str, Any] = {"operacion": "descargar"}
    cuerpo.update(extra)
    return asyncio.run(AportesEnLineaPlugin().validate(cuerpo))


def test_el_manifiesto_y_el_nombre_del_historico_siguen_a_v1() -> None:
    assert AportesEnLineaPlugin.manifest.operaciones == ("descargar",)
    assert AportesEnLineaPlugin.manifest.artefactos_produce[0].nombre == "historico.xls"
    assert nombre_archivo_historico(CUIT, "20260101") == f"AEL - {CUIT} - 20260101.xls"
    assert nombre_archivo_historico(CUIT).startswith(f"AEL - {CUIT} - ")
    # V1 busca el botón con un patrón sin distinguir mayúsculas.
    assert BOTON_INGRESAR.search("Ingresar al servicio")


def test_el_esquema_acepta_los_campos_de_v1() -> None:
    _, entrada = _validar(
        cuit_representante="20-12345678-9",
        clave_representante="clave-ficticia",
        cuit_representado=CUIT,
        archivo_historico_b64=True,
        archivo_historico_minio=False,
        proxy_request=False,
    )

    assert entrada.representado_cuit == CUIT
    assert entrada.incluir_base64 is True
    assert entrada.subir is False
    assert entrada.cuit_representante == "20123456789"


def test_el_esquema_exige_alguna_salida() -> None:
    with pytest.raises(InvalidInputError):
        _validar(incluir_base64=False, subir=False)


def test_execute_descarga_sube_y_devuelve_base64(tmp_path: Path) -> None:
    async def _ir() -> None:
        pagina = _Pagina()
        sesion = _Sesion(pagina)
        subidas = _Subidas()
        plugin = AportesEnLineaPlugin()
        entrada = await plugin.validate(
            {
                "operacion": "descargar",
                "cuit_representado": CUIT,
                "archivo_historico_b64": True,
            }
        )
        runtime = _runtime(tmp_path, sesion, subidas)

        resultado = await plugin.execute(entrada, runtime)

        assert resultado.result == "OK"
        assert sesion.servicios == ["APORTES EN LÍNEA"]
        # V1: popup capturado y cerrado, y descarga guardada una sola vez.
        eventos = [evento for evento in pagina.eventos if isinstance(evento, str)]
        assert eventos[:2] == ["expect_download", "expect_popup"]
        assert "Archivo Histórico" in eventos
        assert eventos[-1] == "guardar"
        nombre = resultado.data["archivo"]
        assert nombre.startswith(f"AEL - {CUIT} - ") and nombre.endswith(".xls")
        assert resultado.data["representado_cuit"] == CUIT
        assert resultado.data["archivo_historico_b64"] == base64.b64encode(XLS).decode(
            "ascii"
        )
        assert subidas.rutas == [("historico.xls", nombre)]
        assert len(resultado.artifacts) == 1
        assert resultado.artifacts[0]["sha256"]
        # El boton INGRESAR se clickea con el timeout de V1 (10 s).
        assert any(
            isinstance(nombre_click, re.Pattern) and nombre_click.pattern == "INGRESAR"
            for nombre_click, _ in pagina.clicks
        )
        assert ("Archivo Histórico", None) in pagina.clicks

    asyncio.run(_ir())


def test_execute_sin_subir_solo_devuelve_el_archivo(tmp_path: Path) -> None:
    async def _ir() -> None:
        pagina = _Pagina()
        subidas = _Subidas()
        plugin = AportesEnLineaPlugin()
        entrada = await plugin.validate(
            {
                "operacion": "descargar",
                "representado_cuit": CUIT,
                "incluir_base64": True,
                "subir": False,
            }
        )
        runtime = _runtime(tmp_path, _Sesion(pagina), subidas)

        resultado = await plugin.execute(entrada, runtime)

        assert subidas.rutas == []
        assert resultado.artifacts == []
        assert "sha256" not in resultado.data
        assert resultado.data["archivo_historico_b64"]

    asyncio.run(_ir())


def test_sin_representado_usa_el_cuit_de_la_credencial(tmp_path: Path) -> None:
    async def _ir() -> None:
        plugin = AportesEnLineaPlugin()
        entrada = await plugin.validate({"operacion": "descargar", "archivo_historico_b64": True})
        runtime = _runtime(tmp_path, _Sesion(_Pagina()), _Subidas())

        resultado = await plugin.execute(entrada, runtime)

        assert resultado.data["representado_cuit"] == CUIT
        assert resultado.data["archivo"].startswith(f"AEL - {CUIT} - ")

    asyncio.run(_ir())


def test_base64_grande_no_viaja_inline(tmp_path: Path) -> None:
    async def _ir() -> None:
        grande = b"x" * (TOPE_BASE64_BYTES + 1)
        plugin = AportesEnLineaPlugin()
        entrada = await plugin.validate(
            {"operacion": "descargar", "representado_cuit": CUIT, "incluir_base64": True}
        )
        runtime = _runtime(tmp_path, _Sesion(_Pagina(grande)), _Subidas())

        resultado = await plugin.execute(entrada, runtime)

        assert "archivo_historico_b64" not in resultado.data
        assert "tope base64" in resultado.data["warnings"][0]
        # El archivo igual se sube como artefacto.
        assert len(resultado.artifacts) == 1

    asyncio.run(_ir())


def test_la_subida_fallida_es_un_error_reintentable(tmp_path: Path) -> None:
    async def _ir() -> None:
        plugin = AportesEnLineaPlugin()
        entrada = await plugin.validate(
            {"operacion": "descargar", "representado_cuit": CUIT}
        )
        runtime = _runtime(tmp_path, _Sesion(_Pagina()), _Subidas(), declarar=False)

        with pytest.raises(ArtifactUploadError):
            await plugin.execute(entrada, runtime)

    asyncio.run(_ir())


def test_configure_ajusta_el_nombre_del_servicio() -> None:
    plugin = AportesEnLineaPlugin().configure({"aportes_servicio": "APORTES OTRO"})

    assert plugin._servicio == "APORTES OTRO"


def test_execute_requiere_credenciales_fiscales() -> None:
    from bot_worker.bots.errors import CredentialsRejectedError

    async def _ir() -> None:
        plugin = AportesEnLineaPlugin()
        entrada = await plugin.validate({"operacion": "descargar", "representado_cuit": CUIT})
        runtime = SimpleNamespace(credentials=None, cancellation=CancellationToken())

        with pytest.raises(CredentialsRejectedError):
            await plugin.execute(entrada, runtime)

    asyncio.run(_ir())
