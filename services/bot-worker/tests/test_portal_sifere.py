"""Pruebas del contrato entre el plugin SIFERE y el catálogo ARCA.

Cubre el port de V1 (``api-bots-mrbot/app/bot/sifere_bot.py``): SIRCAR por
método y jurisdicción (Excel + PDF), SIRCREB con sus dos descargas de mismo
nombre base, SIRTAC con los xlsx separados y las reglas de ``extraer_tabla_html``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.sifere.plugin import (
    ID_ARTEFACTO_CONSOLIDADO,
    ID_ARTEFACTO_SIRCAR_PDF,
    ID_ARTEFACTO_SIRCAR_XLSX,
    SERVICIO_ARCA,
    SiferePlugin,
)
from bot_worker.bots.sifere.schema import SifereConsultarInput
from bot_worker.runtime.context import ArtifactStore
from bot_worker.runtime.portals.sifere import SiferePortal, URL_SIRCAR_ENTRADA


class _ContextoAsync:
    def __init__(self, valor: Any) -> None:
        self.valor = valor

    async def __aenter__(self) -> Any:
        return self.valor

    async def __aexit__(self, *args: Any) -> bool:
        return False


class _ServicioSifere:
    """Servicio falso: un xlsx y un PDF por método y jurisdicción."""

    def __init__(self) -> None:
        self.servicio_abierto: tuple[str, str | None] | None = None
        self.sircar: list[tuple[str, int]] = []

    async def login(self) -> None:
        pass

    async def open_service(self, service: str, *, portal: str | None = None) -> Any:
        self.servicio_abierto = (service, portal)
        return self

    async def seleccionar_representado(self, cuit: str) -> None:
        assert cuit == "20123456789"

    async def consultar_sircar(
        self,
        *,
        metodo: str,
        jurisdiccion: int,
        periodo: str,
        destino_dir: Path,
        representado_cuit: str,
        nombre_representado: str | None = None,
    ) -> dict[str, Any]:
        self.sircar.append((metodo, jurisdiccion))
        archivos = []
        for tipo, ext in (("xlsx", ".xls"), ("pdf", ".pdf")):
            ruta = Path(destino_dir) / f"sircar-{metodo}-{jurisdiccion}-{tipo}{ext}"
            ruta.write_bytes(b"PK\x03\x04sifere")
            archivos.append({"nombre": ruta.name, "tipo": tipo, "filas": 1})
        return {
            "metodo": metodo,
            "etiqueta": "Retenciones" if metodo == "retencion" else "Percepciones",
            "jurisdiccion": jurisdiccion,
            "periodo": periodo,
            "archivos": archivos,
            "errores": [],
        }

    async def consultar_consolidados(self, **_: Any) -> dict[str, Any]:
        # Como el portal real sin tablas consolidadas: no hay archivos.
        return {"archivos": [], "errores": [], "filas": {}}


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


#: Extensión que sugiere el portal falso según el enlace clickeado.
EXT_POR_ACCION = {
    "_click_excel": ".xls",
    "_click_pdf": ".pdf",
    "_click_retenciones_coeficientes": ".xls",
    "_click_exportar_archivo": ".xls",
}


class _PortalGrabador(_SiferePortalTest):
    """Portal falso: registra URLs y descargas; escribe archivos reales."""

    def __init__(self) -> None:
        super().__init__(_SelectorRepresentado())
        self._representado_listo = True
        self.urls: list[str] = []
        self.descargas: list[tuple[str, str]] = []
        self.fallar: set[str] = set()

    async def abrir_url(self, url: str, **kwargs: Any) -> None:
        self.urls.append(url)

    async def descargar_con_nombre_sugerido(
        self, destino_dir: Path, base: str, accion: Any, **kwargs: Any
    ) -> Path:
        nombre_accion = getattr(accion, "__name__", "")
        self.descargas.append((nombre_accion, base))
        if nombre_accion in self.fallar:
            raise TargetUnavailableError("descarga simulada caída")
        ruta = Path(destino_dir) / f"{base}{EXT_POR_ACCION.get(nombre_accion, '.bin')}"
        ruta.write_bytes(nombre_accion.encode())
        return ruta


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


class _PortalSinSelector(_SiferePortalTest):
    """COMARB sin otros representados: mainMenu.do sin ``#selectedCuit``."""

    def __init__(self, encabezado: str) -> None:
        super().__init__(_SelectorRepresentado())
        self.encabezado = encabezado

    async def _buscar_selector_cuit(self) -> tuple[Any, Any]:
        return None, self.page

    async def abrir_url(self, url: str, **kwargs: Any) -> None:
        pass

    async def _representado_vigente(self) -> str | None:
        portal = SiferePortal(_PaginaConTexto(self.encabezado))
        return await portal._representado_vigente()


