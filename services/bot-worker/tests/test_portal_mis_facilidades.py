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

    async def wait_for(self, **kwargs: object) -> None:
        self.pagina.esperas.append(dict(kwargs))

    def locator(self, _: str) -> _FilaEsperable:
        return _FilaEsperable(self.pagina)

    async def evaluate(self, _script: str) -> dict[str, object]:
        if self.pagina.expandidas.get(self.selector):
            return {"headers": ["Cuota"], "rows": [["1"], ["2"], ["3"]]}
        return {"headers": ["Cuota"], "rows": [["1"]]}


class _FilaEsperable(_Locator):
    def __init__(self, pagina: _PaginaVista) -> None:
        super().__init__(1)
        self.pagina = pagina

    async def wait_for(self, **kwargs: object) -> None:
        self.pagina.esperas.append(dict(kwargs))


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
        self.esperas: list[dict[str, object]] = []
        self.url = "https://serviciosweb.afip.gob.ar/mis_facilidades/"

    def get_by_role(self, _: str, *, name: object = None, **__: object) -> _Locator:
        # re.escape agrega barras invertidas a los espacios ("Plan\ de\ Pago"): se quitan.
        pattern = str(getattr(name, "pattern", "")).replace("\\", "").lower()
        for nombre in (
            "ver pagos",
            "plan de pago",
            "obligaciones impositivas",
            "obligaciones previsionales",
        ):
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


def test_espera_tabla_y_primera_fila_20s_como_v1() -> None:
    pagina = _PaginaVista()
    portal = MisFacilidadesPortal(pagina)

    asyncio.run(
        portal._capturar_vista(
            "Ver Pagos", "#tableDetallePagos", "Pagos", obligatorio=True
        )
    )

    assert pagina.esperas == [
        {"state": "visible", "timeout": 20_000},
        {"state": "visible", "timeout": 20_000},
    ]


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


def test_vista_opcional_con_boton_y_sin_tabla_se_exporta_vacia() -> None:
    class _PaginaSinTabla(_PaginaVista):
        def locator(self, selector: str) -> _Locator:
            if selector.startswith("table[id^='tableDetalleDeuda']"):
                return _Locator(0)
            return super().locator(selector)

    portal = MisFacilidadesPortal(_PaginaSinTabla())
    secciones = asyncio.run(
        portal._capturar_vista(
            "Obligaciones Previsionales",
            "table[id^='tableDetalleDeuda']",
            "Obligaciones Previsionales",
            obligatorio=False,
            multiples=True,
        )
    )

    assert secciones == [
        {"titulo": "Obligaciones Previsionales", "headers": [], "rows": []}
    ]


def test_error_de_una_seccion_no_corta_las_siguientes() -> None:
    portal = MisFacilidadesPortal(_PaginaVista())
    llamadas: list[str] = []

    async def _abrir(_: str) -> None:
        return None

    async def _capturar(
        nombre: str,
        _selector: str,
        titulo: str,
        *,
        obligatorio: bool,
        multiples: bool = False,
    ) -> object:
        llamadas.append(nombre)
        if nombre == "Ver Pagos":
            raise RuntimeError("dato sensible de prueba")
        if nombre == "Plan de Pago":
            return {"titulo": titulo, "headers": ["Cuota"], "rows": [["1"]]}
        return [] if multiples else None

    async def _volver(*, suprimir_error: bool = False) -> None:
        return None

    portal._abrir_plan = _abrir  # type: ignore[method-assign]
    portal._capturar_vista = _capturar  # type: ignore[method-assign]
    portal._volver_al_listado = _volver  # type: ignore[method-assign]

    secciones = asyncio.run(portal._obtener_secciones("P-001"))

    assert llamadas == [
        "Ver Pagos",
        "Plan de Pago",
        "Obligaciones Impositivas",
        "Obligaciones Previsionales",
    ]
    assert [seccion["titulo"] for seccion in secciones] == ["Cuotas"]
    assert portal.errores_de_plan("P-001") == [
        {"seccion": "Pagos", "codigo": "RuntimeError"}
    ]


