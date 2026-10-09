"""Pruebas del portal SIPER: campos del detalle (port de V1) y captura PNG intacta.

Sin navegador: el HTML de la conducta fiscal es fijo y la pagina es falsa.
"""
from __future__ import annotations

import asyncio
import struct
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.siper.plugin import SiperRealPlugin
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.siper import (
    CAMPOS_DETALLE,
    SiperPortal,
    extraer_detalle_siper,
)


def _fila(etiqueta: str, valor_html: str, clase_valor: str = "conductaFiscalValue") -> str:
    return (
        f'<tr><td class="conductaFiscalName">{etiqueta}</td>'
        f'<td class="{clase_valor}"><div>{valor_html}</div></td></tr>'
    )


DETALLE_HTML = (
    "<html><body><div id=\"sectionContainer\"><table>"
    + _fila("Contribuyente", "GERMAN BOLZAN")
    + (
        '<tr><td class="conductaFiscalName">Categor\u00eda SIPER</td>'
        '<td class="conductaFiscalValue"><div class="conductaFiscalLink">'
        "<span>Categor\u00eda</span> <span>3</span></div></td></tr>"
    )
    + _fila("Motivo", "Cambio de actividad")
    + _fila("Fecha \u00faltima Categor\u00eda SIPER", "15/03/2026")
    + _fila("Estado tr\u00e1mite de disconformidad", "Sin tr\u00e1mite pendiente")
    + _fila("Otro dato", "no se extrae")
    + "</table></div></body></html>"
)


def _png(ancho: int, alto: int) -> bytes:
    """PNG minimo valido: firma, cabecera IHDR con dimensiones y un IEND."""
    import zlib

    cabecera = struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0)

    def trozo(tipo: bytes, datos: bytes) -> bytes:
        return (
            struct.pack(">I", len(datos))
            + tipo
            + datos
            + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + trozo(b"IHDR", cabecera)
        + trozo(b"IDAT", zlib.compress(b"\x00"))
        + trozo(b"IEND", b"")
    )


def test_extrae_los_campos_de_conducta_fiscal() -> None:
    campos = extraer_detalle_siper(DETALLE_HTML)

    assert campos == {
        "contribuyente": "GERMAN BOLZAN",
        "categoria_siper": "Categor\u00eda 3",
        "motivo": "Cambio de actividad",
        "fecha_ultima_categoria_siper": "15/03/2026",
        "estado_tramite_disconformidad": "Sin tr\u00e1mite pendiente",
    }


def test_no_extrae_campos_fuera_del_mapa_v1() -> None:
    campos = extraer_detalle_siper(DETALLE_HTML)

    assert set(campos) <= set(CAMPOS_DETALLE.values())
    assert "Otro dato" not in campos.values()


def test_etiquetas_sin_acentos_ni_mayusculas_tambien_coinciden() -> None:
    html = (
        "<table>"
        + _fila("CATEGORIA SIPER", "Categoria 2")
        + _fila("estado tramite de disconformidad", "Ninguno")
        + "</table>"
    )

    campos = extraer_detalle_siper(html)

    assert campos["categoria_siper"] == "Categoria 2"
    assert campos["estado_tramite_disconformidad"] == "Ninguno"


def test_valor_vacio_no_aparece_en_el_resumen() -> None:
    html = "<table>" + _fila("Motivo", "") + _fila("Contribuyente", "ACME") + "</table>"

    assert extraer_detalle_siper(html) == {"contribuyente": "ACME"}


def test_html_vacio_devuelve_diccionario_vacio() -> None:
    assert extraer_detalle_siper("") == {}
    assert extraer_detalle_siper("<html><body>sin tabla</body></html>") == {}


class _Locator:
    def __init__(self, html: str) -> None:
        self._html = html

    @property
    def first(self) -> _Locator:
        return self

    async def count(self) -> int:
        return 1

    async def wait_for(self, **_: Any) -> None:
        return None

    async def inner_html(self) -> str:
        return self._html

    async def screenshot(self, path: str) -> None:
        Path(path).write_bytes(_png(640, 480))


class _PaginaSiper:
    def __init__(self, html: str) -> None:
        self._html = html
        self.urls: list[str] = []
        self.url = "https://seti.afip.gob.ar/padron-puc-consulta-internet/"

    def locator(self, selector: str) -> _Locator:
        return _Locator(self._html)

    async def goto(self, url: str, **_: Any) -> None:
        self.urls.append(url)

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


def test_portal_extrae_campos_y_navega_al_detalle_por_cuit() -> None:
    pagina = _PaginaSiper(DETALLE_HTML)
    portal = SiperPortal(pagina)

    campos = asyncio.run(portal.extraer_campos_detalle("20-12345678-9"))

    assert campos["contribuyente"] == "GERMAN BOLZAN"
    assert any("ResponsiveIndexInternetAction.do?idPersona=20123456789" in u for u in pagina.urls)


def test_portal_extraer_campos_rechaza_cuit_invalido() -> None:
    portal = SiperPortal(_PaginaSiper(DETALLE_HTML))

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.extraer_campos_detalle("123"))
    assert error.value.diagnostic_code == "siper_cuit_invalid"


def test_portal_falla_si_el_detalle_no_trae_campos() -> None:
    portal = SiperPortal(_PaginaSiper("<html><body>sin conducta fiscal</body></html>"))

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.extraer_campos_detalle("20123456789"))
    assert error.value.diagnostic_code == "siper_detalle_vacio"


