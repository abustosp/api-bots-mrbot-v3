from __future__ import annotations

import asyncio
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals.liquidacion_granos import (
    LiquidacionGranosPortal,
    _guardar_xlsx,
    _normalizar_denominacion,
    _similitud_denominacion,
)


def test_planilla_xlsx_es_valida_y_preserva_valores(tmp_path: Path) -> None:
    destino = tmp_path / "planilla.xlsx"

    resultado = _guardar_xlsx(
        destino,
        ["Fecha", "Producto"],
        [["27/09/2026", "Maíz & trigo"]],
    )

    assert resultado == destino
    assert destino.stat().st_size > 0
    with zipfile.ZipFile(destino) as libro:
        assert libro.testzip() is None
        xml = libro.read("xl/worksheets/sheet1.xml")
    raiz = ElementTree.fromstring(xml)
    textos = [nodo.text for nodo in raiz.iter() if nodo.tag.endswith("}t")]
    assert textos == ["Fecha", "Producto", "27/09/2026", "Maíz & trigo"]


def test_planilla_vacia_genera_un_xlsx_informativo(tmp_path: Path) -> None:
    destino = _guardar_xlsx(tmp_path / "vacio.xlsx", ["Resultado"], [])

    with zipfile.ZipFile(destino) as libro:
        assert libro.testzip() is None
        contenido = libro.read("xl/worksheets/sheet1.xml")
    assert b"Sin registros" in contenido


def test_extrae_encabezados_y_filas_de_la_tabla_mas_completa() -> None:
    encabezados, filas = LiquidacionGranosPortal._filas_de_tablas(
        [
            {"headers": ["Menú"], "rows": []},
            {"headers": ["Fecha", "COE"], "rows": [["27/09/2026", "123"]]},
        ]
    )

    assert encabezados == ["Fecha", "COE"]
    assert filas == [["27/09/2026", "123"]]


def test_pdf_solo_se_solicita_en_el_mismo_origen_https() -> None:
    misma_origen = LiquidacionGranosPortal._misma_origen

    assert misma_origen("https://servicios.afip.gob.ar/lpg/", "https://servicios.afip.gob.ar/lpg/a.pdf")
    assert not misma_origen("https://servicios.afip.gob.ar/lpg/", "https://otro.example/a.pdf")
    assert not misma_origen("https://servicios.afip.gob.ar/lpg/", "http://servicios.afip.gob.ar/a.pdf")


# --------------------------------------------------------------------------
# Selección del representado (pantalla "Seleccione la Empresa" del portal LPG)

_DENOMINACION = "SUCESION DE SZYCHOWSKI RICARDO BRONISLADO"


class _Elemento:
    """Botón falso: cuenta clics y puede fallar al clickear (fallback JS)."""

    def __init__(
        self, texto: str, *, falla_click: bool = False, al_click: Any = None
    ) -> None:
        self.texto = texto
        self.clicks = 0
        self.falla_click = falla_click
        self.al_click = al_click

    @property
    def first(self) -> "_Elemento":
        return self

    async def count(self) -> int:
        return 1

    async def is_visible(self, **_: Any) -> bool:
        return True

    async def inner_text(self, **_: Any) -> str:
        return self.texto

    async def scroll_into_view_if_needed(self, **_: Any) -> None:
        return None

    async def click(self, **_: Any) -> None:
        if self.falla_click:
            raise RuntimeError("no clickeable")
        self.clicks += 1
        if self.al_click is not None:
            self.al_click()


class _Grupo:
    def __init__(self, elementos: list[Any] | None = None) -> None:
        self.elementos = elementos or []

    async def count(self) -> int:
        return len(self.elementos)

    @property
    def first(self) -> Any:
        return self.elementos[0] if self.elementos else None

    def nth(self, indice: int) -> Any:
        return self.elementos[indice]

    def filter(self, **_: Any) -> "_Grupo":
        return self


