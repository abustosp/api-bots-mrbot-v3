"""Helpers de Declaracion en linea: parseo de DDJJ, VEP, JSON y tiempos (sin navegador)."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.declaracion_en_linea.helpers import (
    PDF_RENDER_TIMEOUT_S,
    VEP_DOWNLOAD_TIMEOUT_S,
    VEP_PROBE_TIMEOUT_S,
    combinar_vep,
    construir_json_ddjj,
    con_timeout,
    contenido_con_timeout,
    escribir_json_ddjj,
    extraer_cabecera_ddjj,
    extraer_campos_ddjj,
    extraer_estados_vep,
    extraer_info_vep,
    extraer_pares_de_texto,
    extraer_secciones_ddjj,
    id_a_etiqueta,
    mapa_estados_vep,
    parsear_tablas,
    pdf_con_timeout,
    periodo_a_yyyymm,
    periodo_desde_texto,
    periodo_to_sijpdj,
    variantes_periodo,
)

HTML_RESUMEN = """
<html><body>
<span id="ucF931_lblCUIT">20123456789</span>
<span id="ucF931_lblPeriodo">03/2025</span>
<span id="ucF931_lblRazonSocial">EMPRESA FICTICIA SA</span>
<table>
  <tr>
    <th class="F931CeldaResumenHeader" colspan="3">I - Remuneraciones</th>
    <th class="F931CeldaResumenHeader" colspan="3">II - Contribuciones</th>
  </tr>
  <tr>
    <td>110</td><td>Sueldos</td><td>1.000,50</td>
    <td>301</td><td>Aportes</td><td>200,00</td>
  </tr>
</table>
</body></html>
"""

HTML_VEP = """
<table>
  <thead><tr><th>Estado</th><th>Descripcion</th><th>Nro. VEP</th></tr></thead>
  <tbody>
    <tr><td>vencido</td><td>SIJPDJ03/25 volante</td><td>1</td></tr>
    <tr><td>pagado</td><td>SIJPDJ03/25 comprobante</td><td>2</td></tr>
    <tr><td>pendiente</td><td>SIJPDJ04/25</td><td>3</td></tr>
  </tbody>
