"""Paridad de ``consulta_pagos_vep`` con V1 (``bot_consulta_pagos_veps``).

Cubre el plugin con un servicio falso y el portal con el fake de Playwright.
El punto central es la seleccion del periodo: V1 busca primero el combo
llamado con el periodo cambiado de ``72`` a ``12`` y, si no esta, usa el ultimo
combo numerico.
"""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.consulta_pagos_vep.plugin import ConsultaPagosVepPlugin
from bot_worker.bots.errors import InvalidInputError, TargetUnavailableError
from bot_worker.runtime.context import ArtifactStore, CancellationToken
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.consulta_pagos_vep import ConsultaPagosVepPortal

from fakes_playwright import Pagina

CUIT = "20123456786"
CSV_BYTES = b"fecha,monto\n01/01/2026,100\n02/01/2026,200\n"
COMBO_12 = "role:combobox:^12$"
COMBO_NUMERICO = "role:combobox:^d+$"


# ----------------------------------------------------------------------
# Plugin con servicio falso

class _Servicio:
    def __init__(self) -> None:
        self.periodo: str | None = None
        self.representado: str | None = None

    async def seleccionar_representado(self, cuit: str) -> None:
        self.representado = cuit

    async def exportar_pagos_csv(self, destino: Path, periodo: str = "72") -> Path:
        self.periodo = periodo
        Path(destino).write_bytes(CSV_BYTES)
        return Path(destino)


class _Sesion:
    def __init__(self, servicio: _Servicio) -> None:
        self.servicio = servicio

    async def login(self) -> None:
        return None

    async def open_service(self, servicio: str, **_: Any) -> _Servicio:
        return self.servicio


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self.sesion = sesion

    def arca_session(self, **_: Any) -> Any:
        sesion = self.sesion

        class _Contexto:
            async def __aenter__(self) -> _Sesion:
                return sesion

            async def __aexit__(self, *_: Any) -> bool:
                return False

        return _Contexto()


class _Sink:
    async def progress(self, **_: Any) -> None:
        return None


class _Presign:
    def __init__(self) -> None:
        self.ids: list[str] = []

    async def __call__(self, artifact_id: str, content_type: str, size: int, filename: str = "") -> dict[str, Any]:
        self.ids.append(artifact_id)
        return {"upload_url": "", "object_key": f"jobs/x/{artifact_id}"}


def _ejecutar(tmp_path: Path, **entrada: Any) -> tuple[Any, _Servicio, _Presign]:
    plugin = ConsultaPagosVepPlugin()
    validado = asyncio.run(
        plugin.validate({"operacion": "consultar", "representado_cuit": CUIT, **entrada})
    )
    servicio = _Servicio()
    presign = _Presign()
    runtime = SimpleNamespace(
        job_id="job-pagos",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia", cuit_representante="20111111116"),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path,
            slots={},
            presign=presign,
            declarados={"pagos_vep.csv": (("text/csv",), 52_428_800)},
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(_Sesion(servicio)),
        cancellation=CancellationToken(),
    )
    resultado = asyncio.run(plugin.execute(validado, runtime))
    return resultado, servicio, presign


def test_se_registra_por_descubrimiento() -> None:
    assert portal_para("consulta_pagos_vep") is ConsultaPagosVepPortal


def test_exporta_csv_sube_y_cuenta_filas(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path)

    assert resultado.result == "OK"
    assert servicio.representado == CUIT
    assert servicio.periodo == "72"
    assert resultado.data["filas"] == 2
    assert resultado.data["muestra"][0] == {"fecha": "01/01/2026", "monto": "100"}
    assert presign.ids == ["pagos_vep_csv"]


def test_periodo_llega_al_portal(tmp_path: Path) -> None:
    resultado, servicio, _ = _ejecutar(tmp_path, periodo="12")

    assert servicio.periodo == "12"
    assert resultado.data["periodo"] == "12"