class _PaginaConTexto:
    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.frames: list[Any] = []

    def locator(self, *_: Any) -> Any:
        texto = self.texto

        class _Body:
            async def inner_text(self, **__: Any) -> str:
                return texto

        return _Body()


ENCABEZADO_PROPIO = (
    "LANZA ANABELLA CLAUDIA (27-24907029-2) representando a\n"
    "LANZA ANABELLA CLAUDIA (27-24907029-2)\nSistema SifereWeb"
)


def test_seleccionar_representado_sin_selector_acepta_el_cuit_vigente() -> None:
    portal = _PortalSinSelector(ENCABEZADO_PROPIO)
    asyncio.run(portal.seleccionar_representado("27249070292"))
    assert portal._representado_listo


def test_seleccionar_representado_sin_selector_rechaza_otro_cuit() -> None:
    portal = _PortalSinSelector(ENCABEZADO_PROPIO)
    with pytest.raises(TargetUnavailableError, match="selector de representado"):
        asyncio.run(portal.seleccionar_representado("20123456789"))
    assert not portal._representado_listo


# --- SIRCAR (port del bucle de V1: retencionIn/percepcionIn + Excel/PDF) ---


def test_consultar_sircar_entra_por_metodo_y_descarga_excel_y_pdf(tmp_path: Path) -> None:
    async def _ejecutar() -> tuple[_PortalGrabador, dict[str, Any], dict[str, Any]]:
        portal = _PortalGrabador()
        retencion = await portal.consultar_sircar(
            "retencion", 901, "202601", tmp_path, "20-12345678-9"
        )
        percepcion = await portal.consultar_sircar(
            "percepcion", "901", "202601", tmp_path, "20-12345678-9"
        )
        return portal, retencion, percepcion

    portal, retencion, percepcion = asyncio.run(_ejecutar())

    # V1 entra por retencionIn y percepcionIn una sola vez por método.
    assert portal.urls[0] == URL_SIRCAR_ENTRADA.format(metodo="retencion")
    assert "method=retencion&juris=901" in portal.urls[1]
    assert "cuitAgente=" in portal.urls[1]
    assert "mesDesde=01&anioDesde=2026&mesHasta=01&anioHasta=2026" in portal.urls[1]
    assert portal.urls[2] == URL_SIRCAR_ENTRADA.format(metodo="percepcion")
    assert "method=percepcion&juris=901" in portal.urls[3]
    assert len(portal.urls) == 4

    # Sin representado el nombre de archivo usa el CUIT (V1) y la extensión la
    # sugiere el portal.
    assert retencion["etiqueta"] == "Retenciones"
    assert [a["tipo"] for a in retencion["archivos"]] == ["xlsx", "pdf"]
    assert retencion["archivos"][0]["nombre"] == (
        "9 - 20123456789 - SIFERE - SIRCAR - Retenciones - 901 - 202601 - 20123456789.xls"
    )
    assert retencion["archivos"][1]["nombre"].endswith(".pdf")
    assert percepcion["archivos"][0]["nombre"] == (
        "9 - 20123456789 - SIFERE - SIRCAR - Percepciones - 901 - 202601 - 20123456789.xls"
    )
    assert (tmp_path / retencion["archivos"][0]["nombre"]).exists()
    assert retencion["errores"] == []


