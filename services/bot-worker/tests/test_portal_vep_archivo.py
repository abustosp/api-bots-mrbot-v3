from __future__ import annotations

import asyncio
import base64
import zipfile
from pathlib import Path

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.vep_archivo import (
    ArchivoVEP,
    VepArchivoPortal,
    contar_registros,
    crear_zip,
    decodificar_qr,
    lote_desde_nombre,
)

from fakes_playwright import Pagina

PDF = b"%PDF-1.4 vep"
GENERAR = "role:button:Generar VEP"
DESDE_ARCHIVO = "text:description Desde archivo"
ENTRADA = '#VepSeleccionarArchivo input[type="file"]'
IMG_INTERNET_BANKING = 'loc:img[id="1003"]'
DESCARGAR = "role:button:Descargar VEP"


def _armar(tmp_path: Path, *, url: str = "https://seti.afip.gob.ar/", con_descarga: bool = True) -> tuple[Pagina, VepArchivoPortal, Path]:
    archivo = tmp_path / "F20001.cuit.20111222333.fecha.20261008.nro.001de002.txt"
    archivo.write_text("01...\n02<VEP/>\n02<VEP/>\n", encoding="utf-8")
    pagina = Pagina(url)
    pagina.poner("role:button:Aceptar")
    pagina.poner(GENERAR)
    pagina.poner(DESDE_ARCHIVO)
    pagina.poner("role:button:Close")
    pagina.poner(f"loc:{ENTRADA}")
    pagina.poner("text:close")
    pagina.poner(IMG_INTERNET_BANKING)
    if con_descarga:
        pagina.poner(DESCARGAR, on={"click": pagina.descargar_al_click(PDF)})
    portal = VepArchivoPortal(pagina)
    portal.espera_pantalla_error_s = 0.0
    return pagina, portal, archivo


def test_se_registra_por_descubrimiento() -> None:
    assert portal_para("vep_archivo") is VepArchivoPortal


def test_genera_vep_y_elige_medio_de_pago(tmp_path: Path) -> None:
    pagina, portal, archivo = _armar(tmp_path)

    resumen = asyncio.run(portal.generar_vep_desde_archivo(archivo, "internet_banking"))

    assert resumen == {"lote": "001de002", "registros": 2}
    assert ("set_input_files", f"loc:{ENTRADA}", archivo.name) in pagina.eventos
    assert pagina.clicks(IMG_INTERNET_BANKING) == 1
    # un click para abrir el flujo y uno final de efecto
    assert pagina.clicks(GENERAR) == 2


def test_medio_de_pago_invalido_no_toca_el_portal(tmp_path: Path) -> None:
    pagina, portal, archivo = _armar(tmp_path)

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.generar_vep_desde_archivo(archivo, "bitcoin"))

    assert error.value.diagnostic_code == "vep_medio_pago_invalid"
    assert pagina.eventos == []


def test_archivo_inexistente(tmp_path: Path) -> None:
    _, portal, _ = _armar(tmp_path)

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.generar_vep_desde_archivo(tmp_path / "no.txt", "link"))

    assert error.value.diagnostic_code == "vep_archivo_missing"


def test_reintenta_pasos_previos_al_click_final(tmp_path: Path) -> None:
    pagina, portal, archivo = _armar(tmp_path)
    pagina.registro[DESDE_ARCHIVO].fallar = {"click": {1}}

    asyncio.run(portal.generar_vep_desde_archivo(archivo, "internet_banking"))

    # intento 1 (falla en Desde archivo) + intento 2 + click final
    assert pagina.clicks(GENERAR) == 3
    assert pagina.clicks(DESDE_ARCHIVO) == 1  # el click fallido no se registra como evento


def test_pasos_previos_agotan_reintentos_sin_click_final(tmp_path: Path) -> None:
    pagina, portal, archivo = _armar(tmp_path)
    pagina.registro[DESDE_ARCHIVO].fallar = {"click": {1, 2, 3}}

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.generar_vep_desde_archivo(archivo, "internet_banking"))

    assert error.value.diagnostic_code == "vep_preparacion_failed"
    # 3 clicks de apertura y ninguno final
    assert pagina.clicks(GENERAR) == 3
    assert pagina.clicks(IMG_INTERNET_BANKING) == 0