class _PaginaFalsa:
    """Página mínima del portal LPG: menú, botones de empresa y evaluate()."""

    def __init__(
        self,
        denominaciones: list[str] | None = None,
        *,
        dentro_lpg: bool = False,
        falla_click: bool = False,
        cuits_encabezado: list[str] | None = None,
    ) -> None:
        self.denominaciones = list(denominaciones or [])
        self.dentro_lpg = dentro_lpg
        self.falla_click = falla_click
        self.cuits_encabezado = list(cuits_encabezado or [])
        self.clicks_js: list[int] = []
        self.url = "https://serviciosjava2.afip.gob.ar/lpg/jsp/index.jsp"
        # Los botones de empresa se crean una sola vez: clickearlos (por rol o
        # por JS) navega a la pantalla con el menú del servicio.
        self.botones_empresa = [
            _Elemento(texto, falla_click=falla_click, al_click=self._entrar)
            for texto in self.denominaciones
        ]

    def _entrar(self) -> None:
        self.dentro_lpg = True

    # --- DOM simplificado -------------------------------------------------
    def _botones_menu(self) -> list[Any]:
        if not self.dentro_lpg:
            return []
        return [_Elemento("Menú principal"), _Elemento("Liquidación Primaria de Granos")]

    def get_by_role(self, role: str, *, name: Any = None, **_: Any) -> _Grupo:
        if role != "button":
            return _Grupo()
        candidatos = self._botones_menu() + self.botones_empresa
        if isinstance(name, re.Pattern):
            return _Grupo([elemento for elemento in candidatos if name.search(elemento.texto)])
        if name is None:
            return _Grupo(candidatos)
        return _Grupo([elemento for elemento in candidatos if str(name) in elemento.texto])

    def locator(self, _: str) -> _Grupo:
        return _Grupo()

    async def evaluate(self, guion: str, indice: Any = None) -> Any:
        if "index: i" in guion:
            return [
                {"index": posicion, "text": texto, "visible": True}
                for posicion, texto in enumerate(self.denominaciones)
            ]
        if "bienvenido" in guion:
            return list(self.cuits_encabezado)
        assert "boton.click()" in guion, "JS inesperado"
        if not isinstance(indice, int) or not 0 <= indice < len(self.denominaciones):
            return False
        self.clicks_js.append(indice)
        self._entrar()
        return True

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_timeout(self, *_: Any) -> None:
        return None


def test_normaliza_y_puntua_denominaciones_de_empresa() -> None:
    assert _normalizar_denominacion("  Szychowski, Ricardo  ") == "SZYCHOWSKI RICARDO"
    assert _similitud_denominacion(_DENOMINACION, "sucesión de szychowski ricardo bronislado") == 1.0
    assert _similitud_denominacion(_DENOMINACION, _DENOMINACION + " 20-20074827-5") == 1.0
    assert _similitud_denominacion(_DENOMINACION, "GIMENEZ RICARDO RENE") < 0.9


def test_si_ya_esta_en_lpg_no_intenta_seleccionar() -> None:
    pagina = _PaginaFalsa([], dentro_lpg=True)
    portal = LiquidacionGranosPortal(pagina)

    asyncio.run(portal.seleccionar_representado("20074827455", _DENOMINACION))

    assert pagina.clicks_js == []


def test_elige_el_boton_de_la_denominacion() -> None:
    pagina = _PaginaFalsa([_DENOMINACION, "OTRA EMPRESA SA"])
    portal = LiquidacionGranosPortal(pagina)

    asyncio.run(portal.seleccionar_representado("20074827455", _DENOMINACION))

    assert pagina.botones_empresa[0].clicks == 1
    assert pagina.clicks_js == []
    assert pagina.dentro_lpg is True
    assert portal._representado_cuit == "20074827455"
    assert portal._representado_nombre == _DENOMINACION


def test_clickea_por_indice_cuando_el_boton_del_rol_no_es_clickeable() -> None:
    pagina = _PaginaFalsa([_DENOMINACION], falla_click=True)
    portal = LiquidacionGranosPortal(pagina)

    asyncio.run(portal.seleccionar_representado("20074827455", _DENOMINACION))

    assert pagina.clicks_js == [0]
    assert pagina.dentro_lpg is True


def test_control_negativo_denominacion_ausente_falla_limpio() -> None:
    """Si ninguna empresa coincide, falla tipado y sin clickear ni descargar."""
    pagina = _PaginaFalsa(["EMPRESA AJENA SA", "OTRA MAS SA"])
    portal = LiquidacionGranosPortal(pagina)

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(portal.seleccionar_representado("20074827455", _DENOMINACION))

    assert exc.value.diagnostic_code == "granos_company_not_found"
    assert pagina.clicks_js == []


