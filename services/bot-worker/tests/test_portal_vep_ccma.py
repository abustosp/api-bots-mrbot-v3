from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.vep_ccma import (
    FIELD_MAP_IMPUESTOS,
    FIELD_MAP_INTERESES,
    VepCcmaPortal,
    _check_volante_rows,
    _row_matches_filtro,
    _scrape_volante_table,
    indices_a_seleccionar,
)

from fakes_playwright import Pagina

PDF = b"%PDF-1.4 ccma"
GENERAR = "role:button:GENERAR VEP O QR"
VOLANTE = "role:button:VOLANTE DE PAGO"
IMG_LINK = 'loc:img[id="1001"]'
FILAS = [
    {"checkbox_index": 0, "tipo": "capital", "periodo": "01/2026", "impuesto": "20",
     "concepto": "19", "subconcepto": "19", "categoria": "A", "importe": 100.0},
    {"checkbox_index": 1, "tipo": "capital", "periodo": "02/2026", "impuesto": "20",
     "concepto": "19", "subconcepto": "19", "categoria": "B", "importe": 200.0},
    {"checkbox_index": 2, "tipo": "interes", "periodo": "01/2026", "impuesto": "20",
     "concepto": "19", "subconcepto": "51", "categoria": "", "importe": 5.0},
]


def _armar(*, filas: Any = None, con_volante: bool = True) -> tuple[Pagina, VepCcmaPortal]:
    pagina = Pagina("https://servicios1.afip.gov.ar/")
    pagina.poner("role:cell:PERÍODO DESDE (MM/AAAA)")
    pagina.poner('loc:input[name="perdesde2"]')
    pagina.poner("role:button:CALCULO DE DEUDA")
    pagina.poner('loc:select[name="rango"]')
    if con_volante:
        pagina.poner(VOLANTE)
    pagina.poner(GENERAR)
    pagina.poner("role:button:Aceptar")
    pagina.poner(IMG_LINK)
    pagina.poner("role:button:Descargar VEP", on={"click": pagina.descargar_al_click(PDF)})
    datos = FILAS if filas is None else filas
    pagina.evaluar = lambda script, arg: list(datos) if "rows.push" in script else None
    return pagina, VepCcmaPortal(pagina)


def test_se_registra_por_descubrimiento() -> None:
    assert portal_para("vep_ccma") is VepCcmaPortal


def test_row_matches_filtro_and_y_listas() -> None:
    fila = FILAS[0]
    assert _row_matches_filtro(fila, {"periodo": "01/2026"}, FIELD_MAP_IMPUESTOS)
    assert _row_matches_filtro(fila, {"periodo": ["01/2026", "02/2026"], "categoria": "A"}, FIELD_MAP_IMPUESTOS)
    assert not _row_matches_filtro(fila, {"periodo": "01/2026", "categoria": "B"}, FIELD_MAP_IMPUESTOS)
    assert _row_matches_filtro(fila, {"periodo": [], "concepto": ""}, FIELD_MAP_IMPUESTOS)
    # alias de interés: subcpto -> subconcepto
    assert _row_matches_filtro(FILAS[2], {"subcpto": "51"}, FIELD_MAP_INTERESES)
    assert not _row_matches_filtro(FILAS[2], {"subcpto": "19"}, FIELD_MAP_INTERESES)


def test_indices_sin_filtro_toma_todo_y_con_filtro_solo_coincidentes() -> None:
    capital = FILAS[:2]
    intereses = FILAS[2:]
    assert indices_a_seleccionar(capital, intereses) == [0, 1, 2]
    assert indices_a_seleccionar(
        capital, intereses, filtro_impuestos=[{"categoria": "B"}], filtro_intereses=[{"subcpto": "99"}]
    ) == [1]
    assert indices_a_seleccionar([], []) == []


def test_scrape_y_check_usan_evaluate() -> None:
    pagina, _ = _armar()

    filas = asyncio.run(_scrape_volante_table(pagina))
    asyncio.run(_check_volante_rows(pagina, [0, 2]))

    assert [f["checkbox_index"] for f in filas] == [0, 1, 2]
    assert pagina.evaluaciones[-1][1] == {"indices": [0, 2]}
    # respuesta no lista -> vacío
    pagina.evaluar = lambda *_: {"x": 1}
    assert asyncio.run(_scrape_volante_table(pagina)) == []


def test_seleccionar_representado_con_listbox() -> None:
    pagina, portal = _armar()
    pagina.poner("role:listbox:None")
    pagina.poner("role:button:Elegir CUIT")

    asyncio.run(portal.seleccionar_representado("20-12345678-9"))

    assert ("select_option", "role:listbox:None", "20123456789") in pagina.eventos
    assert pagina.clicks("role:button:Elegir CUIT") == 1