def test_click_final_ambiguo_no_se_reintenta(tmp_path: Path) -> None:
    pagina, portal, archivo = _armar(tmp_path)
    pagina.registro[GENERAR].fallar = {"click": {2}}

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.generar_vep_desde_archivo(archivo, "internet_banking"))

    assert error.value.diagnostic_code == "vep_generar_click_ambiguo"
    assert [i for i in pagina.intentos if i == ("click", GENERAR)] == [("click", GENERAR)] * 2
    assert pagina.clicks(IMG_INTERNET_BANKING) == 0


def test_pantalla_de_error_tras_generar_no_reintenta(tmp_path: Path) -> None:
    pagina, portal, archivo = _armar(tmp_path, url="https://seti.afip.gob.ar/#/error")

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.generar_vep_desde_archivo(archivo, "internet_banking"))

    assert error.value.diagnostic_code == "vep_pantalla_error"
    assert pagina.clicks(GENERAR) == 2
    assert pagina.clicks(IMG_INTERNET_BANKING) == 0


def test_descargar_detalle_boton_directo(tmp_path: Path) -> None:
    pagina, portal, _ = _armar(tmp_path)
    destino = tmp_path / "salida" / "detalle.pdf"

    resultado = asyncio.run(portal.descargar_detalle(destino))

    assert resultado == destino
    assert destino.read_bytes() == PDF


def test_descargar_detalle_por_popup_si_falla_el_boton(tmp_path: Path) -> None:
    pagina, portal, _ = _armar(tmp_path, con_descarga=False)
    popup = Pagina()
    popup.poner("role:button:picture_as_pdf", on={"click": popup.descargar_al_click(PDF)})
    pagina.popup = popup
    pagina.poner("title:Ver detalle")
    destino = tmp_path / "detalle.pdf"

    asyncio.run(portal.descargar_detalle(destino))

    assert destino.read_bytes() == PDF
    assert popup.cerrada


def test_descargar_detalle_sin_ninguna_via_informa_diagnostico(tmp_path: Path) -> None:
    _, portal, _ = _armar(tmp_path, con_descarga=False)

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.descargar_detalle(tmp_path / "detalle.pdf"))

    assert error.value.diagnostic_code in {
        "portal_step_descargar_detalle",
        "vep_detalle_download_failed",
    }


def test_crear_zip_usa_stdlib_y_estructura_por_lote() -> None:
    archivos = [
        ArchivoVEP("a.txt", "01\n02", "20111222333", "7", 1, 1),
        ArchivoVEP("b.txt", "01\n02", "20111222333", "8", 1, 1),
    ]

    buffer = crear_zip(archivos)

    with zipfile.ZipFile(buffer) as zf:
        assert sorted(zf.namelist()) == [
            "20111222333/lote_7/a.txt",
            "20111222333/lote_8/b.txt",
        ]
        assert zf.read("20111222333/lote_7/a.txt") == b"01\n02"


def test_helpers_puros() -> None:
    assert contar_registros("01\n02a\n02b\n") == 2
    assert lote_desde_nombre("x.nro.003de010.txt") == "003de010"
    assert lote_desde_nombre("x.txt") == ""
    datos = base64.b64encode(b"png").decode()
    assert decodificar_qr(f"data:image/png;base64,{datos}") == b"png"
    assert decodificar_qr("data:image/png,xyz") is None
    assert decodificar_qr(None) is None


def test_qr_guardado_si_el_modal_lo_trae(tmp_path: Path) -> None:
    pagina, portal, _ = _armar(tmp_path)
    datos = base64.b64encode(b"png").decode()
    pagina.poner('loc:div.modal:has-text("Pagar con QR")')
    pagina.poner(
        'loc:div.modal:has-text("Pagar con QR")>img',
        attrs={"src": f"data:image/png;base64,{datos}"},
    )
    destino = tmp_path / "qr.png"

    assert asyncio.run(portal.descargar_qr(destino)) is True
    assert destino.read_bytes() == b"png"
    assert asyncio.run(VepArchivoPortal(Pagina()).descargar_qr(tmp_path / "x.png")) is False
