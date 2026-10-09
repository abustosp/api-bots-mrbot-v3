"""Bot ``apoc``: busqueda local de condicion apocrifa por CUIT.

Cubre el port puro de ``buscar_cuit_en_base_apoc`` de V1 (lineas
``CUIT, Fecha Condicion, Fecha Publicacion[, Descripcion]``), el ``execute``
del plugin con un runtime falso y la base real de V1 cuando esta disponible.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from bot_worker.bots.apoc.plugin import ApocPlugin, buscar_en_base
from bot_worker.bots.errors import InvalidInputError, TargetUnavailableError
from bot_worker.runtime.context import (
    ArtifactStore,
    BotRuntime,
    CancellationToken,
    DeadlineBudget,
)

CUIT = "30703948983"
#: Base real de V1 (``api-bots-mrbot/data/FacturasApocrifas.txt``).
BASE_V1 = (
    Path(__file__).resolve().parents[4]
    / "api-bots-mrbot"
    / "data"
    / "FacturasApocrifas.txt"
)


class _Sink:
    async def progress(self, phase: str, percent: int, message: str) -> None:
        return None


def _runtime(tmp_path: Path) -> BotRuntime:
    return BotRuntime(
        job_id="job-apoc",
        work_dir=tmp_path,
        deadline=DeadlineBudget(deadline_epoch=time.time() + 60),
        credentials=None,
        proxy=None,
        artifact_store=ArtifactStore(work_dir=tmp_path, slots={}),
        event_sink=_Sink(),
        browser_factory=None,  # type: ignore[arg-type]
        cancellation=CancellationToken(),
    )


def test_busca_ignora_encabezados_y_acepta_descripcion_y_tab_final():
    base = (
        "# Padrón de prueba\n"
        "# Encabezado sintético\n"
        "# CUIT,Fecha Condicion Apocrifo, Fecha Publicacion, Descripcion,\t\n"
        "20-12345678-9,01/02/2024,03/04/2024,Descripción sintética,\t\n"
    )
    resultado = buscar_en_base("20123456789", base)
    assert resultado == {
        "cuit": "20123456789",
        "fecha_condicion": "01/02/2024",
        "fecha_publicacion": "03/04/2024",
        "apoc": True,
    }


def test_busca_base_con_bom():
    base = "\ufeff# encabezado\n20-12345678-9,fecha A,fecha B,detalle,\t\n"
    assert buscar_en_base("20 12345678 9", base)["apoc"] is True


def test_cuit_ausente_devuelve_falso():
    resultado = buscar_en_base("20123456789", "# encabezado\n27-98765432-1,x,y,detalle,\t\n")
    assert resultado == {
        "cuit": "20123456789",
        "fecha_condicion": None,
        "fecha_publicacion": None,
        "apoc": False,
    }


def test_base_grande_sintetica():
    filas = [f"20-{numero:08d}-1,fecha A,fecha B,detalle sintético,\t" for numero in range(50_000)]
    base = "# encabezado\n" + "\n".join(filas)
    resultado = buscar_en_base("20-00049999-1", base)
    assert resultado["apoc"] is True
    assert resultado["cuit"] == "20000499991"


def test_configure_guarda_texto_sin_copiarlo_a_workdir():
    texto = "# base sintética\n"
    plugin = ApocPlugin().configure({"apoc_base_text": texto})
    assert plugin._base_text is texto


def test_validate_exige_operacion_y_cuit_de_11_digitos():
    plugin = ApocPlugin()

    _, entrada = asyncio.run(
        plugin.validate({"operacion": "consultar", "cuit": "30-70394898-3"})
    )
    assert entrada.cuit == CUIT

    for payload in (
        {"operacion": "otra", "cuit": CUIT},
        {"operacion": "consultar", "cuit": "123"},
    ):
        with pytest.raises(InvalidInputError):
            asyncio.run(plugin.validate(payload))


def test_execute_busca_en_la_base_provisionada_y_copia_el_resultado(tmp_path: Path) -> None:
    async def _ir() -> None:
        plugin = ApocPlugin().configure(
            {"apoc_base_text": "# encabezado\n" + f"{CUIT},08/11/2005,08/11/2005,,\t\n"}
        )
        validado = await plugin.validate({"operacion": "consultar", "cuit": CUIT})

        resultado = await plugin.execute(validado, _runtime(tmp_path))

        assert resultado.result == "OK"
        assert resultado.data == {
            "cuit": CUIT,
            "fecha_condicion": "08/11/2005",
            "fecha_publicacion": "08/11/2005",
            "apoc": True,
        }
        assert resultado.warnings == []
        copia = json.loads((tmp_path / "resultado.json").read_text(encoding="utf-8"))
        assert copia["apoc"] is True

    asyncio.run(_ir())


def test_execute_sin_base_provisionada_falla_como_v1(tmp_path: Path) -> None:
    async def _ir() -> None:
        plugin = ApocPlugin()
        validado = await plugin.validate({"operacion": "consultar", "cuit": CUIT})

        with pytest.raises(TargetUnavailableError) as error:
            await plugin.execute(validado, _runtime(tmp_path))

        assert error.value.diagnostic_code == "apoc_base_missing"

    asyncio.run(_ir())


@pytest.mark.skipif(not BASE_V1.is_file(), reason="base APOC de V1 no disponible")
def test_paridad_con_la_base_real_de_v1(tmp_path: Path) -> None:
    texto = BASE_V1.read_text(encoding="utf-8-sig")
    presente = buscar_en_base(CUIT, texto)
    ausente = buscar_en_base("20123456786", texto)

    assert presente == {
        "cuit": CUIT,
        "fecha_condicion": "08/11/2005",
        "fecha_publicacion": "08/11/2005",
        "apoc": True,
    }
    assert ausente["apoc"] is False