def test_subir_csv_false_no_sube_pero_conserva_el_resultado(tmp_path: Path) -> None:
    resultado, _, presign = _ejecutar(tmp_path, minio_upload=False)

    assert presign.ids == []
    assert resultado.artifacts == []
    assert resultado.data["filas"] == 2


def test_incluir_json_false_omite_muestra(tmp_path: Path) -> None:
    resultado, _, _ = _ejecutar(tmp_path, incluir_json=False)

    assert "muestra" not in resultado.data


def test_representado_invalido_se_rechaza_antes_del_navegador(tmp_path: Path) -> None:
    plugin = ConsultaPagosVepPlugin()
    with pytest.raises(InvalidInputError):
        asyncio.run(plugin.validate({"operacion": "consultar", "representado_cuit": "123"}))


# ----------------------------------------------------------------------
# Portal: seleccion de periodo como V1

def _portal_con_combos(combos: dict[str, bool]) -> tuple[Pagina, ConsultaPagosVepPortal]:
    pagina = Pagina("https://seti.afip.gob.ar/")
    for clave in combos:
        pagina.poner(clave)
    portal = ConsultaPagosVepPortal(pagina)
    portal._preparado = True
    return pagina, portal


def test_periodo_72_busca_primero_combo_12_como_v1() -> None:
    pagina, portal = _portal_con_combos({COMBO_12: True, COMBO_NUMERICO: True})

    asyncio.run(portal._seleccionar_periodo("72"))

    assert pagina.clicks(COMBO_12) == 1
    assert pagina.clicks(COMBO_NUMERICO) == 0


def test_sin_combo_12_usa_el_ultimo_combo_numerico_como_v1() -> None:
    pagina, portal = _portal_con_combos({COMBO_NUMERICO: True})

    asyncio.run(portal._seleccionar_periodo("72"))

    assert pagina.clicks(COMBO_NUMERICO) == 1
    assert pagina.clicks(COMBO_12) == 0


def test_periodo_distinto_de_72_no_busca_el_combo_12() -> None:
    pagina, portal = _portal_con_combos({COMBO_12: True, COMBO_NUMERICO: True})

    asyncio.run(portal._seleccionar_periodo("6"))

    assert pagina.clicks(COMBO_12) == 0
    assert pagina.clicks(COMBO_NUMERICO) == 1


def _portal_con_opcion_representado(
    cuit_seleccionado: str | None,
) -> tuple[Pagina, ConsultaPagosVepPortal]:
    pagina = Pagina()
    opcion_id = f"cuitcontribuyente-multiselect-option-{CUIT}"
    pagina.poner(f"loc:#{opcion_id}", attrs={"id": opcion_id})
    fake = pagina.poner("loc:.multiselect-fake-input", attrs={"value": ""})

    def aplicar_click(_script: str, argumento: Any) -> bool:
        if argumento == opcion_id and cuit_seleccionado is not None:
            fake.attrs["value"] = cuit_seleccionado
        return argumento == opcion_id

    pagina.evaluar = aplicar_click
    portal = ConsultaPagosVepPortal(pagina)
    return pagina, portal


def test_click_opcion_no_confirma_representado_si_fake_input_no_cambia() -> None:
    _, portal = _portal_con_opcion_representado(None)

    seleccionado = asyncio.run(portal._click_opcion(CUIT))

    assert seleccionado is False


def test_click_opcion_confirma_cuit_reflejado_en_fake_input() -> None:
    _, portal = _portal_con_opcion_representado("20-12345678-6")

    seleccionado = asyncio.run(portal._click_opcion(CUIT))

    assert seleccionado is True


def test_sin_ningun_combo_no_falla() -> None:
    _, portal = _portal_con_combos({})

    asyncio.run(portal._seleccionar_periodo("72"))


def test_exportar_sin_boton_de_descarga_informa_diagnostico(tmp_path: Path) -> None:
    pagina, portal = _portal_con_combos({})

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.exportar_pagos_csv(tmp_path / "pagos.csv", "72"))

    assert error.value.diagnostic_code == "portal_download_missing"