def test_consultar_sircar_usa_el_nombre_del_representado(tmp_path: Path) -> None:
    portal = _PortalGrabador()

    resumen = asyncio.run(
        portal.consultar_sircar(
            "retencion", 924, "202601", tmp_path, "20123456789", "Empresa SA"
        )
    )

    assert resumen["archivos"][0]["nombre"] == (
        "9 - 20123456789 - SIFERE - SIRCAR - Retenciones - 924 - 202601 - Empresa SA.xls"
    )


def test_consultar_sircar_registra_descarga_caida_sin_cortar(tmp_path: Path) -> None:
    portal = _PortalGrabador()
    portal.fallar = {"_click_pdf"}

    resumen = asyncio.run(
        portal.consultar_sircar("retencion", 901, "202601", tmp_path, "20123456789")
    )

    # En V1 cada descarga fallaba en silencio: el Excel queda y el PDF se anota.
    assert [a["tipo"] for a in resumen["archivos"]] == ["xlsx"]
    assert len(resumen["errores"]) == 1
    assert "No se pudo descargar SIRCAR Retenciones (pdf)" in resumen["errores"][0]


def test_consultar_sircar_rechaza_metodo_desconocido(tmp_path: Path) -> None:
    portal = _PortalGrabador()

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(
            portal.consultar_sircar("otro", 901, "202601", tmp_path, "20123456789")
        )

    assert error.value.diagnostic_code == "sifere_metodo_invalido"


# --- Consolidados SIRCREB y SIRTAC (port de V1 extraer_tabla_html y exports xlsx) ---

HTML_SIRCREB = """
<html><body>
<table id="retencion">
  <thead><tr><th>Jurisdicción</th><th>Importe</th></tr></thead>
  <tbody>
    <tr><td>901</td><td>1.500,00</td></tr>
    <tr><td>902</td><td>&amp;2.000,00</td></tr>
  </tbody>
</table>
<table id="coef">
  <thead><tr><th>Jurisdicción</th><th>Coeficiente</th></tr></thead>
  <tbody><tr><td>901</td><td>0,25</td></tr></tbody>
</table>
</body></html>
"""

HTML_SIRTAC = """
<table id="inclusion"><thead><tr><th>Jur</th><th>Monto</th><th>Extra</th></tr></thead>
<tbody><tr><td>901</td><td>10</td></tr></tbody></table>
<table id="retencion"><thead><tr><th>Operación</th><th>Importe</th></tr></thead>
<tbody><tr><td>901</td><td>25</td></tr></tbody></table>
"""


def test_extraer_tablas_sifere_lee_encabezados_y_filas() -> None:
    from bot_worker.runtime.portals.sifere import extraer_tablas_sifere

    encabezados, filas = extraer_tablas_sifere(HTML_SIRCREB, "retencion")

    assert encabezados == ["Jurisdicción", "Importe"]
    assert filas == [["901", "1.500,00"], ["902", "&2.000,00"]]


def test_extraer_tablas_sifere_aplica_las_reglas_de_v1() -> None:
    from bot_worker.runtime.portals.sifere import extraer_tablas_sifere

    # Fila más ancha que el encabezado: V1 antepone la columna "Acciones".
    html_acciones = (
        '<table id="t"><thead><tr><th>A</th><th>B</th></tr></thead>'
        "<tbody><tr><td>1</td><td>2</td><td>x</td></tr></tbody></table>"
    )
    assert extraer_tablas_sifere(html_acciones, "t") == (
        ["Acciones", "A", "B"],
        [["1", "2", "x"]],
    )

    # Encabezados de más: se recortan al ancho de la fila.
    assert extraer_tablas_sifere(HTML_SIRTAC, "inclusion") == (
        ["Jur", "Monto"],
        [["901", "10"]],
    )

    # Filas con anchos distintos: la tabla se descarta (V1: pandas fallaba).
    html_desigual = (
        '<table id="t"><thead><tr><th>A</th><th>B</th></tr></thead>'
        "<tbody><tr><td>1</td><td>2</td></tr><tr><td>3</td></tr></tbody></table>"
    )
    assert extraer_tablas_sifere(html_desigual, "t") == ([], [])

    # Sin thead, o sin filas, la tabla se descarta.
    html_sin_thead = '<table id="t"><tbody><tr><td>1</td></tr></tbody></table>'
    assert extraer_tablas_sifere(html_sin_thead, "t") == ([], [])
    assert extraer_tablas_sifere('<table id="retencion"><tbody></tbody></table>', "retencion") == ([], [])
    assert extraer_tablas_sifere(HTML_SIRTAC, "no-existe") == ([], [])


