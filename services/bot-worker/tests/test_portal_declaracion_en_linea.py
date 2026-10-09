"""Pruebas del port DDJJ en línea sin credenciales ni navegador real."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals import declaracion_en_linea as portal_mod
from bot_worker.runtime.portals.declaracion_en_linea import DeclaracionEnLineaPortal


class _Locator:
    def __init__(self, onclick: str = "") -> None:
        self.onclick = onclick

    @property
    def first(self) -> _Locator:
        return self

    async def count(self) -> int:
        return int(bool(self.onclick))

    async def get_attribute(self, nombre: str) -> str:
        return self.onclick if nombre == "onclick" else ""


class _Fila:
    def locator(self, selector: str) -> _Locator:
        if "dvVerF931" in selector:
            return _Locator("AbrirPopupF931('202503','17')")
        if "dvVerVep" in selector:
            return _Locator("AbrirPopupVep('202503','21')")
        return _Locator()


class _Pagina:
    url = "https://serviciossegsoc.afip.gob.ar/djproforma/app/consultar/dj_generadas.aspx"


def test_portal_ddjj_se_registra_y_es_de_solo_lectura() -> None:
    assert portal_para("declaracion_en_linea") is DeclaracionEnLineaPortal
    assert "declaracion_en_linea" in PORTALES
    assert hasattr(DeclaracionEnLineaPortal, "consultar_periodo")
    assert not hasattr(DeclaracionEnLineaPortal, "presentar")
    assert not hasattr(DeclaracionEnLineaPortal, "generar")


def test_normaliza_periodo_y_rechaza_mes_invalido() -> None:
    assert DeclaracionEnLineaPortal._normalizar_periodo("202503") == "202503"
    with pytest.raises(TargetUnavailableError) as error:
        DeclaracionEnLineaPortal._normalizar_periodo("202513")
    assert error.value.diagnostic_code == "declaracion_en_linea_period_invalid"


def test_resuelve_representado_solo_por_digitos() -> None:
    opciones = [
        {"value": "", "label": "Seleccione"},
        {"value": "00000000000", "label": "00000000000 - Empresa ficticia"},
    ]
    assert DeclaracionEnLineaPortal._resolver_opcion_representado(opciones, "00000000000") == "00000000000"
    assert DeclaracionEnLineaPortal._resolver_opcion_representado(opciones, "00999999999") is None


def test_construye_urls_desde_los_handlers_de_la_fila() -> None:
    portal = DeclaracionEnLineaPortal(_Pagina())
    ddjj, vep = asyncio.run(portal._urls_periodo({"row": _Fila()}, "202503"))
    assert ddjj.endswith("ver_formulario.aspx?Periodo=202503&SecDJVig=17")
    assert vep.endswith("ver_Vep.aspx?Periodo=202503&SecDJVig=21")


def test_consulta_periodo_guarda_ddjj_y_admite_vep_ausente(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    portal = DeclaracionEnLineaPortal(_Pagina())
    llamada: dict[str, Any] = {}

    async def preparar() -> None:
        return None

    async def filas() -> list[dict[str, Any]]:
        return [{"periodo": "202503", "row": object()}]

    async def urls(_fila: dict[str, Any], periodo: str) -> tuple[str, str]:
        llamada["periodo"] = periodo
        return "https://ejemplo.test/ddjj", "https://ejemplo.test/vep"

    async def guardar_ddjj(_url: str, destino: Path) -> Path:
        destino.write_bytes(b"%PDF-1.7 ficticio")
        return destino

    async def guardar_vep(_url: str, _periodo: str, _destino: Path) -> tuple[None, dict[str, Any]]:
        return None, {"estado_vep": "", "pagado": False}

    monkeypatch.setattr(portal, "preparar", preparar)
    monkeypatch.setattr(portal, "_filas_ddjj", filas)
    monkeypatch.setattr(portal, "_urls_periodo", urls)
    monkeypatch.setattr(portal, "_guardar_ddjj", guardar_ddjj)
    monkeypatch.setattr(portal, "_guardar_vep", guardar_vep)

    destino = tmp_path / "ddjj.pdf"
    resultado = asyncio.run(portal.consultar_periodo("202503", destino, tmp_path / "vep.pdf"))

    assert llamada["periodo"] == "202503"
    assert destino.read_bytes().startswith(b"%PDF-")
    assert resultado == {"estado_vep": "", "pagado": False}


# ---------------------------------------------------------------------------
# VEP: busqueda de la fila (port de ``find_vep_row`` de V1)


class _FilaTexto:
    def __init__(self, texto: str) -> None:
        self._texto = texto

    async def inner_text(self) -> str:
        return self._texto


class _GrupoFilas:
    def __init__(self, filas: list[_FilaTexto]) -> None:
        self._filas = filas

    async def count(self) -> int:
        return len(self._filas)

    @property
    def first(self) -> _FilaTexto:
        return self._filas[0]

    def nth(self, indice: int) -> _FilaTexto:
        return self._filas[indice]


class _FilasVep:
    def __init__(self, textos: list[str]) -> None:
        self._textos = textos

    def filter(self, has_text: Any) -> _GrupoFilas:
        return _GrupoFilas([_FilaTexto(t) for t in self._textos if has_text.search(t)])

    async def count(self) -> int:
        return len(self._textos)

    @property
    def first(self) -> _FilaTexto:
        return _FilaTexto(self._textos[0])


class _PaginaVep:
    def __init__(self, textos: list[str]) -> None:
        self._textos = textos

    def locator(self, _selector: str) -> _FilasVep:
        return _FilasVep(self._textos)


def _texto_fila(fila: Any) -> str:
    return asyncio.run(fila.inner_text())


def test_fila_vep_prioriza_el_numero_informado() -> None:
    pagina = _PaginaVep(["SIJPDJ03/25 vencido 111", "SIJPDJ03/25 pagado 222"])

    fila = asyncio.run(DeclaracionEnLineaPortal(_Pagina())._fila_vep(pagina, "202503", "222"))

    assert _texto_fila(fila) == "SIJPDJ03/25 pagado 222"


def test_fila_vep_prioriza_la_pagada_entre_las_del_periodo() -> None:
    pagina = _PaginaVep(["SIJPDJ03/25 vencido", "SIJPDJ03/25 pagado"])

    fila = asyncio.run(DeclaracionEnLineaPortal(_Pagina())._fila_vep(pagina, "202503"))

    assert _texto_fila(fila) == "SIJPDJ03/25 pagado"


def test_fila_vep_acepta_las_variantes_del_periodo() -> None:
    portal = DeclaracionEnLineaPortal(_Pagina())

    assert _texto_fila(asyncio.run(portal._fila_vep(_PaginaVep(["2025-03 algo"]), "202503"))) == "2025-03 algo"
    assert _texto_fila(asyncio.run(portal._fila_vep(_PaginaVep(["03.2025 algo"]), "202503"))) == "03.2025 algo"


def test_fila_vep_usa_la_unica_fila_o_devuelve_none() -> None:
    portal = DeclaracionEnLineaPortal(_Pagina())

    unica = _PaginaVep(["otra cosa"])
    assert _texto_fila(asyncio.run(portal._fila_vep(unica, "202503"))) == "otra cosa"
    assert asyncio.run(portal._fila_vep(_PaginaVep(["a", "b"]), "202503")) is None


class _PaginaVepSinFilas:
    """El listado SPA recien aparece despues del primer sondeo (V1)."""

    def __init__(self) -> None:
        self.sondeos = 0

    def locator(self, _selector: str) -> Any:
        pagina = self

        class _Filas:
            async def count(self) -> int:
                pagina.sondeos += 1
                return 0 if pagina.sondeos == 1 else 1

        return _Filas()

    async def wait_for_timeout(self, _ms: int) -> None:
        return None


def test_espera_filas_vep_hasta_que_rendericen() -> None:
    portal = DeclaracionEnLineaPortal(_Pagina())
    pagina = _PaginaVepSinFilas()

    assert asyncio.run(portal._esperar_filas_vep(pagina, timeout_ms=2_000)) == 1
    assert pagina.sondeos == 2


# ---------------------------------------------------------------------------
# VEP: guardado con detalle + listado SETI


HTML_DETALLE_VEP = """
<table>
  <tr><td>Estado</td><td>Pagado</td></tr>
  <tr><td>Nro. VEP</td><td>12345678901</td></tr>
