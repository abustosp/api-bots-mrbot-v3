"""Historial de consultas de Mis Comprobantes (port V1 ``descarga_csv_consulta``).

La operacion ``historial`` no debe generar consultas nuevas: elige la consulta
existente del rango pedido y descarga su CSV. Estas pruebas usan una pagina
falsa que registra cada clic, para fijar la secuencia que hace V1.
"""

from __future__ import annotations

import asyncio
import io
import zipfile
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals.comprobantes import (
    ComprobantesPortal,
    clasificar_estado,
    consultas_desde_filas,
    indice_consulta,
)

FILAS = [
    ("Fecha: 01/12/2025 a 31/12/2025 Finalizada", 1),
    ("Fecha: 01/01/2026 a 31/01/2026 Finalizada", 2),
]
VISIBLES = {"Comprobantes Emitidos", "Historial", "Menú Principal"}


def _zip_con_csv() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as paquete:
        paquete.writestr("consulta.csv", "Fecha;Tipo\n2026-01-10;11\n")
    return buffer.getvalue()


class _Boton:
    def __init__(self, pagina: "_Pagina", fila: int, indice: int) -> None:
        self._pagina = pagina
        self._rotulo = f"boton:{fila}:{indice}"

    async def click(self, **_: Any) -> None:
        self._pagina.clicks.append(self._rotulo)


class _Botones:
    def __init__(self, pagina: "_Pagina", fila: int, cantidad: int) -> None:
        self._pagina, self._fila, self._cantidad = pagina, fila, cantidad

    async def count(self) -> int:
        return self._cantidad

    def nth(self, indice: int) -> _Boton:
        return _Boton(self._pagina, self._fila, indice)

    @property
    def first(self) -> _Boton:
        return _Boton(self._pagina, self._fila, 0)


class _Fila:
    def __init__(self, pagina: "_Pagina", indice: int, texto: str, botones: int) -> None:
        self._pagina, self._indice, self._texto, self._botones = pagina, indice, texto, botones

    async def text_content(self) -> str:
        return self._texto

    def locator(self, selector: str) -> _Botones:
        assert selector == "button"
        return _Botones(self._pagina, self._indice, self._botones)


class _Filas:
    def __init__(self, pagina: "_Pagina") -> None:
        self._pagina = pagina

    async def count(self) -> int:
        return len(self._pagina.filas)

    def nth(self, indice: int) -> _Fila:
        texto, botones = self._pagina.filas[indice]
        return _Fila(self._pagina, indice, texto, botones)


class _Vacio:
    async def count(self) -> int:
        return 0


class _Click:
    def __init__(self, pagina: "_Pagina", clave: str, nombre: str) -> None:
        self._pagina, self._clave, self._nombre = pagina, clave, nombre

    @property
    def first(self) -> "_Click":
        return self

    async def count(self) -> int:
        return 1

    async def is_visible(self, **_: Any) -> bool:
        return self._nombre in self._pagina.visibles

    async def scroll_into_view_if_needed(self, **_: Any) -> None:
        return None

    async def click(self, **_: Any) -> None:
        self._pagina.clicks.append(f"{self._clave}:{self._nombre}")


class _Descarga:
    def __init__(self, contenido: bytes) -> None:
        self._contenido = contenido

    async def save_as(self, path: str) -> None:
        Path(path).write_bytes(self._contenido)


class _EsperaDescarga:
    def __init__(self, pagina: "_Pagina") -> None:
        self._pagina = pagina

    async def __aenter__(self) -> "_EsperaDescarga":
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    @property
    def value(self) -> Any:
        async def _valor() -> _Descarga:
            return _Descarga(self._pagina.contenido_csv)

        return _valor()


