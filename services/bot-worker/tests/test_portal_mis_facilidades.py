"""Pruebas unitarias del portal Mis Facilidades (sólo lectura)."""
from __future__ import annotations

import asyncio
import zipfile
from xml.etree import ElementTree as ET

from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.mis_facilidades import MisFacilidadesPortal


class _Locator:
    def __init__(self, cantidad: int = 0) -> None:
        self._cantidad = cantidad

    @property
    def first(self) -> "_Locator":
        return self

    async def count(self) -> int:
        return self._cantidad

    async def wait_for(self, **_: object) -> None:
        return None

    async def is_visible(self, **_: object) -> bool:
        return self._cantidad > 0


class _Page:
    def __init__(self, filas: list[dict[str, str]]) -> None:
        self.filas = filas

    def locator(self, selector: str) -> _Locator:
        return _Locator(1 if selector == "table.searchTable" else 0)

    async def evaluate(self, script: str) -> list[dict[str, str]]:
        assert "table.searchTable tbody tr" in script
        return self.filas


class _Opcion:
    def __init__(self, valor: str, etiqueta: str) -> None:
        self.valor = valor
        self.etiqueta = etiqueta

    async def get_attribute(self, nombre: str) -> str:
        return self.valor if nombre == "value" else ""

    async def text_content(self) -> str:
        return self.etiqueta


class _Opciones:
    def __init__(self, opciones: list[_Opcion]) -> None:
        self.opciones = opciones

    async def count(self) -> int:
        return len(self.opciones)

    def nth(self, indice: int) -> _Opcion:
        return self.opciones[indice]


class _Combo(_Locator):
    def __init__(self, opciones: list[_Opcion]) -> None:
        super().__init__(1)
        self.opciones = _Opciones(opciones)
        self.seleccion: tuple[str, str] | None = None

    def locator(self, _: str) -> _Opciones:
        return self.opciones

    async def select_option(self, *, value: str = "", label: str = "") -> None:
        self.seleccion = (value, label)


class _PaginaSeleccion:
    def __init__(self, combo: _Combo) -> None:
        self.combo = combo

    async def wait_for_load_state(self, *_: object, **__: object) -> None:
        return None

    def locator(self, selector: str) -> _Combo:
        assert selector == "#ContentPlaceHolder1_ddlCUIT"
        return self.combo

    def get_by_role(self, *_: object, **__: object) -> _Locator:
        return _Locator(0)


def test_registro_y_listado_de_planes() -> None:
    assert portal_para("mis_facilidades") is MisFacilidadesPortal
    page = _Page([
        {
            "numero": "P-001",
            "denominacion": "Contribuyente de prueba",
            "situacion": "Plan Vigente",
            "detalle_id": "ContentPlaceHolder1_rpt_detallePlan_0",
        },
        {
            "numero": "P-002",
            "denominacion": "Contribuyente de prueba",
            "situacion": "Plan Cancelado",
            "detalle_id": "ContentPlaceHolder1_rpt_detallePlan_1",
        },
        {
            "numero": "P-003",
            "denominacion": "Contribuyente de prueba",
            "situacion": "Plan sin detalle",
            "detalle_id": "",
        },
    ])

    planes = asyncio.run(MisFacilidadesPortal(page).listar_planes())

    assert [plan["numero"] for plan in planes] == ["P-001", "P-002"]
    assert planes[0]["situacion"] == "Plan Vigente"


def test_selecciona_representado_en_combo_especifico() -> None:
    combo = _Combo([_Opcion("42", "Empresa ficticia 20-12345678-9")])
    portal = MisFacilidadesPortal(_PaginaSeleccion(combo))

    asyncio.run(portal.seleccionar_representado("20-12345678-9"))

    assert combo.seleccion == ("42", "")