</table>
"""


class _PaginaCuerpo:
    def __init__(self, texto: str = "SIJPDJ03/25 pagado") -> None:
        self._texto = texto
        self.cerrada = False

    def locator(self, _selector: str) -> Any:
        texto = self._texto

        class _Cuerpo:
            async def inner_text(self, **_: Any) -> str:
                return texto

        return _Cuerpo()

    async def close(self) -> None:
        self.cerrada = True


def test_guardar_vep_combina_detalle_y_listado_y_devuelve_los_datos_de_v1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    portal = DeclaracionEnLineaPortal(_Pagina())
    pagina = _PaginaCuerpo()
    llamadas: dict[str, Any] = {}

    async def contenido(_pagina: Any, timeout_s: float = 30.0) -> str:
        return HTML_DETALLE_VEP

    async def nueva() -> _PaginaCuerpo:
        return pagina

    async def abrir(_pagina: Any, _url: str, intentos: int = 3) -> None:
        return None

    async def estado(_pagina: Any) -> dict[str, Any]:
        return {"estado_vep": "vencido", "pagado": False}

    async def mapa() -> dict[str, dict[str, str]]:
        return {"202503": {"nro_vep": "12345678901", "cuit": "20123456789"}}

    async def descargar(_pagina: Any, _periodo: str, destino: Path, nro_vep: str | None = None) -> Path:
        llamadas["nro_vep"] = nro_vep
        destino.write_bytes(b"%PDF-1.7 vep")
        return destino

    async def fila(_pagina: Any, _periodo: str, _nro: str | None = None) -> None:
        return None

    monkeypatch.setattr(portal_mod, "contenido_con_timeout", contenido)
    monkeypatch.setattr(portal, "_pagina_nueva", nueva)
    monkeypatch.setattr(portal, "_abrir_url", abrir)
    monkeypatch.setattr(portal, "_estado_vep", estado)
    monkeypatch.setattr(portal, "_mapa_vep_seti", mapa)
    monkeypatch.setattr(portal, "_descargar_vep", descargar)
    monkeypatch.setattr(portal, "_fila_vep", fila)

    destino = tmp_path / "vep.pdf"
    archivo, info = asyncio.run(portal._guardar_vep("https://ejemplo.test/vep", "202503", destino))

    assert archivo == destino and destino.read_bytes().startswith(b"%PDF-")
    # El numero del detalle viaja a la busqueda de la fila (V1 ``find_vep_row``).
    assert llamadas["nro_vep"] == "12345678901"
    assert info["estado_vep"] == "Pagado"
    assert info["pagado"] is True
    assert info["vep"] == {
        "estado": "Pagado",
        "nro_vep": "12345678901",
        "cuit": "20123456789",
    }
    assert pagina.cerrada is True


def test_guardar_vep_sin_datos_usa_el_texto_de_la_pagina(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    portal = DeclaracionEnLineaPortal(_Pagina())

    async def contenido(_pagina: Any, timeout_s: float = 30.0) -> str:
        return ""

    async def nueva() -> _PaginaCuerpo:
        return _PaginaCuerpo()

    async def abrir(_pagina: Any, _url: str, intentos: int = 3) -> None:
        return None

    async def mapa() -> dict[str, dict[str, str]]:
        return {}

    async def descargar(_pagina: Any, _periodo: str, destino: Path, nro_vep: str | None = None) -> None:
        return None

    async def fila(_pagina: Any, _periodo: str, _nro: str | None = None) -> None:
        return None

    monkeypatch.setattr(portal_mod, "contenido_con_timeout", contenido)
    monkeypatch.setattr(portal, "_pagina_nueva", nueva)
    monkeypatch.setattr(portal, "_abrir_url", abrir)
    monkeypatch.setattr(portal, "_mapa_vep_seti", mapa)
    monkeypatch.setattr(portal, "_descargar_vep", descargar)
    monkeypatch.setattr(portal, "_fila_vep", fila)

    archivo, info = asyncio.run(
        portal._guardar_vep("https://ejemplo.test/vep", "202503", tmp_path / "vep.pdf")
    )

    assert archivo is None
    assert info == {"estado_vep": "pagado", "pagado": True, "vep": {}}


# ---------------------------------------------------------------------------
# DDJJ: una sola pestana para el HTML y el PDF


class _PaginaDdjj:
    def __init__(self, *, imprime: bool = True) -> None:
        self._imprime = imprime
        self.pdf_llamadas: list[str] = []

    def locator(self, _selector: str) -> Any:
        class _Control:
            async def count(self) -> int:
                return 0

        return _Control()

    async def emulate_media(self, **_: Any) -> None:
        return None

    async def pdf(self, *, path: str, format: str, print_background: bool) -> None:
        if not self._imprime:
            raise RuntimeError("renderer bloqueado")
        Path(path).write_bytes(b"%PDF-1.7 ddjj")
        self.pdf_llamadas.append(path)

    async def close(self) -> None:
        return None


class _ContextoDdjj:
    def __init__(self, paginas: list[_PaginaDdjj]) -> None:
        self._paginas = paginas

    async def new_page(self) -> _PaginaDdjj:
        return self._paginas.pop(0)


def _portal_con_paginas(paginas: list[_PaginaDdjj]) -> DeclaracionEnLineaPortal:
    class _PaginaBase:
        url = "https://ejemplo.test/listado"

    portal = DeclaracionEnLineaPortal(_PaginaBase())
    portal._context = _ContextoDdjj(paginas)  # type: ignore[attr-defined]
    return portal


def test_leer_ddjj_devuelve_html_y_pdf_en_una_sola_pestana(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    pagina = _PaginaDdjj()
    portal = _portal_con_paginas([pagina])

    async def abrir(_pagina: Any, _url: str, intentos: int = 3) -> None:
        return None

    async def contenido(_pagina: Any, timeout_s: float = 30.0) -> str:
        return "<html>ddjj</html>"

    monkeypatch.setattr(portal, "_abrir_url", abrir)
    monkeypatch.setattr(portal_mod, "contenido_con_timeout", contenido)

    destino = tmp_path / "ddjj.pdf"
    pdf, html = asyncio.run(portal._leer_ddjj("https://ejemplo.test/ddjj", destino))

    assert html == "<html>ddjj</html>"
    assert pdf == destino and destino.read_bytes().startswith(b"%PDF-")
    assert pagina.pdf_llamadas == [str(destino)]


def test_leer_ddjj_reintenta_en_pestana_nueva_si_la_impresion_falla(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    primera = _PaginaDdjj(imprime=False)
    segunda = _PaginaDdjj()
    portal = _portal_con_paginas([primera, segunda])

    async def abrir(_pagina: Any, _url: str, intentos: int = 3) -> None:
        return None

    async def contenido(_pagina: Any, timeout_s: float = 30.0) -> str:
        return "<html>ddjj</html>"

    monkeypatch.setattr(portal, "_abrir_url", abrir)
    monkeypatch.setattr(portal_mod, "contenido_con_timeout", contenido)

    destino = tmp_path / "ddjj.pdf"
    pdf, html = asyncio.run(portal._leer_ddjj("https://ejemplo.test/ddjj", destino))

    assert html == "<html>ddjj</html>"
    assert pdf == destino
    assert segunda.pdf_llamadas == [str(destino)]


def test_leer_ddjj_sin_pdf_no_corta_la_consulta(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    portal = _portal_con_paginas([_PaginaDdjj(imprime=False), _PaginaDdjj(imprime=False)])

    async def abrir(_pagina: Any, _url: str, intentos: int = 3) -> None:
        return None

    async def contenido(_pagina: Any, timeout_s: float = 30.0) -> str:
        return "<html>ddjj</html>"

    monkeypatch.setattr(portal, "_abrir_url", abrir)
    monkeypatch.setattr(portal_mod, "contenido_con_timeout", contenido)

    pdf, html = asyncio.run(portal._leer_ddjj("https://ejemplo.test/ddjj", tmp_path / "ddjj.pdf"))

    assert pdf is None
    assert html == "<html>ddjj</html>"
    assert not (tmp_path / "ddjj.pdf").exists()
