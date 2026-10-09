from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.bots.mis_retenciones.plugin import MisRetencionesPlugin
from bot_worker.bots.mis_retenciones_iva_simple.plugin import (
    IVA_SIMPLE_OPERACIONES,
    MisRetencionesIvaSimplePlugin,
)


class _Cancellation:
    async def raise_if_cancelled(self) -> None:
        return None


class _Sink:
    async def progress(self, **_: Any) -> None:
        return None


class _Artifacts:
    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve(self, name: str) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


class _SimpleService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def activar_modo_iva_simple(self) -> None:
        return None

    async def descargar_csv(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        Path(kwargs["destino"]).write_text("columna\nvalor\n", encoding="utf-8")


class _RetencionesService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def descargar_csv(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)
        if kwargs["impuesto"] == "216":
            raise TargetUnavailableError("fallo aislado", diagnostic_code="retenciones_query_failed")
        Path(kwargs["destino"]).write_text("columna\nvalor\n", encoding="utf-8")


def _runtime(tmp_path: Path) -> Any:
    return SimpleNamespace(
        cancellation=_Cancellation(), event_sink=_Sink(), artifact_store=_Artifacts(tmp_path)
    )


def test_iva_simple_ejecuta_tres_operaciones_y_dos_formatos(tmp_path: Path) -> None:
    servicio = _SimpleService()
    entrada = SimpleNamespace(
        representado_cuit="20123456789",
        representado_nombre="Empresa de prueba",
        fecha_desde="01/01/2026",
        fecha_hasta="31/01/2026",
        required_result_marker="IVA SIMPLE",
        subir_csv=False,
        incluir_json=False,
    )

    datos, artefactos = asyncio.run(
        MisRetencionesIvaSimplePlugin()._consultar(servicio, entrada, _runtime(tmp_path))
    )

    assert artefactos == []
    assert len(servicio.calls) == 6
    assert {call["operacion"] for call in servicio.calls} == set(IVA_SIMPLE_OPERACIONES)
    assert {call["export_option"] for call in servicio.calls} == {"iva_simple", "xls"}
    assert all(call["required_result_marker"] == "IVA SIMPLE" for call in servicio.calls)
    assert all(set(formatos) == {"iva_simple", "xls"} for formatos in datos["operaciones"].values())


def test_retenciones_exporta_siap_y_xls_y_continua_tras_un_impuesto_fallido(tmp_path: Path) -> None:
    servicio = _RetencionesService()
    entrada = SimpleNamespace(
        representado_cuit="20123456789",
        representado_nombre="Empresa de prueba",
        fecha_desde="01/01/2026",
        fecha_hasta="31/01/2026",
        impuestos=["216", "217"],
        tipos=["Retencion"],
        exportar_para_aplicativo=True,
        subir_csv=False,
        incluir_json=False,
    )

    datos, artefactos = asyncio.run(
        MisRetencionesPlugin()._consultar(servicio, entrada, _runtime(tmp_path))
    )

    assert artefactos == []
    assert [(call["impuesto"], call["export_option"]) for call in servicio.calls] == [
        ("216", "siap"),
        ("216", "xls"),
        ("217", "siap"),
        ("217", "xls"),
    ]
    assert len(datos["errores"]) == 2
    assert set(datos["impuestos"]["217"]["Retencion"]) == {"siap", "xls"}
    assert all("Empresa de prueba" in item["archivo"] for item in datos["impuestos"]["217"]["Retencion"].values())
