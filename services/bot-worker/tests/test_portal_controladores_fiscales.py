from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.controladores_fiscales import (
    ControladoresFiscalesPortal,
    estado_desde_error,
    estado_desde_texto,
)

from fakes_playwright import Pagina

PDF = b"%PDF-1.4 constancia"
BTN_PRESENTAR = "role:button:Presentar"
BTN_CONTINUAR = "role:button:Continuar"
ETIQUETA = "loc:label"


def _armar(tmp_path: Path, *, final: str = "Se presento: presentada correctamente",
           fallar_subida: set[int] | None = None,
           tras_continuar: str | None = None) -> tuple[Pagina, ControladoresFiscalesPortal, Path, Path]:
    archivo = tmp_path / "F8010.001.pem"
    archivo.write_text("contenido")
    destino = tmp_path / "constancia.pdf"
    pagina = Pagina()
    estado = {"cuerpo": ""}
    pagina.poner("role:button:Buscá en tus archivos")
    pagina.poner("loc:input[type='file']", fallar={"set_input_files": fallar_subida or set()})
    pagina.poner(f"text:{archivo.name}")

    def al_continuar() -> None:
        estado["cuerpo"] = tras_continuar or ""

    pagina.poner(BTN_CONTINUAR, on={"click": al_continuar})
    pagina.poner(
        ETIQUETA,
        visible=lambda: tras_continuar is None,
    )
    pagina.poner(f"{ETIQUETA}>input[type='checkbox']")
    pagina.poner(BTN_PRESENTAR, on={"click": lambda: estado.update(cuerpo=final)})
    pagina.poner("loc:body", texto=lambda: estado["cuerpo"])
    pagina.poner(
        "role:button:Descargar Constancia", on={"click": pagina.descargar_al_click(PDF)}
    )
    pagina.poner(
        'loc:a[title="Exportar detalle en archivo PDF"]',
        on={"click": pagina.descargar_al_click(PDF)},
    )
    portal = ControladoresFiscalesPortal(pagina)
    portal.intervalo_sondeo_s = 0.0
    portal.espera_tras_continuar_s = 0.2
    portal.espera_resultado_s = 0.2
    portal.espera_pantalla_carga_s = 0.2
    return pagina, portal, archivo, destino


def test_se_registra_por_descubrimiento() -> None:
    assert portal_para("controladores_fiscales") is ControladoresFiscalesPortal


def test_estado_desde_texto_clasifica() -> None:
    assert estado_desde_texto("La DDJJ ya ha sido presentada") == "ya_presentado"
    assert estado_desde_texto("Presentada correctamente") == "presentado_correctamente"
    assert estado_desde_texto("nada") is None
    err = estado_desde_error("Se ha producido un error\n* Error X\nCódigo error\n42")
    assert err == "error|* Error X; código 42"


def test_presenta_y_descarga_constancia_nueva(tmp_path: Path) -> None:
    pagina, portal, archivo, destino = _armar(tmp_path)

    estado = asyncio.run(portal.presentar_archivo(archivo, destino))

    assert estado == "presentado_correctamente"
    assert destino.read_bytes() == PDF
    assert pagina.clicks(BTN_PRESENTAR) == 1
    assert ("set_input_files", "loc:input[type='file']", archivo.name) in pagina.eventos
    assert pagina.registro[f"{ETIQUETA}>input[type='checkbox']"].marcado


def test_ya_presentado_no_pulsa_presentar_y_baja_constancia_existente(tmp_path: Path) -> None:
    pagina, portal, archivo, destino = _armar(
        tmp_path, tras_continuar="Esta declaración ya ha sido presentada"
    )

    estado = asyncio.run(portal.presentar_archivo(archivo, destino))

    assert estado == "ya_presentado"
    assert pagina.clicks(BTN_PRESENTAR) == 0
    assert destino.read_bytes() == PDF
    assert pagina.clicks('loc:a[title="Exportar detalle en archivo PDF"]') == 1


def test_reintenta_solo_antes_de_presentar(tmp_path: Path) -> None:
    pagina, portal, archivo, destino = _armar(tmp_path, fallar_subida={1})

    estado = asyncio.run(portal.presentar_archivo(archivo, destino))

    assert estado == "presentado_correctamente"
    subidas = [e for e in pagina.intentos if e == ("set_input_files", "loc:input[type='file']")]
    assert len(subidas) == 2
    assert pagina.clicks(BTN_PRESENTAR) == 1


def test_falla_persistente_previa_agota_intentos_sin_presentar(tmp_path: Path) -> None:
    pagina, portal, archivo, destino = _armar(tmp_path, fallar_subida={1, 2, 3})

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.presentar_archivo(archivo, destino))

    assert error.value.diagnostic_code == "controladores_preparacion_failed"
    assert pagina.clicks(BTN_PRESENTAR) == 0


def test_error_tras_presentar_no_reintenta(tmp_path: Path) -> None:
    pagina, portal, archivo, destino = _armar(
        tmp_path, final="Se ha producido un error\n* Error de sistema"
    )

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.presentar_archivo(archivo, destino))

    assert error.value.diagnostic_code == "controladores_presentacion_error"
    assert pagina.clicks(BTN_PRESENTAR) == 1
    assert not destino.exists()
    subidas = [e for e in pagina.intentos if e[0] == "set_input_files"]
    assert len(subidas) == 1


def test_timeout_tras_presentar_no_reintenta(tmp_path: Path) -> None:
    pagina, portal, archivo, destino = _armar(tmp_path, final="sin novedades")

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.presentar_archivo(archivo, destino))

    assert error.value.diagnostic_code == "controladores_presentacion_timeout"
    assert pagina.clicks(BTN_PRESENTAR) == 1


def test_click_presentar_ambiguo_no_reintenta(tmp_path: Path) -> None:
    pagina, portal, archivo, destino = _armar(tmp_path)
    pagina.registro[BTN_PRESENTAR].fallar = {"click": {1}}

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.presentar_archivo(archivo, destino))

    assert error.value.diagnostic_code == "controladores_presentar_click_ambiguo"
    assert [e for e in pagina.intentos if e == ("click", BTN_PRESENTAR)] == [("click", BTN_PRESENTAR)]


def test_archivo_inexistente(tmp_path: Path) -> None:
    _, portal, _, destino = _armar(tmp_path)

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.presentar_archivo(tmp_path / "no_existe.pem", destino))

    assert error.value.diagnostic_code == "controladores_archivo_missing"


def test_sin_boton_de_constancia_informa_diagnostico(tmp_path: Path) -> None:
    pagina, portal, archivo, destino = _armar(tmp_path)
    del pagina.registro["role:button:Descargar Constancia"]

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.presentar_archivo(archivo, destino))

    assert error.value.diagnostic_code == "controladores_constancia_failed"
    assert pagina.clicks(BTN_PRESENTAR) == 1