def test_escribir_xlsx_produce_hojas_legibles(tmp_path: Path) -> None:
    import zipfile
    from xml.etree import ElementTree as ET

    from bot_worker.runtime.portals.sifere import escribir_xlsx

    destino = tmp_path / "consolidado.xlsx"
    escribir_xlsx(
        destino,
        [("Retenciones", ["Jurisdicción", "Importe"], [["901", "1.500,00"]])],
    )

    assert zipfile.is_zipfile(destino)
    with zipfile.ZipFile(destino) as libro:
        assert {"xl/workbook.xml", "xl/worksheets/sheet1.xml"} <= set(libro.namelist())
        hoja = ET.fromstring(libro.read("xl/worksheets/sheet1.xml"))
        ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        textos = [t.text for t in hoja.iterfind(".//m:t", ns)]
        nombres = [
            hoja_xml.get("name")
            for hoja_xml in ET.fromstring(libro.read("xl/workbook.xml")).iterfind(".//m:sheet", ns)
        ]

    assert textos == ["Jurisdicción", "Importe", "901", "1.500,00"]
    assert nombres == ["Retenciones"]


class _PaginaSifereHtml(_PortalGrabador):
    """Pagina falsa: devuelve el HTML de SIRCREB o SIRTAC segun la URL abierta."""

    def __init__(self) -> None:
        super().__init__()
        self._html = ""
        self._pagina_falsa = SimpleNamespace(content=self._contenido)

    @property
    def page(self) -> Any:  # type: ignore[override]
        return self._pagina_falsa

    async def abrir_url(self, url: str, **kwargs: Any) -> None:
        self.urls.append(url)
        self._html = HTML_SIRCREB if "srbPadron" in url else HTML_SIRTAC

    async def _contenido(self) -> str:
        return self._html


def test_consultar_consolidados_escribe_sircreb_y_sirtac(tmp_path: Path) -> None:
    portal = _PaginaSifereHtml()

    resumen = asyncio.run(
        portal.consultar_consolidados("202601", tmp_path, "20-12345678-9")
    )

    # Ahora el nombre usa el CUIT cuando no hay representado (V1).
    assert resumen["archivos"] == [
        "9 - 20123456789 - SIFERE - SIRCREB - Tablas - 202601 - 20123456789.xlsx",
        "9 - 20123456789 - SIFERE - SIRCREB - 202601 - 20123456789.xls",
        "9 - 20123456789 - SIFERE - SIRTAC - Tablas - 202601 - 20123456789.xlsx",
        "9 - 20123456789 - SIFERE - SIRTAC - jurisdicciones - 202601 - 20123456789.xlsx",
        "9 - 20123456789 - SIFERE - SIRTAC - operaciones - 202601 - 20123456789.xlsx",
    ]
    assert resumen["errores"] == []
    assert resumen["filas"] == {
        "Retenciones": 2,
        "Coeficientes": 1,
        "Distribución Jurisdicciones": 1,
        "Operaciones Informadas": 1,
    }
    assert any("srbPadron.do?method=detalleMensual&anio=2026&mes=01" in u for u in portal.urls)
    assert any("srtPadron.do?method=retenciones&cuit=20123456789&periodo=202601" in u for u in portal.urls)
    assert (tmp_path / resumen["archivos"][0]).exists()

    # V1: las dos descargas SIRCREB comparten nombre base; la segunda pisa a la
    # primera cuando el portal sugiere la misma extensión.
    assert [base for _, base in portal.descargas] == [
        "9 - 20123456789 - SIFERE - SIRCREB - 202601 - 20123456789",
        "9 - 20123456789 - SIFERE - SIRCREB - 202601 - 20123456789",
    ]
    archivo = tmp_path / "9 - 20123456789 - SIFERE - SIRCREB - 202601 - 20123456789.xls"
    assert archivo.read_bytes() == b"_click_exportar_archivo"