def test_apertura_de_detalle_usa_timeout_legacy_de_15s() -> None:
    class _ControlPlan(_Locator):
        def __init__(self) -> None:
            super().__init__(1)
            self.timeouts: list[int] = []

        async def scroll_into_view_if_needed(self, **_: object) -> None:
            return None

        async def click(self, **kwargs: object) -> None:
            self.timeouts.append(int(kwargs["timeout"]))

    class _PaginaPlan:
        def __init__(self) -> None:
            self.control = _ControlPlan()

        def locator(self, selector: str) -> _Locator:
            if selector == "#detalle-1":
                return self.control
            if selector == "#ContentPlaceHolder1_CabeceraPlanesEnviados_cab_nroPlan":
                return _Visible()
            return _Locator(0)

        async def wait_for_load_state(self, *_: object, **__: object) -> None:
            return None

        async def wait_for_timeout(self, *_: object) -> None:
            return None

    pagina = _PaginaPlan()
    portal = MisFacilidadesPortal(pagina)
    portal._detalle_por_numero["P-001"] = "detalle-1"

    async def _volver(*, suprimir_error: bool = False) -> None:
        return None

    portal._volver_al_listado = _volver  # type: ignore[method-assign]
    asyncio.run(portal._abrir_plan("P-001"))

    assert pagina.control.timeouts == [15_000]



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
        work_dir=tmp_path,
    )


class _ServicioMF:
    """Servicio falso: planes, vistas PDF por plan y excel por plan."""

    def __init__(
        self,
        planes: list[dict[str, str]],
        fallos: set[str] | None = None,
        fallos_genericos: set[str] | None = None,
    ) -> None:
        self.planes = planes
        self.fallos = fallos or set()
        self.fallos_genericos = fallos_genericos or set()
        self.pdf_pedidos: list[str] = []

    async def listar_planes(self) -> list[dict[str, str]]:
        return self.planes

    async def fecha_consolidacion(self, numero: str) -> str | None:
        return getattr(self, "fechas", {}).get(numero)

    async def exportar_vistas_pdf(self, *, numero: str, destino_dir: Path, base: str) -> list[dict]:
        from bot_worker.bots.errors import TargetUnavailableError

        self.pdf_pedidos.append(numero)
        if numero in self.fallos_genericos:
            raise RuntimeError("marcador_sensible_no_debe_publicarse")
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


def test_consultar_sube_pdfs_de_subcarpeta_y_xlsx(tmp_path: Path) -> None:
    """Regresion: los PDF viven en ``<base>.vistas`` y deben subirse igual."""
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin
    from bot_worker.runtime.context import ArtifactStore

    plugin = MisFacilidadesPlugin()
    _, entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
        "denominacion": "EMPRESA SA",
    }))
    runtime = _runtime_mf(tmp_path)
    runtime.artifact_store = ArtifactStore(
        tmp_path,
        {},
        declarados={"plan_pdf": (("application/pdf",), 1_000_000),
                    "planes_xlsx": ((), 1_000_000)},
    )
    servicio = _ServicioMF([{"numero": "P-001", "situacion": "Vigente", "denominacion": "X"}])

    datos, artefactos, errores = asyncio.run(
        plugin._consultar(servicio, entrada, runtime, "20123456789")
    )

    assert errores == []
    assert len(artefactos) == 3
    assert len(datos["planes"][0]["pdf_vistas"]) == 2


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


def test_excepcion_generica_de_un_plan_no_aborta_consulta_ni_filtra_mensaje(
    tmp_path: Path,
) -> None:
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin
    from bot_worker.bots.errors import Categoria

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
        fallos_genericos={"P-001"},
    )

    datos, _, errores = asyncio.run(
        plugin._consultar(servicio, entrada, _runtime_mf(tmp_path), "20123456789")
    )

    assert servicio.pdf_pedidos == ["P-001", "P-002"]
    assert [plan["numero"] for plan in datos["planes"]] == ["P-001", "P-002"]
    assert len(errores) == 1
    assert errores[0].category == Categoria.TARGET_UNAVAILABLE
    assert "target_unavailable_unclassified" in errores[0].internal_diagnostic
    assert "marcador_sensible_no_debe_publicarse" not in errores[0].internal_diagnostic


