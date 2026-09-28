"""Pruebas del contrato entre el plugin SIFERE y el catálogo ARCA."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bot_worker.bots.sifere.plugin import SERVICIO_ARCA, SiferePlugin
from bot_worker.runtime.portals.sifere import SiferePortal, URL_INICIO_RETENCIONES
from bot_worker.runtime.context import ArtifactStore


class _ContextoAsync:
    def __init__(self, valor: Any) -> None:
        self.valor = valor

    async def __aenter__(self) -> Any:
        return self.valor

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _ServicioSifere:
    def __init__(self) -> None:
        self.servicio_abierto: tuple[str, str | None] | None = None

    async def login(self) -> None:
        pass

    async def open_service(self, service: str, *, portal: str | None = None) -> Any:
        self.servicio_abierto = (service, portal)
        return self

    async def seleccionar_representado(self, cuit: str) -> None:
        assert cuit == "20123456789"

    async def consultar_jurisdiccion(
        self, codigo: int, periodo: str, destino: Path
    ) -> dict[str, int]:
        destino.write_text("jurisdiccion,importe\n901,0\n", encoding="utf-8")
        return {"filas": 1}


class _BrowserFactory:
    def __init__(self, servicio: _ServicioSifere) -> None:
        self.servicio = servicio

    def arca_session(self, **kwargs: Any) -> _ContextoAsync:
        return _ContextoAsync(self.servicio)


class _Cancelacion:
    async def raise_if_cancelled(self) -> None:
        pass


class _Eventos:
    async def progress(self, **kwargs: Any) -> None:
        pass


class _SelectorRepresentado:
    def __init__(self) -> None:
        self.codigo: str | None = None
        self.script: str | None = None

    async def select_option(self, value: str) -> None:
        self.codigo = value

    async def evaluate(self, script: str) -> bool:
        self.script = script
        # The selector's form navigates on submit. The portal must queue that
        # submit rather than await navigation inside locator.evaluate().
        return "setTimeout(() =>" in script and "form.requestSubmit()" in script


class _AmbitoRepresentado:
    async def wait_for_load_state(self, *args: Any, **kwargs: Any) -> None:
        pass

    def get_by_role(self, *args: Any, **kwargs: Any) -> object:
        return object()

    def get_by_text(self, *args: Any, **kwargs: Any) -> object:
        return object()

    def locator(self, *args: Any, **kwargs: Any) -> object:
        return object()


class _SiferePortalTest(SiferePortal):
    def __init__(self, selector: _SelectorRepresentado) -> None:
        super().__init__(
            SimpleNamespace(
                url="https://app1.comarb.gob.ar/siferewebconsultas/mainMenu.do"
            )
        )
        self.selector = selector

    async def _resolver_entry_point(self) -> None:
        pass

    async def _buscar_selector_cuit(self) -> tuple[Any, _AmbitoRepresentado]:
        return self.selector, _AmbitoRepresentado()

    async def _clickear(self, *args: Any, **kwargs: Any) -> bool:
        return False

    async def _esperar(self, *args: Any, **kwargs: Any) -> None:
        pass


def test_seleccionar_representado_envia_formulario_sin_esperar_navegacion() -> None:
    async def _ejecutar() -> tuple[str | None, str | None, bool]:
        selector = _SelectorRepresentado()
        portal = _SiferePortalTest(selector)
        await portal.seleccionar_representado("20-12345678-9")
        return selector.codigo, selector.script, portal._representado_listo

    codigo, script, listo = asyncio.run(_ejecutar())
    assert codigo == "20123456789"
    assert script is not None and "setTimeout(() =>" in script
    assert listo


def test_consultar_inicializa_retenciones_antes_de_filtrar_jurisdiccion(tmp_path: Path) -> None:
    class PortalGrabador(_SiferePortalTest):
        def __init__(self) -> None:
            super().__init__(_SelectorRepresentado())
            self.urls: list[str] = []
            self._representado_listo = True

        async def abrir_url(self, url: str, **kwargs: Any) -> None:
            self.urls.append(url)

        async def capturar_descarga(self, destino: Path, accion: Any, **kwargs: Any) -> Path:
            destino.write_text("jurisdiccion,importe\n901,1\n", encoding="utf-8")
            return destino

    async def _ejecutar() -> list[str]:
        portal = PortalGrabador()
        await portal.consultar_jurisdiccion(901, "202601", tmp_path / "primera.xls")
        await portal.consultar_jurisdiccion(902, "202601", tmp_path / "segunda.xls")
        return portal.urls

    urls = asyncio.run(_ejecutar())
    assert urls[0] == URL_INICIO_RETENCIONES
    assert "method=retencion&juris=901" in urls[1]
    assert "method=retencion&juris=902" in urls[2]
    assert len(urls) == 3


def test_manifiesto_autoriza_archivos_de_distintas_jurisdicciones(tmp_path: Path) -> None:
    spec = SiferePlugin.manifest.artefactos_produce[0]
    assert spec.nombre == "sifere_jurisdiccion"
    store = ArtifactStore(
        tmp_path,
        slots={},
        declarados={spec.nombre: (spec.content_types, spec.max_bytes)},
    )
    (tmp_path / "jurisdiccion.xlsx").write_bytes(b"PK\x03\x04ejemplo")

    async def _ejecutar() -> list[str]:
        uno = await store.upload("sifere_jurisdiccion_xlsx_901", "jurisdiccion.xlsx")
        dos = await store.upload("sifere_jurisdiccion_xlsx_902", "jurisdiccion.xlsx")
        assert uno["content_type"] == spec.content_types[0]
        assert dos["content_type"] == spec.content_types[0]
        return [uno["artifact_id"], dos["artifact_id"]]

    assert asyncio.run(_ejecutar()) == [
        "sifere_jurisdiccion_xlsx_901",
        "sifere_jurisdiccion_xlsx_902",
    ]


def test_execute_abre_el_nombre_visible_de_sifere_en_arca(tmp_path: Path) -> None:
    async def _ejecutar() -> tuple[str, str | None]:
        servicio = _ServicioSifere()
        plugin = SiferePlugin()
        entrada = await plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": "20123456789",
                "periodo": "202401",
                "jurisdicciones": [901],
                "subir": False,
            }
        )
        runtime = SimpleNamespace(
            credentials=SimpleNamespace(clave="clave-ficticia"),
            cancellation=_Cancelacion(),
            event_sink=_Eventos(),
            deadline=SimpleNamespace(remaining_seconds=lambda: 60),
            browser_factory=_BrowserFactory(servicio),
            proxy=None,
            artifact_store=SimpleNamespace(resolve=lambda name: tmp_path / name),
        )
        resultado = await plugin.execute(entrada, runtime)
        assert resultado.result == "OK"
        assert servicio.servicio_abierto is not None
        return servicio.servicio_abierto

    servicio, portal = asyncio.run(_ejecutar())
    assert servicio == "CONVENIO MULTILATERAL – SIFERE WEB - CONSULTAS"
    assert servicio == SERVICIO_ARCA
    assert portal == "sifere"