def test_consultar_consolidados_usa_el_nombre_del_representado(tmp_path: Path) -> None:
    portal = _PaginaSifereHtml()

    resumen = asyncio.run(
        portal.consultar_consolidados("202601", tmp_path, "20123456789", "Empresa SA")
    )

    assert resumen["archivos"][0] == (
        "9 - 20123456789 - SIFERE - SIRCREB - Tablas - 202601 - Empresa SA.xlsx"
    )
    assert resumen["archivos"][2] == (
        "9 - 20123456789 - SIFERE - SIRTAC - Tablas - 202601 - Empresa SA.xlsx"
    )
    assert resumen["archivos"][3].endswith("- 202601 - Empresa SA.xlsx")


def test_consultar_consolidados_registra_descarga_caida_sin_cortar(tmp_path: Path) -> None:
    portal = _PaginaSifereHtml()
    portal.fallar = {"_click_retenciones_coeficientes"}

    resumen = asyncio.run(
        portal.consultar_consolidados("202601", tmp_path, "20123456789")
    )

    # La primera descarga SIRCREB cae, la segunda y el resto siguen (V1).
    assert resumen["archivos"][0].endswith("SIRCREB - Tablas - 202601 - 20123456789.xlsx")
    assert resumen["archivos"][1] == (
        "9 - 20123456789 - SIFERE - SIRCREB - 202601 - 20123456789.xls"
    )
    assert resumen["archivos"][-1].endswith("SIRTAC - operaciones - 202601 - 20123456789.xlsx")
    assert len(resumen["errores"]) == 1
    assert resumen["errores"][0].startswith(
        "No se pudo descargar Retenciones y coeficientes:"
    )
    assert len(resumen["archivos"]) == 5


def test_consultar_consolidados_exige_representado(tmp_path: Path) -> None:
    portal = _PaginaSifereHtml()
    portal._representado_listo = False

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.consultar_consolidados("202601", tmp_path, "20123456789"))
    assert error.value.diagnostic_code == "sifere_representado_faltante"


def test_consultar_sircar_exige_representado(tmp_path: Path) -> None:
    portal = _PortalGrabador()
    portal._representado_listo = False

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.consultar_sircar("retencion", 901, "202601", tmp_path, "20123456789"))
    assert error.value.diagnostic_code == "sifere_representado_faltante"


# --- Plugin: manifiesto, loop SIRCAR y consolidados ---


def test_plugin_sifere_sube_sircar_y_consolidados_por_defecto_como_v1(tmp_path: Path) -> None:
    class _Servicio(_ServicioSifere):
        async def consultar_consolidados(self, **kwargs: Any) -> dict[str, Any]:
            (tmp_path / "sircreb.xlsx").write_bytes(b"PK\x03\x04sircreb")
            return {
                "archivos": ["sircreb.xlsx"],
                "errores": ["No se pudo descargar Exportar archivo con: caída"],
                "filas": {},
            }

    class _Store:
        def __init__(self) -> None:
            self.subidos: list[tuple[str, str]] = []

        def resolve(self, nombre: str) -> Path:
            return tmp_path / nombre

        async def upload(self, artifact_id: str, nombre: str) -> dict[str, Any]:
            self.subidos.append((artifact_id, nombre))
            return {"sha256": "abc", "size_bytes": 12}

    async def _ejecutar(consolidados: bool) -> tuple[dict[str, Any], _Store, list[Any]]:
        servicio = _Servicio()
        store = _Store()
        plugin = SiferePlugin()
        _, entrada = await plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": "20123456789",
                "periodo": "202401",
                "jurisdicciones": [901],
                "subir": True,
                "consolidados": consolidados,
            }
        )
        runtime = SimpleNamespace(
            cancellation=_Cancelacion(),
            event_sink=_Eventos(),
            artifact_store=store,
        )
        datos, _, errores = await plugin._consultar(servicio, entrada, runtime)
        return datos, store, errores

    datos_sin, store_sin, errores_sin = asyncio.run(_ejecutar(False))
    assert "consolidados" not in datos_sin
    assert [aid for aid, _ in store_sin.subidos] == [
        f"{ID_ARTEFACTO_SIRCAR_XLSX}_retencion_901",
        f"{ID_ARTEFACTO_SIRCAR_PDF}_retencion_901",
        f"{ID_ARTEFACTO_SIRCAR_XLSX}_percepcion_901",
        f"{ID_ARTEFACTO_SIRCAR_PDF}_percepcion_901",
    ]
    assert errores_sin == []
    assert [fila["metodo"] for fila in datos_sin["jurisdicciones"]] == [
        "retencion",
        "percepcion",
    ]
    assert datos_sin["jurisdicciones"][1]["etiqueta"] == "Percepciones"
    assert datos_sin["jurisdicciones"][0]["archivos"][0]["tipo"] == "xlsx"

    datos_con, store_con, errores_con = asyncio.run(_ejecutar(True))
    assert datos_con["consolidados"] == [
        {"archivo": "sircreb.xlsx", "sha256": "abc", "size_bytes": 12}
    ]
    ids_con = [aid for aid, _ in store_con.subidos]
    assert ids_con[-1] == f"{ID_ARTEFACTO_CONSOLIDADO}_1"
    # La descarga SIRCREB caída queda como error no fatal (V1) sin sumar artefactos.
    assert len(errores_con) == 1
    assert "Exportar archivo con" in errores_con[0].internal_diagnostic