def test_captura_png_sigue_entregando_dimensiones(tmp_path: Path) -> None:
    pagina = _PaginaSiper(DETALLE_HTML)
    portal = SiperPortal(pagina)
    destino = tmp_path / "detalle.png"

    resumen = asyncio.run(
        portal.capturar_vista("detalle", destino, representado_cuit="20123456789")
    )

    assert resumen == {"vista": "detalle", "ancho": 640, "alto": 480, "archivo": "detalle.png"}
    assert destino.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_plugin_siper_campos_es_default_como_v1() -> None:
    from bot_worker.bots.siper.schema import SiperConsultarInput

    base = {"representado_cuit": "20123456789"}
    # V1 (siper_bot.py) siempre extraia los campos del detalle: default True.
    assert SiperConsultarInput(**base).extraer_campos is True
    assert SiperConsultarInput(**base, extraer_campos=False).extraer_campos is False


def test_plugin_siper_agrega_campos_al_resultado_sin_tocar_las_capturas(tmp_path: Path) -> None:
    class _Servicio:
        def __init__(self) -> None:
            self.vistas: list[str] = []

        async def capturar_vista(self, *, vista: str, destino: Path, **_: Any) -> dict[str, int]:
            self.vistas.append(vista)
            destino.write_bytes(_png(10, 20))
            return {"ancho": 10, "alto": 20}

        async def extraer_campos_detalle(self, *, representado_cuit: str) -> dict[str, str]:
            return {"contribuyente": "GERMAN BOLZAN"}

    class _Store:
        def __init__(self) -> None:
            self.subidos: list[str] = []

        def resolve(self, nombre: str) -> Path:
            return tmp_path / nombre

        async def upload(self, artifact_id: str, nombre: str) -> dict[str, Any]:
            self.subidos.append(artifact_id)
            return {"sha256": "abc", "size_bytes": 10}

    class _Cancelacion:
        async def raise_if_cancelled(self) -> None:
            return None

    class _Eventos:
        async def progress(self, **_: Any) -> None:
            return None

    async def _ejecutar(extraer: bool) -> tuple[dict[str, Any], _Servicio, _Store]:
        servicio = _Servicio()
        store = _Store()
        plugin = SiperRealPlugin()
        _, entrada = await plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": "20123456789",
                "extraer_campos": extraer,
            }
        )
        runtime = type(
            "Runtime",
            (),
            {"cancellation": _Cancelacion(), "event_sink": _Eventos(), "artifact_store": store},
        )()
        datos, _, errores = await plugin._capturar(servicio, entrada, runtime)
        return datos, servicio, store, errores

    datos_sin, servicio_sin, store_sin, errores_sin = asyncio.run(_ejecutar(False))
    assert "campos" not in datos_sin
    assert errores_sin == []
    assert servicio_sin.vistas == ["detalle", "categorias"]
    assert store_sin.subidos == ["siper_detalle_png", "siper_categorias_png"]

    datos_con, _, store_con, errores_con = asyncio.run(_ejecutar(True))
    assert datos_con["campos"] == {"contribuyente": "GERMAN BOLZAN"}
    assert errores_con == []
    assert store_con.subidos == ["siper_detalle_png", "siper_categorias_png"]
    assert len(datos_con["vistas"]) == 2


def test_fallo_de_campos_siper_no_pierde_las_capturas(tmp_path: Path) -> None:
    from bot_worker.bots.errors import TargetUnavailableError

    class _Servicio:
        async def capturar_vista(self, *, vista: str, destino: Path, **_: Any) -> dict[str, int]:
            destino.write_bytes(_png(10, 20))
            return {"ancho": 10, "alto": 20}

        async def extraer_campos_detalle(self, *, representado_cuit: str) -> dict[str, str]:
            raise TargetUnavailableError(
                "el detalle SIPER no trajo campos de conducta fiscal",
                diagnostic_code="siper_detalle_vacio",
            )

    class _Store:
        def __init__(self) -> None:
            self.subidos: list[str] = []

        def resolve(self, nombre: str) -> Path:
            return tmp_path / nombre

        async def upload(self, artifact_id: str, nombre: str) -> dict[str, Any]:
            self.subidos.append(artifact_id)
            return {"sha256": "abc", "size_bytes": 10}

    class _Cancelacion:
        async def raise_if_cancelled(self) -> None:
            return None

    class _Eventos:
        async def progress(self, **_: Any) -> None:
            return None

    plugin = SiperRealPlugin()
    _, entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
    }))
    store = _Store()
    runtime = type(
        "Runtime",
        (),
        {"cancellation": _Cancelacion(), "event_sink": _Eventos(), "artifact_store": store},
    )()

    datos, _, errores = asyncio.run(plugin._capturar(_Servicio(), entrada, runtime))

    # Las capturas ya subidas se conservan; el fallo queda como error no fatal.
    assert store.subidos == ["siper_detalle_png", "siper_categorias_png"]
    assert len(datos["vistas"]) == 2
    assert "campos" not in datos
    assert len(errores) == 1
    assert "siper_detalle_vacio" in errores[0].internal_diagnostic


def test_siper_sigue_registrado_en_el_catalogo_de_portales() -> None:
    assert portal_para("siper") is SiperPortal
