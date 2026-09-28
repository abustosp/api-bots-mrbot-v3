from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.portal_iva.plugin import PortalIvaPlugin
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals.portal_iva import PortalIvaPortal, _normalizar_periodo


class _Option:
    def __init__(self, value: str, label: str) -> None:
        self.value = value
        self.label = label

    async def get_attribute(self, name: str) -> str:
        return self.value if name == "value" else ""

    async def text_content(self) -> str:
        return self.label


class _Locator:
    def __init__(self, *, visible: bool = False, options: list[_Option] | None = None) -> None:
        self.visible = visible
        self.options = options or []
        self.selected: str | None = None
        self.clicked = False

    @property
    def first(self) -> _Locator:
        return self

    def nth(self, index: int) -> _Option:
        return self.options[index]

    def locator(self, selector: str) -> _Locator:
        return _Locator(visible=self.visible, options=self.options if selector == "option" else [])

    async def count(self) -> int:
        return len(self.options) if self.options else int(self.visible)

    async def is_visible(self, **_: Any) -> bool:
        return self.visible or bool(self.options)

    async def scroll_into_view_if_needed(self, **_: Any) -> None:
        return None

    async def click(self, **_: Any) -> None:
        self.clicked = True

    async def element_handle(self) -> None:
        return None

    async def select_option(self, *, value: str = "", **_: Any) -> None:
        self.selected = value

    async def get_attribute(self, name: str) -> str:
        return ""

    async def text_content(self) -> str:
        return ""

    async def inner_text(self, **_: Any) -> str:
        return ""


class _Page:
    def __init__(self, *, period_options: list[_Option] | None = None, csv: bool = False) -> None:
        self.url = "https://siapweb.cloud.afip.gob.ar/iva/"
        self.period = _Locator(visible=bool(period_options), options=period_options or [])
        self.csv = _Locator(visible=csv)
        self.actions: dict[str, _Locator] = {
            "validar": _Locator(visible=True),
            "liva": _Locator(visible=True),
        }
        self.content_text = (
            "<html><body>Portal IVA Representando a: "
            + "contenido " * 20
            + "</body></html>"
        )

    def locator(self, selector: str) -> _Locator:
        if selector == "#periodo":
            return self.period
        if selector == "#periodoPresentacion":
            return _Locator()
        if selector == "span.nombre-propio.nombre-activo.pull-right":
            return _Locator()
        if selector == "a[href*='menuPresentacion.do']":
            return _Locator()
        if selector == "[title*='cambio relación' i]":
            return _Locator()
        if selector.startswith("a[href*"):
            return _Locator()
        return _Locator()

    def get_by_role(self, role: str, *, name: Any = None, **_: Any) -> _Locator:
        text = str(name or "")
        if "CSV" in text.upper():
            return self.csv
        if "Validar" in text or "validar" in text:
            return self.actions["validar"]
        if "liva" in text.lower() or "Libro IVA" in text:
            return self.actions["liva"]
        return _Locator()

    def get_by_label(self, *_: Any, **__: Any) -> _Locator:
        return _Locator()

    def get_by_text(self, text: Any, **_: Any) -> _Locator:
        if hasattr(text, "search") and text.search(self.content_text):
            return _Locator(visible=True)
        return _Locator()

    def get_by_title(self, *_: Any, **__: Any) -> _Locator:
        return _Locator()

    async def content(self) -> str:
        return self.content_text

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [("202505", "202505"), ("05/2025", "202505"), ("2025-05", "202505")],
)
def test_normaliza_periodo_portal_iva(entrada: str, esperado: str) -> None:
    assert _normalizar_periodo(entrada) == esperado


def test_seleccionar_representado_ya_activo_no_abre_el_selector() -> None:
    pagina = _Page()
    pagina.content_text = "<html><body>Representando a: 20-12345678-9</body></html>" + (
        "contenido " * 20
    )
    portal = PortalIvaPortal(pagina)

    asyncio.run(portal.seleccionar_representado("20123456789"))

    assert not pagina.get_by_title("cambio relación").clicked