</table>
"""


class _Pagina:
    def __init__(self, contenido: Any = None, *, cuelga: bool = False, pdf_cuelga: bool = False) -> None:
        self._contenido = contenido
        self._cuelga = cuelga
        self._pdf_cuelga = pdf_cuelga
        self.pdf_llamadas: list[str] = []

    async def content(self) -> str:
        if self._cuelga:
            await asyncio.sleep(3600)
        return self._contenido or ""

    async def pdf(self, *, path: str, format: str, print_background: bool) -> None:
        if self._pdf_cuelga:
            await asyncio.sleep(3600)
        self.pdf_llamadas.append(path)


# ---------------------------------------------------------------------------
# Cabecera y secciones (port de V1)


def test_cabecera_toma_los_ids_del_formulario() -> None:
    cabecera = extraer_cabecera_ddjj(HTML_RESUMEN)
    assert cabecera["C.U.I.T."] == "20123456789"
    assert cabecera["Mes - Año"] == "03/2025"
    assert cabecera["Apellido y Nombre o Razón Social"] == "EMPRESA FICTICIA SA"
    assert "Nro. Verificador" not in cabecera


def test_cabecera_incluye_las_sumas_de_remuneraciones_de_v1() -> None:
    html = "".join(
        f'<span id="ucF931_lblSumaRem{i}_lblImporte">{i}.000,00</span>' for i in range(1, 11)
    )

    cabecera = extraer_cabecera_ddjj(html)

    assert list(cabecera) == [f"Suma de Rem. {i}" for i in range(1, 11)]
    assert cabecera["Suma de Rem. 10"] == "10.000,00"


def test_parsear_tablas_arma_registros_por_pares_sin_encabezados() -> None:
    """V1: si no hay registros por encabezado, usa filas de cantidad par."""
    html = "<table><tr><td>Estado</td><td>Pagado</td><td>Importe</td><td>1.000,00</td></tr></table>"

    tabla = parsear_tablas(html)[0]

    assert tabla["headers"] == []
    assert tabla["records"] == [{"Estado": "Pagado", "Importe": "1.000,00"}]


def test_parsear_tablas_toma_los_th_como_encabezado_sin_thead() -> None:
    """V1: sin ``thead`` los encabezados son todas las celdas ``th``."""
    html = (
        "<table><tr><th>Estado</th><th>Descripcion</th></tr>"
        "<tr><td>pagado</td><td>SIJPDJ03/25</td></tr></table>"
    )

    tabla = parsear_tablas(html)[0]

    assert tabla["headers"] == ["Estado", "Descripcion"]
    # V1 tambien deja la fila de encabezado entre las filas de datos, asi que el
    # primer registro mapea los rotulos a si mismos.
    assert tabla["rows"][0] == ["Estado", "Descripcion"]
    assert tabla["records"][0] == {"Estado": "Estado", "Descripcion": "Descripcion"}
    assert tabla["records"][1] == {"Estado": "pagado", "Descripcion": "SIJPDJ03/25"}


def test_secciones_separa_izquierda_y_derecha_por_encabezado() -> None:
    secciones = extraer_secciones_ddjj(HTML_RESUMEN)
    assert "I - Remuneraciones" in secciones
    assert "II - Contribuciones" in secciones
    assert secciones["I - Remuneraciones"]["110 Sueldos"] == "1.000,50"
    assert secciones["II - Contribuciones"]["301 Aportes"] == "200,00"


def test_sin_tabla_de_resumen_devuelve_vacio() -> None:
    assert extraer_secciones_ddjj("<table><tr><td>x</td></tr></table>") == {}


def test_pares_codigo_descripcion_importe_y_clave_valor() -> None:
    pares = extraer_pares_de_texto("110 - Sueldos 1.000,50")
    assert pares == [("110 - Sueldos", "1.000,50")]
    assert extraer_pares_de_texto("Total: 5") == [("Total", "5")]
    assert extraer_pares_de_texto("") == []


# ---------------------------------------------------------------------------
# VEP (port de V1)


def test_periodo_desde_texto_acepta_mes_anio_y_aaaamm() -> None:
    assert periodo_desde_texto("SIJPDJ03/25 volante") == "202503"
    assert periodo_desde_texto("03/2025") == "202503"
    assert periodo_desde_texto("periodo 202503 x") == "202503"
    assert periodo_desde_texto("sin fecha") is None


def test_mapa_de_vep_prioriza_la_fila_pagada_del_periodo() -> None:
    tablas = parsear_tablas(HTML_VEP)
    mapa = mapa_estados_vep(tablas)
    assert mapa["202503"]["estado"] == "pagado"
    assert mapa["202504"]["estado"] == "pendiente"


HTML_VEP_COMPLETO = """
<table>
  <thead><tr><th>Estado</th><th>Descripcion</th><th>Nro. VEP</th><th>CUIT</th><th>Importe</th><th>Fecha de Pago</th></tr></thead>
  <tbody>
    <tr><td>pagado</td><td>SIJPDJ03/25</td><td>12345678901</td><td>20123456789</td><td>1.000,00</td><td>20/03/2025</td></tr>
  </tbody>