def test_control_negativo_empresa_similar_pero_distinta_falla_limpio() -> None:
    """Un parecido por debajo del umbral no debe elegir a otra empresa."""
    pagina = _PaginaFalsa(["SUCESION DE SZYCHOWSKI RICARDO JOSE MARIA"])
    portal = LiquidacionGranosPortal(pagina)

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(portal.seleccionar_representado("20074827455", _DENOMINACION))

    assert exc.value.diagnostic_code == "granos_company_not_found"
    assert pagina.clicks_js == [] and pagina.dentro_lpg is False


def test_sin_denominacion_cae_al_camino_por_cuit_y_falla_mapeado() -> None:
    pagina = _PaginaFalsa([_DENOMINACION])
    portal = LiquidacionGranosPortal(pagina)

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(portal.seleccionar_representado("20074827455"))

    assert exc.value.diagnostic_code == "represented_cuit_not_selectable"
    assert pagina.clicks_js == []


def test_guarda_de_cuit_acepta_el_representado_pedido() -> None:
    pagina = _PaginaFalsa([_DENOMINACION], cuits_encabezado=["20074827455"])
    portal = LiquidacionGranosPortal(pagina)

    asyncio.run(portal.seleccionar_representado("20074827455", _DENOMINACION))

    assert pagina.dentro_lpg is True


def test_control_negativo_cuit_distinto_detiene_la_seleccion() -> None:
    """Si el portal queda en otro CUIT, no se sigue: nada de otro contribuyente."""
    pagina = _PaginaFalsa([_DENOMINACION], cuits_encabezado=["20111111112"])
    portal = LiquidacionGranosPortal(pagina)

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(portal.seleccionar_representado("20074827455", _DENOMINACION))

    assert exc.value.diagnostic_code == "granos_representado_incorrecto"


def test_guarda_de_cuit_sin_cuits_visibles_continua() -> None:
    pagina = _PaginaFalsa([_DENOMINACION], dentro_lpg=True, cuits_encabezado=[])
    portal = LiquidacionGranosPortal(pagina)

    asyncio.run(portal.seleccionar_representado("20074827455", _DENOMINACION))

    assert pagina.clicks_js == []


# --------------------------------------------------------------------------
# Planilla: la grilla real es la de las filas con link de descarga (port de V2)

_TABLA_MAQUETADO = {
    "title": "BodyTable",
    "encabezados": [["Fecha Desde", "Fecha Hasta", "Coe", "Cuit emisor", "Liquidaciones"]],
    "rows": [
        ["Encabezado del portal con el menú", "", "", "", ""],
        ["Bienvenido", "CUIT:", "20074827455", "Dependencia", "SEDE"],
        ["Menú principal", "", "LPG", "", ""],
    ],
    "descargas": [False, False, False],
}
_TABLA_GRILLA = {
    "title": "Liquidaciones",
    "encabezados": [
        ["Liquidaciones"],
        [
            "Fecha",
            "Cuit Vendedor",
            "Coe",
            "Sistema",
            "Tipo operación",
            "Estado",
            "Ver Comprobante",
            "Accion",
        ],
    ],
    "rows": [
        ["15/02/2025", "20111111112", "123456789", "LPG", "Primaria", "Activo", "Ver", "Detalle"],
        ["20/02/2025", "20222222223", "987654321", "LPG", "Primaria", "Activo", "Ver", "Detalle"],
    ],
    "descargas": [True, True],
}


def test_planilla_toma_la_grilla_con_descargas_y_no_el_maquetado() -> None:
    encabezados, filas = LiquidacionGranosPortal._filas_de_tablas(
        [_TABLA_MAQUETADO, _TABLA_GRILLA]
    )

    assert encabezados == [
        "Fecha",
        "Cuit Vendedor",
        "Coe",
        "Sistema",
        "Tipo operación",
        "Estado",
        "Ver Comprobante",
        "Accion",
    ]
    assert filas == _TABLA_GRILLA["rows"]


def test_planilla_ignora_filas_sin_descarga_dentro_de_la_grilla() -> None:
    grilla = dict(_TABLA_GRILLA)
    grilla["rows"] = [*_TABLA_GRILLA["rows"], ["Total", "", "", "", "", "", "", ""]]
    grilla["descargas"] = [True, True, False]

    _encabezados, filas = LiquidacionGranosPortal._filas_de_tablas([grilla])

    assert filas == _TABLA_GRILLA["rows"]


def test_planilla_sin_filas_de_datos_es_informativa() -> None:
    encabezados, filas = LiquidacionGranosPortal._filas_de_tablas([_TABLA_MAQUETADO])

    assert encabezados == ["Resultado"]
    assert filas == [["Sin registros"]]