def test_xlsx_es_openxml_valido_y_con_varias_hojas(tmp_path) -> None:
    destino = tmp_path / "planes.xlsx"
    secciones = [
        {"titulo": "Pagos", "headers": ["Fecha", "Importe"], "rows": [["2026-01-01", "100"]]},
        {"titulo": "Cuotas", "headers": ["Nro", "Estado"], "rows": [["1", "Pagada"]]},
    ]

    MisFacilidadesPortal._escribir_xlsx(destino, secciones, "P-001")
    MisFacilidadesPortal._validar_archivo(
        destino, b"PK\x03\x04", "mis_facilidades_xlsx_empty"
    )

    with zipfile.ZipFile(destino) as libro:
        assert libro.testzip() is None
        raiz = ET.fromstring(libro.read("xl/workbook.xml"))
        hojas = raiz.findall("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets/{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheet")
        assert [hoja.attrib["name"] for hoja in hojas] == ["Pagos", "Cuotas"]
        hoja_pagos = ET.fromstring(libro.read("xl/worksheets/sheet1.xml"))
        textos = [n.text for n in hoja_pagos.iter("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t")]
        assert textos == ["Fecha", "Importe", "2026-01-01", "100"]


def test_pdf_html_escapa_datos_del_plan() -> None:
    html = MisFacilidadesPortal._html_reporte(
        "<plan>",
        [{"titulo": "Pagos", "headers": ["Importe"], "rows": [["<script>"]]}],
    )

    assert "&lt;plan&gt;" in html
    assert "&lt;script&gt;" in html


# --- Expansion previa de cuotas y obligaciones (port de V1) -----------------------


class _TablaExpandible(_Locator):
    """Tabla cuyas filas ocultas solo aparecen despues de expandirse."""

    def __init__(self, pagina: _PaginaVista, selector: str) -> None:
        super().__init__(1)
        self.pagina = pagina
        self.selector = selector

    def nth(self, _: int) -> _TablaExpandible:
        return self

    async def evaluate(self, _script: str) -> dict[str, object]:
        if self.pagina.expandidas.get(self.selector):
            return {"headers": ["Cuota"], "rows": [["1"], ["2"], ["3"]]}
        return {"headers": ["Cuota"], "rows": [["1"]]}


class _Visible(_Locator):
    """Elemento que siempre existe y es visible (indicador de la pantalla de detalle)."""

    def __init__(self) -> None:
        super().__init__(1)

    async def is_visible(self, **_: object) -> bool:
        return True


class _BotonVista(_Locator):
    def __init__(self, nombre: str) -> None:
        super().__init__(1)
        self.nombre = nombre

    @property
    def first(self) -> _BotonVista:
        return self

    async def click(self, **_: object) -> None:
        return None


class _PaginaVista:
    """Pagina de detalle con las vistas de Pagos, Plan de Pago y Obligaciones."""

    def __init__(self) -> None:
        self.expandidas: dict[str, bool] = {}
        self.scripts: list[str] = []
        self.url = "https://serviciosweb.afip.gob.ar/mis_facilidades/"

    def get_by_role(self, _: str, *, name: object = None, **__: object) -> _Locator:
        # re.escape agrega barras invertidas a los espacios ("Plan\ de\ Pago"): se quitan.
        pattern = str(getattr(name, "pattern", "")).replace("\\", "").lower()
        for nombre in ("ver pagos", "plan de pago", "obligaciones impositivas"):
            if nombre in pattern:
                return _BotonVista(nombre)
        return _Locator(0)

    def locator(self, selector: str) -> _Locator:
        if selector in ("#tableDetallePagos", "#tableDetallePlanPago", "table[id^='tableDetalleDeuda']"):
            return _TablaExpandible(self, selector)
        if selector == "#ContentPlaceHolder1_btnVolver":
            # Indicador de detalle: _volver_a_detalle exige verlo tras volver de cada vista.
            return _Visible()
        return _Locator(0)

    async def evaluate(self, script: str, selector: str | None = None) -> bool:
        if selector is not None:
            self.scripts.append(selector)
            self.expandidas[selector] = True
        return True

    async def wait_for_timeout(self, *_: object) -> None:
        return None

    async def wait_for_load_state(self, *_: object, **__: object) -> None:
        return None

    async def wait_for(self, *_: object, **__: object) -> None:
        return None


def test_expansion_muestra_filas_de_cuotas_antes_de_leer() -> None:
    from bot_worker.runtime.portals.mis_facilidades import _SELECTOR_CUOTAS

    pagina = _PaginaVista()
    portal = MisFacilidadesPortal(pagina)

    tabla = asyncio.run(
        portal._capturar_vista("Plan de Pago", _SELECTOR_CUOTAS, "Cuotas", obligatorio=False)
    )

    assert pagina.expandidas.get(_SELECTOR_CUOTAS) is True
    assert tabla is not None
    assert len(tabla["rows"]) == 3