def test_jurisdicciones_vacias_no_consultan_sircar_y_no_fallan(tmp_path: Path) -> None:
    class _Store:
        def __init__(self) -> None:
            self.subidos: list[str] = []

        def resolve(self, nombre: str) -> Path:
            return tmp_path / nombre

        async def upload(self, artifact_id: str, nombre: str) -> dict[str, Any]:
            self.subidos.append(artifact_id)
            return {"sha256": "abc", "size_bytes": 4}

    servicio = _ServicioSifere()
    store = _Store()
    plugin = SiferePlugin()
    _, entrada = asyncio.run(
        plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": "20123456789",
                "periodo": "202401",
                "jurisdicciones": [],
            }
        )
    )
    runtime = SimpleNamespace(
        cancellation=_Cancelacion(),
        event_sink=_Eventos(),
        artifact_store=store,
    )

    datos, artefactos, errores = asyncio.run(plugin._consultar(servicio, entrada, runtime))

    assert servicio.sircar == []
    assert artefactos == [] and store.subidos == []
    assert errores == []
    assert datos["jurisdicciones"] == []
    # Los consolidados del período siguen corriendo (V1 consultaba igual).
    assert datos["consolidados"] == []


def test_fallo_de_consolidados_no_pierde_el_sircar(tmp_path: Path) -> None:
    class _Servicio(_ServicioSifere):
        async def consultar_consolidados(self, **_: Any) -> dict[str, Any]:
            raise TargetUnavailableError(
                "la tabla SIRCREB no respondió",
                diagnostic_code="sifere_sircreb_fallo",
            )

    class _Store:
        def __init__(self) -> None:
            self.subidos: list[str] = []

        def resolve(self, nombre: str) -> Path:
            return tmp_path / nombre

        async def upload(self, artifact_id: str, nombre: str) -> dict[str, Any]:
            self.subidos.append(artifact_id)
            return {"sha256": "abc", "size_bytes": 4}

    plugin = SiferePlugin()
    _, entrada = asyncio.run(
        plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": "20123456789",
                "periodo": "202401",
                "jurisdicciones": [901],
            }
        )
    )
    store = _Store()
    runtime = SimpleNamespace(
        cancellation=_Cancelacion(),
        event_sink=_Eventos(),
        artifact_store=store,
    )

    datos, _, errores = asyncio.run(plugin._consultar(_Servicio(), entrada, runtime))

    # Lo ya subido se conserva; el fallo queda como error no fatal.
    assert store.subidos == [
        f"{ID_ARTEFACTO_SIRCAR_XLSX}_retencion_901",
        f"{ID_ARTEFACTO_SIRCAR_PDF}_retencion_901",
        f"{ID_ARTEFACTO_SIRCAR_XLSX}_percepcion_901",
        f"{ID_ARTEFACTO_SIRCAR_PDF}_percepcion_901",
    ]
    assert len(datos["jurisdicciones"]) == 2
    assert len(errores) == 1
    assert "sifere_sircreb_fallo" in errores[0].internal_diagnostic