def test_error_de_seccion_del_portal_llega_al_resultado_parcial(tmp_path: Path) -> None:
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin
    from bot_worker.bots.errors import Categoria

    plugin = MisFacilidadesPlugin()
    _, entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
        "subir_archivos": False,
        "incluir_xlsx": False,
    }))
    servicio = _ServicioMF([
        {"numero": "P-001", "situacion": "Vigente", "denominacion": "A"},
    ])
    servicio.errores_de_plan = lambda numero: (
        [{"seccion": "Pagos", "codigo": "PlaywrightTimeoutError"}]
        if numero == "P-001"
        else []
    )

    datos, _, errores = asyncio.run(
        plugin._consultar(servicio, entrada, _runtime_mf(tmp_path), "20123456789")
    )

    assert datos["planes_procesados"] == 1
    assert len(errores) == 1
    assert errores[0].category == Categoria.TARGET_UNAVAILABLE
    assert errores[0].internal_diagnostic == (
        "plan P-001: sección Pagos: PlaywrightTimeoutError"
    )


def _runtime_con_deadline(tmp_path: Path, segundos: float):
    """Runtime como el de produccion, con un presupuesto de deadline acotado."""
    from bot_worker.runtime.context import DeadlineBudget

    import time as _time

    base = _runtime_mf(tmp_path)
    base.deadline = DeadlineBudget(deadline_epoch=_time.time() + segundos)
    return base


def test_consultar_corta_por_deadline_y_devuelve_el_parcial(tmp_path: Path) -> None:
    """Con cuentas grandes el job se corta: el parcial ya producido no se pierde."""
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin
    from bot_worker.bots.errors import Categoria

    plugin = MisFacilidadesPlugin()
    _, entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
        "incluir_pdf": True,
        "incluir_xlsx": True,
        "subir_archivos": False,
    }))
    planes = [
        {"numero": f"P-{i:03d}", "situacion": "Vigente", "denominacion": "A"}
        for i in range(1, 6)
    ]

    datos, _, errores = asyncio.run(
        plugin._consultar(
            _ServicioMF(planes), entrada, _runtime_con_deadline(tmp_path, 0), "20123456789"
        )
    )

    # Se procesa el plan en curso y el guardia corta antes de empezar el
    # siguiente: la corrida queda PARCIAL con lo ya producido en disco.
    assert datos["planes_procesados"] == 1
    assert len(datos["planes"][0]["pdf_vistas"]) == 2
    assert (tmp_path / datos["planes"][0]["xlsx"]["archivo"]).is_file()
    assert [e.category for e in errores] == [Categoria.DEADLINE_EXCEEDED]
    assert "deadline" in errores[0].internal_diagnostic


def test_consultar_procesa_todos_los_planes_con_presupuesto_holgado(tmp_path: Path) -> None:
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin

    plugin = MisFacilidadesPlugin()
    _, entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
        "incluir_pdf": True,
        "incluir_xlsx": True,
        "subir_archivos": False,
    }))
    planes = [
        {"numero": f"P-{i:03d}", "situacion": "Vigente", "denominacion": "A"}
        for i in range(1, 6)
    ]

    datos, _, errores = asyncio.run(
        plugin._consultar(
            _ServicioMF(planes), entrada, _runtime_con_deadline(tmp_path, 600), "20123456789"
        )
    )

    assert datos["planes_procesados"] == 5
    assert errores == []


def _consultar_con_rango(tmp_path: Path, fechas: dict[str, str | None], **rango: str):
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin

    plugin = MisFacilidadesPlugin()
    _, entrada = asyncio.run(plugin.validate({
        "operacion": "consultar",
        "representado_cuit": "20123456789",
        "subir_archivos": False,
        "incluir_xlsx": False,
        **rango,
    }))
    servicio = _ServicioMF([
        {"numero": numero, "situacion": "Vigente"} for numero in fechas
    ])
    servicio.fechas = fechas
    datos, _, errores = asyncio.run(
        plugin._consultar(servicio, entrada, _runtime_mf(tmp_path), "20123456789")
    )
    return datos, errores, servicio