def test_expansion_de_obligaciones_se_aplica_a_su_tabla() -> None:
    from bot_worker.runtime.portals.mis_facilidades import _SELECTOR_OBLIGACIONES

    pagina = _PaginaVista()
    portal = MisFacilidadesPortal(pagina)

    asyncio.run(
        portal._capturar_vista(
            "Obligaciones Impositivas",
            _SELECTOR_OBLIGACIONES,
            "Obligaciones",
            obligatorio=False,
            multiples=True,
        )
    )

    assert pagina.expandidas.get(_SELECTOR_OBLIGACIONES) is True


def test_tabla_de_pagos_no_se_expande() -> None:
    from bot_worker.runtime.portals.mis_facilidades import _SELECTOR_CUOTAS

    pagina = _PaginaVista()
    portal = MisFacilidadesPortal(pagina)

    asyncio.run(portal._capturar_vista("Ver Pagos", "#tableDetallePagos", "Pagos", obligatorio=False))

    assert "#tableDetallePagos" not in pagina.expandidas
    assert _SELECTOR_CUOTAS not in pagina.expandidas


def test_fallo_de_expansion_no_corta_la_captura() -> None:
    from bot_worker.runtime.portals.mis_facilidades import _SELECTOR_CUOTAS

    class _PaginaQueFalla(_PaginaVista):
        async def evaluate(self, script: str, selector: str | None = None) -> bool:
            raise RuntimeError("script rechazado")

    portal = MisFacilidadesPortal(_PaginaQueFalla())

    tabla = asyncio.run(
        portal._capturar_vista("Plan de Pago", _SELECTOR_CUOTAS, "Cuotas", obligatorio=False)
    )

    assert tabla is not None
    assert len(tabla["rows"]) == 1



# --- Paridad V1: PDF por vista, denominacion, errores por plan y alias -----


def test_nombre_base_plan_sigue_el_patron_de_v1() -> None:
    from bot_worker.bots.mis_facilidades.plugin import nombre_base_plan

    # V1 build_report_path: "{fin} - {cuit} - {label} - {plan} - {denominacion}"
    assert nombre_base_plan("20-12345678-9", "P-001", "EMPRESA SA") == (
        "9 - 20123456789 - P-001 - EMPRESA SA"
    )
    # Caracteres invalidos de nombre de archivo se reemplazan (safe_filename).
    assert nombre_base_plan("20123456789", "P/1", "A:B") == "9 - 20123456789 - P_1 - A_B"
    assert nombre_base_plan("20123456789", "", "") == "9 - 20123456789 - plan - denominacion"


def test_etiqueta_de_vista_usa_los_nombres_de_v1() -> None:
    from bot_worker.runtime.portals.mis_facilidades import _etiqueta_vista

    assert _etiqueta_vista("Pagos") == "pagos"
    assert _etiqueta_vista("Plan de Pago") == "cuotas"
    assert _etiqueta_vista("Obligaciones Impositivas") == "obligaciones"
    assert _etiqueta_vista("Obligaciones Previsionales") == "obligaciones_previsionales"


def test_alias_lista_exclusion_situacion_se_acepta() -> None:
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin

    async def _validar(payload: dict) -> object:
        return (await MisFacilidadesPlugin().validate(payload))[1]

    base = {"operacion": "consultar", "representado_cuit": "20123456789"}
    alias = asyncio.run(_validar({**base, "lista_exclusion_situacion": ["Plan Caduco"]}))
    nombre = asyncio.run(_validar({**base, "situacion_excluyente": ["Plan Caduco"]}))
    assert alias.situacion_excluyente == ["Plan Caduco"]
    assert nombre.situacion_excluyente == ["Plan Caduco"]


def test_schema_publica_el_alias_lista_exclusion_situacion() -> None:
    from bot_worker.bots.mis_facilidades.schema import esquema_entrada

    assert "lista_exclusion_situacion" in esquema_entrada()["properties"]