def test_seleccionar_representado_sin_listbox_es_noop() -> None:
    pagina, portal = _armar()

    asyncio.run(portal.seleccionar_representado("20123456789"))

    assert pagina.eventos == []


def test_seleccionar_representado_invalido_o_no_admitido() -> None:
    pagina, portal = _armar()
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.seleccionar_representado("123"))
    assert error.value.diagnostic_code == "represented_cuit_invalid"

    pagina.poner("role:listbox:None", fallar={"select_option": {1}})
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.seleccionar_representado("20123456789"))
    assert error.value.diagnostic_code == "represented_cuit_not_selectable"


def test_listar_obligaciones_separa_capital_e_interes() -> None:
    pagina, portal = _armar()

    resultado = asyncio.run(portal.listar_obligaciones())

    assert [f["checkbox_index"] for f in resultado["impuestos"]] == [0, 1]
    assert [f["checkbox_index"] for f in resultado["intereses"]] == [2]
    assert ("fill", 'loc:input[name="perdesde2"]', "04/2000") in pagina.eventos
    assert pagina.clicks("role:button:CALCULO DE DEUDA") == 1
    assert pagina.clicks(VOLANTE) == 1


def test_listar_obligaciones_volante_vacio_o_sin_boton() -> None:
    _, portal = _armar(filas=[])
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.listar_obligaciones())
    assert error.value.diagnostic_code == "vep_ccma_volante_vacio"

    _, portal = _armar(con_volante=False)
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.listar_obligaciones())
    assert error.value.diagnostic_code == "vep_ccma_volante_button_missing"


def test_generar_volante_tilda_filas_y_pulsa_generar_una_vez() -> None:
    pagina, portal = _armar()
    seleccion = {"impuestos": FILAS[:2], "intereses": FILAS[2:]}

    resultado = asyncio.run(portal.generar_volante(seleccion=seleccion, medio_pago="link"))

    assert [f["checkbox_index"] for f in resultado["volante"]] == [0, 1, 2]
    assert pagina.clicks(GENERAR) == 1
    assert pagina.clicks(IMG_LINK) == 1
    tildar = [a for _, a in pagina.evaluaciones if isinstance(a, dict)]
    assert tildar == [{"indices": [0, 1, 2]}]


def test_generar_volante_solo_impuestos() -> None:
    pagina, portal = _armar()

    resultado = asyncio.run(
        portal.generar_volante(
            seleccion={"impuestos": FILAS[:2], "intereses": []}, medio_pago="link"
        )
    )

    assert [f["checkbox_index"] for f in resultado["volante"]] == [0, 1]


def test_generar_volante_sin_seleccion_o_medio_invalido_no_toca_el_portal() -> None:
    pagina, portal = _armar()
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.generar_volante(seleccion={"impuestos": [], "intereses": []}, medio_pago="link"))
    assert error.value.diagnostic_code == "vep_ccma_seleccion_vacia"
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.generar_volante(seleccion={"impuestos": FILAS}, medio_pago="paypal"))
    assert error.value.diagnostic_code == "vep_medio_pago_invalid"
    assert pagina.eventos == []
    assert pagina.evaluaciones == []


def test_boton_generar_ausente_falla_antes_del_efecto() -> None:
    pagina, portal = _armar()
    del pagina.registro[GENERAR]

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.generar_volante(seleccion={"impuestos": FILAS[:1]}, medio_pago="link"))

    assert error.value.diagnostic_code == "vep_ccma_generar_button_missing"
    assert pagina.clicks(IMG_LINK) == 0


def test_click_generar_ambiguo_no_se_reintenta() -> None:
    pagina, portal = _armar()
    pagina.registro[GENERAR].fallar = {"click": {1}}

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.generar_volante(seleccion={"impuestos": FILAS[:1]}, medio_pago="link"))

    assert error.value.diagnostic_code == "vep_ccma_generar_click_ambiguo"
    assert [i for i in pagina.intentos if i == ("click", GENERAR)] == [("click", GENERAR)]
    assert pagina.clicks(IMG_LINK) == 0


def test_descargar_detalle_y_qr_inactivo(tmp_path: Path) -> None:
    pagina, portal = _armar()
    destino = tmp_path / "vep.pdf"

    asyncio.run(portal.descargar_detalle(destino))

    assert destino.read_bytes() == PDF
    # sin medio qr el QR no se intenta
    assert asyncio.run(portal.descargar_qr(tmp_path / "qr.png")) is False
    assert not (tmp_path / "qr.png").exists()