def test_filtro_fecha_consolidacion_es_inclusivo_en_ambos_extremos(tmp_path: Path) -> None:
    datos, errores, servicio = _consultar_con_rango(
        tmp_path,
        {"A": "31/12/2025", "B": "01/01/2026", "C": "30/06/2026", "D": "01/07/2026"},
        fecha_consolidacion_desde="01/01/2026",
        fecha_consolidacion_hasta="30/06/2026",
    )

    assert errores == []
    assert servicio.pdf_pedidos == ["B", "C"]
    assert [p["fecha_consolidacion"] for p in datos["planes"]] == ["01/01/2026", "30/06/2026"]


def test_filtro_fecha_consolidacion_con_un_solo_limite(tmp_path: Path) -> None:
    fechas = {"A": "31/12/2025", "B": "01/01/2026"}
    _, _, solo_desde = _consultar_con_rango(tmp_path, fechas, fecha_consolidacion_desde="01/01/2026")
    _, _, solo_hasta = _consultar_con_rango(tmp_path, fechas, fecha_consolidacion_hasta="31/12/2025")

    assert solo_desde.pdf_pedidos == ["B"]
    assert solo_hasta.pdf_pedidos == ["A"]


def test_sin_rango_no_se_lee_la_fecha_de_consolidacion(tmp_path: Path) -> None:
    datos, _, servicio = _consultar_con_rango(tmp_path, {"A": "31/12/2025"})

    assert servicio.pdf_pedidos == ["A"]
    assert "fecha_consolidacion" not in datos["planes"][0]


def test_fecha_de_consolidacion_ilegible_omite_el_plan_y_reporta_error(tmp_path: Path) -> None:
    datos, errores, servicio = _consultar_con_rango(
        tmp_path, {"A": None, "B": "10/02/2026"}, fecha_consolidacion_desde="01/01/2026"
    )

    assert servicio.pdf_pedidos == ["B"]
    assert len(errores) == 1 and "plan A" in errores[0].internal_diagnostic


def test_validacion_de_fechas_de_consolidacion() -> None:
    from bot_worker.bots.errors import InvalidInputError
    from bot_worker.bots.mis_facilidades.plugin import MisFacilidadesPlugin

    base = {"operacion": "consultar", "representado_cuit": "20123456789"}
    for invalido in (
        {"fecha_consolidacion_desde": "2026-01-01"},
        {"fecha_consolidacion_hasta": "31/02/2026"},
        {"fecha_consolidacion_desde": "02/01/2026", "fecha_consolidacion_hasta": "01/01/2026"},
    ):
        try:
            asyncio.run(MisFacilidadesPlugin().validate({**base, **invalido}))
        except InvalidInputError:
            continue
        raise AssertionError(f"debió rechazar {invalido}")
    _, ok = asyncio.run(MisFacilidadesPlugin().validate(
        {**base, "fecha_consolidacion_desde": "01/01/2026", "fecha_consolidacion_hasta": "01/01/2026"}
    ))
    assert ok.fecha_consolidacion_desde == ok.fecha_consolidacion_hasta == "01/01/2026"


def test_portal_lee_la_fecha_de_consolidacion_del_detalle() -> None:
    class _Span(_Locator):
        async def text_content(self, **_: object) -> str:
            return " 05/06/2026 "

    class _Pagina:
        def locator(self, selector: str) -> _Locator:
            assert selector == "#ContentPlaceHolder1_CabeceraPlanesEnviados_cab_fechaConsolidacion"
            return _Span(1)

    portal = MisFacilidadesPortal(_Pagina())
    pasos: list[str] = []

    async def _abrir(numero: str) -> None:
        pasos.append(f"abrir {numero}")

    async def _volver(*, suprimir_error: bool = False) -> None:
        pasos.append("listado")

    portal._abrir_plan = _abrir  # type: ignore[method-assign]
    portal._volver_al_listado = _volver  # type: ignore[method-assign]

    assert asyncio.run(portal.fecha_consolidacion("P-1")) == "05/06/2026"
    assert pasos == ["abrir P-1", "listado"]
