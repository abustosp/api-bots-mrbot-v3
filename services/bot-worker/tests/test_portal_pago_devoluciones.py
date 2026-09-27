from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.pago_devoluciones import PagoDevolucionesPortal


class _Localizador:
    def __init__(self, etiqueta: str, visible: bool, eventos: list[str]) -> None:
        self.etiqueta = etiqueta
        self.visible = visible
        self.eventos = eventos

    @property
    def first(self) -> "_Localizador":
        return self

    async def count(self) -> int:
        return 1 if self.visible else 0

    async def is_visible(self, **_: Any) -> bool:
        return self.visible

    async def wait_for(self, **_: Any) -> None:
        if not self.visible:
            raise TimeoutError

    async def click(self, **_: Any) -> None:
        if not self.visible:
            raise TimeoutError
        self.eventos.append(self.etiqueta)


class _Descarga:
    suggested_filename = "pagos.xlsx"

    async def save_as(self, destino: str) -> None:
        Path(destino).write_bytes(b"PK\x03\x04contenido-xlsx-de-prueba")


class _EventoDescarga:
    async def __aenter__(self) -> "_EventoDescarga":
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    @property
    def value(self):  # type: ignore[no-untyped-def]
        async def _obtener() -> _Descarga:
            return _Descarga()

        return _obtener()


class _ComboRepresentado:
    def __init__(self) -> None:
        self.value = "20999999999"

    @property
    def first(self) -> "_ComboRepresentado":
        return self

    async def count(self) -> int:
        return 1

    async def wait_for(self, **_: Any) -> None:
        return None

    async def evaluate(self, *_: Any) -> list[str]:
        return ["20999999999", "20123456789"]

    async def input_value(self) -> str:
        return self.value

    async def select_option(self, *, value: str) -> None:
        self.value = value


class _Pagina:
    def __init__(self, *, exportar: bool = True, aceptar: bool = True) -> None:
        self.eventos: list[str] = []
        self.hay_exportar = exportar
        self.hay_aceptar = aceptar

    def get_by_role(self, rol: str, *, name: Any) -> _Localizador:
        if isinstance(name, re.Pattern):
            etiqueta = "Consultar" if name.search("Consultar") else ""
            etiqueta = etiqueta or ("Exportar" if name.search("Exportar") else "")
            etiqueta = etiqueta or ("Aceptar" if name.search("Aceptar") else "")
        else:
            etiqueta = str(name)
        visible = etiqueta == "Consultar"
        if etiqueta == "Exportar":
            visible = self.hay_exportar
        if etiqueta == "Aceptar":
            visible = self.hay_aceptar
        return _Localizador(etiqueta or rol, visible, self.eventos)

    def locator(self, selector: str) -> _Localizador:
        if "Consultar" in selector:
            return _Localizador("Consultar", True, self.eventos)
        if "Exportar" in selector:
            return _Localizador("Exportar", self.hay_exportar, self.eventos)
        if "Aceptar" in selector:
            return _Localizador("Aceptar", self.hay_aceptar, self.eventos)
        return _Localizador(selector, False, self.eventos)

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    def expect_download(self, **_: Any) -> _EventoDescarga:
        return _EventoDescarga()


class _PaginaConCombo(_Pagina):
    def __init__(self) -> None:
        super().__init__()
        self.combo = _ComboRepresentado()

    def locator(self, selector: str) -> Any:
        if "ctl00_ddlExtranetEmpresa" in selector:
            return self.combo
        return super().locator(selector)


def test_portal_descubierto_por_nombre() -> None:
    assert portal_para("pago_devoluciones") is PagoDevolucionesPortal


def test_consultar_exporta_archivo_y_acepta_confirmacion(tmp_path: Path) -> None:
    pagina = _Pagina()
    portal = PagoDevolucionesPortal(pagina)
    destino = tmp_path / "pagos.xlsx"

    resultado = asyncio.run(portal.consultar_y_exportar("20-12345678-9", destino))

    assert resultado == destino
    assert destino.read_bytes().startswith(b"PK\x03\x04")
    assert pagina.eventos == ["Consultar", "Exportar", "Aceptar"]


def test_consultar_exporta_sin_dialogo_de_confirmacion(tmp_path: Path) -> None:
    pagina = _Pagina(aceptar=False)
    portal = PagoDevolucionesPortal(pagina)
    destino = tmp_path / "pagos.xlsx"

    asyncio.run(portal.consultar_y_exportar("20123456789", destino))

    assert pagina.eventos == ["Consultar", "Exportar"]
    assert destino.stat().st_size > 0


def test_exportacion_falla_si_no_aparece_el_control_exportar(tmp_path: Path) -> None:
    portal = PagoDevolucionesPortal(_Pagina(exportar=False))

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.consultar_y_exportar("20123456789", tmp_path / "p.xlsx"))

    assert error.value.diagnostic_code == "pago_devoluciones_export_missing"


def test_rechaza_cuit_representado_invalido(tmp_path: Path) -> None:
    portal = PagoDevolucionesPortal(_Pagina())

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.consultar_y_exportar("123", tmp_path / "p.xlsx"))

    assert error.value.diagnostic_code == "pago_devoluciones_cuit_invalid"


def test_selecciona_representado_con_combo_legacy() -> None:
    pagina = _PaginaConCombo()
    portal = PagoDevolucionesPortal(pagina)

    asyncio.run(portal.seleccionar_representado("20-12345678-9"))

    assert pagina.combo.value == "20123456789"