def test_representante_propio_visible_no_abre_el_selector() -> None:
    pagina = _Page()
    pagina.content_text = "<html><body>20-12345678-9</body></html>" + (
        "contenido " * 20
    )
    portal = PortalIvaPortal(pagina)

    asyncio.run(
        portal.seleccionar_representado(
            "20123456789", cuit_representante="20-12345678-9"
        )
    )

    assert not pagina.get_by_title("cambio relación").clicked


def test_rechaza_periodo_invalido_sin_navegar() -> None:
    portal = PortalIvaPortal(_Page())
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.seleccionar_periodo("202513"))
    assert error.value.diagnostic_code == "portal_iva_period_invalid"


def test_seleccionar_periodo_valida_opcion_y_no_guarda() -> None:
    pagina = _Page(period_options=[_Option("202505", "05/2025")])
    portal = PortalIvaPortal(pagina)

    asyncio.run(portal.seleccionar_periodo("05/2025"))

    assert pagina.period.selected == "202505"
    assert pagina.actions["validar"].clicked
    assert pagina.actions["liva"].clicked


def test_descargar_libro_rechaza_tipo_desconocido_antes_de_interactuar() -> None:
    portal = PortalIvaPortal(_Page())
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.descargar_libro("declaracion", Path("salida.csv")))
    assert error.value.diagnostic_code == "portal_iva_book_unknown"


def test_descargar_libro_captura_csv(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pagina = _Page(csv=True)
    portal = PortalIvaPortal(pagina)
    destino = tmp_path / "ventas.csv"

    async def capturar(self: PortalIvaPortal, ruta: Path, accion: Any, **_: Any) -> Path:
        await accion()
        ruta.write_text("fecha,importe\n", encoding="utf-8")
        return ruta

    async def volver(self: PortalIvaPortal) -> None:
        return None

    monkeypatch.setattr(PortalIvaPortal, "capturar_descarga", capturar)
    monkeypatch.setattr(PortalIvaPortal, "_volver_al_menu", volver)

    resultado = asyncio.run(portal.descargar_libro("ventas", destino))

    assert resultado == destino
    assert destino.read_text(encoding="utf-8") == "fecha,importe\n"
    assert pagina.csv.clicked


def test_portal_registrado_sin_metodos_de_carga_ni_presentacion() -> None:
    assert portal_para("portal_iva") is PortalIvaPortal
    assert "portal_iva" in PORTALES
    assert not hasattr(PortalIvaPortal, "importar_txt")
    assert not hasattr(PortalIvaPortal, "cargar")
    assert not hasattr(PortalIvaPortal, "presentar")


def test_plugin_lee_csv_portal_iva_windows_1252(tmp_path: Path) -> None:
    archivo = tmp_path / "portal.csv"
    archivo.write_bytes("fecha,denominación\n2026-09-01,compra\n".encode("cp1252"))

    assert PortalIvaPlugin._contar_filas(archivo) == 1
    assert PortalIvaPlugin._muestra(archivo)[0]["denominación"] == "compra"


def test_csv_zipeado_se_extrae_plano(tmp_path) -> None:
    import zipfile

    from bot_worker.runtime.portals.portal_iva import _extraer_csv_si_es_zip

    destino = tmp_path / "ventas.csv"
    with zipfile.ZipFile(destino, "w") as z:
        z.writestr("comprobantes_periodo_202609_ventas.csv", "Fecha;Tipo\n01/09/2026;1\n")
    _extraer_csv_si_es_zip(destino)
    assert destino.read_text() == "Fecha;Tipo\n01/09/2026;1\n"

    # Un CSV plano queda intacto.
    _extraer_csv_si_es_zip(destino)
    assert destino.read_text().startswith("Fecha;Tipo")