def test_manifiesto_sifere_declara_las_familias_de_v1() -> None:
    familias = {spec.nombre: spec for spec in SiferePlugin.manifest.artefactos_produce}
    assert set(familias) == {"sifere_sircar_xlsx", "sifere_sircar_pdf", "sifere_consolidado"}
    assert familias["sifere_sircar_xlsx"].content_types[0].endswith("spreadsheetml.sheet")
    assert familias["sifere_sircar_pdf"].content_types == ("application/pdf",)


def test_manifiesto_autoriza_sircar_por_metodo_y_jurisdiccion(tmp_path: Path) -> None:
    declarados = {
        spec.nombre: (spec.content_types, spec.max_bytes)
        for spec in SiferePlugin.manifest.artefactos_produce
    }
    store = ArtifactStore(tmp_path, slots={}, declarados=declarados)
    (tmp_path / "sircar.xls").write_bytes(b"PK\x03\x04ejemplo")

    async def _ejecutar() -> list[str]:
        ids = []
        for artifact_id in (
            "sifere_sircar_xlsx_retencion_901",
            "sifere_sircar_xlsx_percepcion_902",
            "sifere_sircar_pdf_retencion_901",
            "sifere_consolidado_1",
        ):
            subido = await store.upload(artifact_id, "sircar.xls")
            ids.append(subido["artifact_id"])
        return ids

    assert asyncio.run(_ejecutar()) == [
        "sifere_sircar_xlsx_retencion_901",
        "sifere_sircar_xlsx_percepcion_902",
        "sifere_sircar_pdf_retencion_901",
        "sifere_consolidado_1",
    ]

    async def _no_autorizado() -> None:
        with pytest.raises(ValueError):
            await store.upload("sifere_desconocido_1", "sircar.xls")

    asyncio.run(_no_autorizado())


def test_schema_descarta_sobre_sellado_y_acepta_alias_cuit_representado() -> None:
    entrada = SifereConsultarInput.model_validate(
        {
            "cuit_representado": "20-12345678-9",
            "periodo": "202601",
            "representado": "Empresa SA",
            "cuit_representante": "30712345678",
            "clave_representante": "secreta",
            "clave_encriptada": "xxx",
            "proxy_request": True,
            "jurisdicciones": [],
        }
    )

    assert entrada.representado_cuit == "20123456789"
    assert entrada.representado_nombre == "Empresa SA"
    assert entrada.jurisdicciones == []


def test_schema_representado_nombre_nullable_y_jurisdicciones_por_defecto() -> None:
    entrada = SifereConsultarInput.model_validate(
        {"representado_cuit": "20123456789", "periodo": "202601"}
    )
    assert entrada.representado_nombre is None
    assert entrada.jurisdicciones == list(range(901, 925))

    vacio = SifereConsultarInput.model_validate(
        {
            "representado_cuit": "20123456789",
            "periodo": "202601",
            "representado_nombre": "   ",
        }
    )
    assert vacio.representado_nombre is None


def test_validate_acepta_el_sobre_sellado_de_v1() -> None:
    from bot_worker.bots.sifere.plugin import SiferePlugin as _Plugin

    async def _ejecutar() -> Any:
        plugin = _Plugin()
        _, entrada = await plugin.validate(
            {
                "operacion": "consultar",
                "cuit_representado": "20123456789",
                "periodo": "202601",
                "representado": None,
                "cuit_representante": "30712345678",
                "clave_representante": "secreta",
                "clave_encriptada": "xxx",
                "proxy_request": True,
            }
        )
        return entrada

    entrada = asyncio.run(_ejecutar())
    assert entrada.representado_cuit == "20123456789"
    assert entrada.representado_nombre is None


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