class _Pagina:
    def __init__(self, filas: list[tuple[str, int]], visibles: set[str], contenido_csv: bytes = b"") -> None:
        self.filas = filas
        self.visibles = visibles
        self.contenido_csv = contenido_csv
        self.clicks: list[str] = []

    def locator(self, selector: str) -> Any:
        return _Filas(self) if selector == "table tbody tr" else _Vacio()

    def get_by_text(self, texto: str, exact: bool = False) -> _Click:
        return _Click(self, "texto", texto)

    def get_by_role(self, rol: str, name: str = "") -> _Click:
        return _Click(self, rol, name)

    def expect_download(self, timeout: int = 0) -> _EsperaDescarga:
        return _EsperaDescarga(self)

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


def test_clasificar_estado_sigue_el_orden_de_v1() -> None:
    assert clasificar_estado("Fecha ... Finalizada") == "Finalizada"
    assert clasificar_estado("En proceso") == "En proceso"
    assert clasificar_estado("Pendiente de firma") == "Pendiente"
    assert clasificar_estado("Error de sistema") == "Error"
    assert clasificar_estado("sin datos") == "Desconocido"


def test_consultas_desde_filas_extrae_rango_estado_y_exportable() -> None:
    consultas = consultas_desde_filas([("Fecha: 01/01/2026 a 31/01/2026 En proceso", 0)])

    assert consultas == [
        {
            "indice": 0,
            "estado": "En proceso",
            "exportable": False,
            "texto_completo": "Fecha: 01/01/2026 a 31/01/2026 En proceso",
            "fecha_desde": "01/01/2026",
            "fecha_hasta": "31/01/2026",
        }
    ]


def test_indice_consulta_compara_el_rango_completo_con_espacios_irregulares() -> None:
    textos = ["Fecha: 01/01/2026 a 15/01/2026", "Fecha:  01/01/2026  a  31/01/2026"]

    assert indice_consulta(textos, "01/01/2026", "31/01/2026") == 1
    assert indice_consulta(textos, "01/01/2026", "1/1/2026") is None


def test_historial_descarga_la_consulta_del_rango_con_el_segundo_boton(tmp_path: Path) -> None:
    pagina = _Pagina(FILAS, VISIBLES, _zip_con_csv())
    destino = tmp_path / "crudo.csv"

    resultado = asyncio.run(
        ComprobantesPortal(pagina).historial("emitidos", "01/01/2026", "31/01/2026", destino)
    )

    assert resultado["descargado"] is True
    assert [c["exportable"] for c in resultado["consultas"]] == [True, True]
    assert pagina.clicks == [
        "texto:Comprobantes Emitidos",
        "link:Historial",
        "boton:1:1",
        "link:CSV",
        "texto:Menú Principal",
    ]
    assert destino.read_bytes() == _zip_con_csv()


def test_historial_sin_consulta_del_rango_no_descarga_ni_falla(tmp_path: Path) -> None:
    pagina = _Pagina(FILAS, VISIBLES, _zip_con_csv())
    destino = tmp_path / "crudo.csv"

    resultado = asyncio.run(
        ComprobantesPortal(pagina).historial("emitidos", "01/02/2026", "28/02/2026", destino)
    )

    assert resultado["descargado"] is False
    assert len(resultado["consultas"]) == 2
    assert "link:CSV" not in pagina.clicks
    assert not destino.exists()


def test_historial_falla_si_no_aparece_la_seccion(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def sin_clic(self: Any, localizadores: Any, *, total_ms: int = 8_000) -> bool:
        return False

    monkeypatch.setattr(ComprobantesPortal, "_clickear", sin_clic)

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(
            ComprobantesPortal(_Pagina(FILAS, VISIBLES)).historial(
                "recibidos", "01/01/2026", "31/01/2026", tmp_path / "x.csv"
            )
        )
    assert error.value.diagnostic_code == "comprobantes_section_missing"


def test_historial_rechaza_tipo_desconocido_antes_de_navegar(tmp_path: Path) -> None:
    pagina = _Pagina(FILAS, VISIBLES)

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(
            ComprobantesPortal(pagina).historial("otro", "01/01/2026", "31/01/2026", tmp_path / "x.csv")
        )
    assert error.value.diagnostic_code == "comprobantes_type_unknown"
    assert pagina.clicks == []