def _runtime_mf(tmp_path: Path):
    from types import SimpleNamespace

    from bot_worker.runtime.context import ArtifactStore

    class _Store(ArtifactStore):
        pass

    async def _no_op(*_: Any, **__: Any) -> None:
        return None

    return SimpleNamespace(
        cancellation=SimpleNamespace(raise_if_cancelled=_no_op),
        event_sink=SimpleNamespace(progress=_no_op),
        artifact_store=ArtifactStore(tmp_path, {}),
    )


class _ServicioMF:
    """Servicio falso: planes, vistas PDF por plan y excel por plan."""

    def __init__(self, planes: list[dict[str, str]], fallos: set[str] | None = None) -> None:
        self.planes = planes
        self.fallos = fallos or set()
        self.pdf_pedidos: list[str] = []

    async def listar_planes(self) -> list[dict[str, str]]:
        return self.planes

    async def exportar_vistas_pdf(self, *, numero: str, destino_dir: Path, base: str) -> list[dict]:
        from bot_worker.bots.errors import TargetUnavailableError

        self.pdf_pedidos.append(numero)
        if numero in self.fallos:
            raise TargetUnavailableError("sin vistas", diagnostic_code="mis_facilidades_view_missing")
        destino_dir.mkdir(parents=True, exist_ok=True)
        salida = []
        for etiqueta in ("pagos", "cuotas"):
            ruta = destino_dir / f"{base} - {etiqueta}.pdf"
            ruta.write_bytes(b"%PDF-1.4 vista")
            salida.append({"vista": etiqueta, "titulo": etiqueta, "destino": ruta})
        return salida

    async def exportar_plan_xlsx(self, *, numero: str, destino: Path) -> Path:
        destino.write_bytes(b"PK\x03\x04tablas")
        return destino


def test_consultar_genera_un_pdf_por_vista_con_nombre_v1(tmp_path: Path) -> None:
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin

    plugin = MisFacilidadesPlugin()
    _, entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
        "denominacion": "EMPRESA SA",
        "subir_archivos": False,
    }))
    servicio = _ServicioMF([{"numero": "P-001", "situacion": "Plan Vigente", "denominacion": "X"}])

    datos, _, errores = asyncio.run(
        plugin._consultar(servicio, entrada, _runtime_mf(tmp_path), "20123456789")
    )

    plan = datos["planes"][0]
    assert errores == []
    assert plan["denominacion"] == "EMPRESA SA"
    assert [v["vista"] for v in plan["pdf_vistas"]] == ["pagos", "cuotas"]
    assert plan["pdf_vistas"][0]["archivo"] == "9 - 20123456789 - P-001 - EMPRESA SA - pagos.pdf"
    assert plan["xlsx"]["archivo"] == "9 - 20123456789 - P-001 - EMPRESA SA - tablas.xlsx"


def test_denominacion_del_listado_se_usa_si_el_request_no_la_trae(tmp_path: Path) -> None:
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin

    plugin = MisFacilidadesPlugin()
    _, entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
        "subir_archivos": False,
        "incluir_xlsx": False,
    }))
    servicio = _ServicioMF([{"numero": "P-002", "situacion": "Vigente", "denominacion": "DEL LISTADO"}])

    datos, _, _ = asyncio.run(
        plugin._consultar(servicio, entrada, _runtime_mf(tmp_path), "20123456789")
    )

    assert datos["planes"][0]["denominacion"] == "DEL LISTADO"


def test_un_plan_con_error_no_corta_los_demas(tmp_path: Path) -> None:
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin

    plugin = MisFacilidadesPlugin()
    _, entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
        "subir_archivos": False,
        "incluir_xlsx": False,
    }))
    servicio = _ServicioMF(
        [
            {"numero": "P-001", "situacion": "Vigente", "denominacion": "A"},
            {"numero": "P-002", "situacion": "Vigente", "denominacion": "B"},
        ],
        fallos={"P-001"},
    )

    datos, _, errores = asyncio.run(
        plugin._consultar(servicio, entrada, _runtime_mf(tmp_path), "20123456789")
    )

    assert servicio.pdf_pedidos == ["P-001", "P-002"]
    assert [p["numero"] for p in datos["planes"]] == ["P-001", "P-002"]
    assert len(errores) == 1
    assert "P-001" in errores[0].internal_diagnostic
    assert "mis_facilidades_view_missing" in errores[0].internal_diagnostic