</table>
"""


def test_mapa_de_vep_incluye_las_columnas_rotuladas_de_v1() -> None:
    mapa = mapa_estados_vep(parsear_tablas(HTML_VEP_COMPLETO))

    assert mapa["202503"] == {
        "estado": "pagado",
        "descripcion": "SIJPDJ03/25",
        "nro_vep": "12345678901",
        "cuit": "20123456789",
        "importe": "1.000,00",
        "fecha_de_pago": "20/03/2025",
    }


def test_mapa_de_vep_usa_el_ultimo_rotulo_que_matchea() -> None:
    """V1 sobreescribe el indice: con dos columnas de estado vale la ultima."""
    html = (
        "<table><thead><tr><th>Estado</th><th>Estado del VEP</th><th>Descripcion</th></tr></thead>"
        "<tbody><tr><td>pendiente</td><td>pagado</td><td>SIJPDJ03/25</td></tr></tbody></table>"
    )

    mapa = mapa_estados_vep(parsear_tablas(html))

    assert mapa["202503"]["estado"] == "pagado"


def test_info_vep_desde_el_detalle_por_rotulos() -> None:
    html = """
    <table>
      <tr><td>Estado</td><td>Pagado</td></tr>
      <tr><td>Nro. VEP</td><td>12345678901</td></tr>
      <tr><td>CUIT</td><td>20123456789</td></tr>
      <tr><td>Importe</td><td>1.000,00</td></tr>
      <tr><td>Fecha de pago</td><td>20/03/2025</td></tr>
      <tr><td>Descripcion</td><td>SIJPDJ03/25</td></tr>
    </table>
    """

    # El periodo pedido no esta en el listado: se leen los rotulos de la pagina.
    assert extraer_info_vep(html, "202501") == {
        "estado": "Pagado",
        "nro_vep": "12345678901",
        "cuit": "20123456789",
        "importe": "1.000,00",
        "fecha_de_pago": "20/03/2025",
        "descripcion": "SIJPDJ03/25",
    }


def test_info_vep_devuelve_la_fila_del_listado_si_el_periodo_aparece() -> None:
    """Quirk de V1: sin encabezados, la fila del listado gana aunque venga vacia."""
    html = """
    <table>
      <tr><td>Estado</td><td>Pagado</td></tr>
      <tr><td>Descripcion</td><td>SIJPDJ03/25</td></tr>
    </table>
    """

    assert extraer_info_vep(html, "202503") == {
        "estado": "",
        "descripcion": "",
        "nro_vep": "",
        "cuit": "",
        "importe": "",
        "fecha_de_pago": "",
    }


def test_info_vep_prioriza_la_fila_del_periodo() -> None:
    assert extraer_info_vep(HTML_VEP_COMPLETO, "202503")["nro_vep"] == "12345678901"
    # Periodo sin fila propia: no se atribuye el VEP de otro periodo.
    assert extraer_info_vep(HTML_VEP_COMPLETO, "202504") == {}


def test_info_vep_sin_datos_devuelve_el_mapa_de_un_solo_periodo() -> None:
    html = (
        "<table><thead><tr><th>Estado</th><th>Descripcion</th></tr></thead>"
        "<tbody><tr><td>pagado</td><td>SIJPDJ03/25</td></tr></tbody></table>"
    )

    assert extraer_info_vep(html)["estado"] == "pagado"


def test_id_a_etiqueta_y_volcado_generico_como_v1() -> None:
    assert id_a_etiqueta("ucF931_lblSumaRem1_lblImporte") == "Suma Rem 1"
    assert id_a_etiqueta("ucF931_lblEmpleadosEnNomina") == "Empleados En Nomina"
    assert id_a_etiqueta("ucF931_lblCUIT") == "CUIT"

    campos = extraer_campos_ddjj(
        '<span id="ucF931_lblCUIT">20123456789</span>'
        '<span id="ucF931_lblSumaRem1_lblImporte">1.000,00</span>'
        '<span id="otro">x</span>'
    )

    assert campos == {"CUIT": "20123456789", "Suma Rem 1": "1.000,00"}


def test_estados_vep_usa_el_primer_rotulo_y_el_respaldo_por_texto() -> None:
    tablas = parsear_tablas(HTML_VEP)
    assert extraer_estados_vep(tablas) == ["vencido", "pagado", "pendiente"]

    sin_rotulos = parsear_tablas("<table><tr><td>SIJPDJ03/25 pagado</td></tr></table>")
    assert extraer_estados_vep(sin_rotulos) == ["SIJPDJ03/25 pagado"]


def test_periodos_de_v1() -> None:
    assert periodo_a_yyyymm("03/2025") == "202503"
    assert periodo_a_yyyymm("2025-03") == "202503"
    assert periodo_a_yyyymm("2025/03") == "202503"
    assert periodo_a_yyyymm("202503") == "202503"
    assert periodo_a_yyyymm("marzo") is None
    assert periodo_to_sijpdj("202503") == "SIJPDJ03/25"
    assert periodo_to_sijpdj("2025") is None
    assert variantes_periodo("202503") == [
        "202503",
        "03/2025",
        "2025/03",
        "03-2025",
        "2025-03",
        "03.2025",
    ]


def test_combinar_vep_completa_sin_pisar_lo_ya_leido() -> None:
    combinado = combinar_vep({"estado": "pagado", "nro_vep": ""}, {"estado": "vencido", "nro_vep": "7"})
    assert combinado == {"estado": "pagado", "nro_vep": "7"}


def test_parsear_tablas_arma_registros_con_encabezados() -> None:
    tablas = parsear_tablas(HTML_VEP)
    assert tablas[0]["headers"] == ["Estado", "Descripcion", "Nro. VEP"]
    assert tablas[0]["records"][1]["Estado"] == "pagado"


# ---------------------------------------------------------------------------
# JSON de resultados (port de V1)


def test_json_tiene_la_misma_forma_que_v1(tmp_path: Path) -> None:
    fijo = datetime(2026, 10, 8, 3, 0, 0, tzinfo=timezone.utc)
    payload = construir_json_ddjj(
        cabecera={"C.U.I.T.": "20123456789"},
        secciones={"I - Remuneraciones": {"110 Sueldos": "1.000,50"}},
        periodo="202503",
        vep={"estado": "pagado"},
        ahora=fijo,
    )
    assert set(payload) == {"periodo", "scraped_at", "vep", "datos", "tablas"}
    assert payload["scraped_at"] == "20261008_030000"

    destino = escribir_json_ddjj(payload, tmp_path / "ddjj.json")
    leido = json.loads(destino.read_text(encoding="utf-8"))
    assert leido["tablas"]["I - Remuneraciones"]["110 Sueldos"] == "1.000,50"


def test_json_conserva_acentos_sin_escapar(tmp_path: Path) -> None:
    payload = construir_json_ddjj(
        cabecera={"Empleados en nómina": "5"}, secciones={}, periodo="202503", vep=None
    )
    destino = escribir_json_ddjj(payload, tmp_path / "x.json")
    assert "nómina" in destino.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Tiempos acotados (port de V1)


def test_los_timeouts_coinciden_con_v1() -> None:
    assert VEP_PROBE_TIMEOUT_S == 20
    assert VEP_DOWNLOAD_TIMEOUT_S == 90
    assert PDF_RENDER_TIMEOUT_S == 60


def test_contenido_con_timeout_devuelve_html_cuando_responde() -> None:
    assert asyncio.run(contenido_con_timeout(_Pagina("<html>ok</html>"), timeout_s=1)) == "<html>ok</html>"


def test_contenido_con_timeout_no_queda_colgado_si_la_pagina_no_responde() -> None:
    async def _ir() -> str:
        return await contenido_con_timeout(_Pagina(cuelga=True), timeout_s=0.05)

    assert asyncio.run(_ir()) == ""


def test_contenido_sin_pagina_devuelve_vacio() -> None:
    assert asyncio.run(contenido_con_timeout(None)) == ""


def test_con_timeout_degrada_a_default_solo_en_timeout() -> None:
    async def _colgado() -> str:
        await asyncio.sleep(3600)
        return "nunca"

    assert asyncio.run(con_timeout(_colgado(), 0.05, default="vacio")) == "vacio"
    assert asyncio.run(con_timeout(asyncio.sleep(0, result="ok"), 1, default="x")) == "ok"


def test_con_timeout_propaga_errores_reales() -> None:
    async def _falla() -> None:
        raise RuntimeError("navegador caido")

    with pytest.raises(RuntimeError, match="navegador caido"):
        asyncio.run(con_timeout(_falla(), 1, default="x"))


def test_pdf_con_timeout_propaga_el_timeout_para_el_respaldo(tmp_path: Path) -> None:
    async def _ir() -> None:
        await pdf_con_timeout(_Pagina(pdf_cuelga=True), tmp_path / "x.pdf", timeout_s=0.05)

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(_ir())


def test_pdf_con_timeout_imprime_cuando_responde(tmp_path: Path) -> None:
    pagina = _Pagina()
    asyncio.run(pdf_con_timeout(pagina, tmp_path / "x.pdf", timeout_s=1))
    assert pagina.pdf_llamadas == [str(tmp_path / "x.pdf")]
