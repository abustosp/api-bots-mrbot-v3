"""Plugin ``ccma_compensacion``: validacion, registro y ejecucion con portal falso."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.ccma_compensacion.plugin import CcmaCompensacionPlugin
from bot_worker.bots.errors import InvalidInputError
from bot_worker.bots.registry import get_plugin
from bot_worker.runtime.context import ArtifactStore, CancellationToken
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.ccma_compensacion import (
    CcmaCompensacionPortal,
    _construir_destinos,
    _parse_monto,
)

CUIT = "20147130202"
RESUMEN = {
    "cuit": CUIT,
    "periodo": "10/2026",
    "deuda_capital": "10.00",
    "deuda_accesorios": "2.00",
    "total_deuda": "12.00",
    "credito_capital": "5.00",
    "credito_accesorios": "0.00",
    "total_a_favor": "5.00",
}


class _Portal:
    def __init__(self, detalle: dict[str, Any], avisos: list[str] | None = None) -> None:
        self.detalle = detalle
        self.avisos = avisos or []
        self.llamadas: list[tuple[str, Any]] = []

    async def seleccionar_cuit(self, cuit: str) -> None:
        self.llamadas.append(("cuit", cuit))

    async def calcular_deuda(self, periodo: str) -> None:
        self.llamadas.append(("calcular", periodo))

    async def ir_a_cuenta_corriente(self, url: str) -> None:
        self.llamadas.append(("ctacte", url))

    async def seleccionar_rango(self, valor: str) -> None:
        return None

    async def resumen(self, cuit: str, periodo_desde: str) -> dict[str, str]:
        return {**RESUMEN, "periodo_desde": periodo_desde}

    async def compensar_saldos_a_favor(self, **kwargs: Any) -> tuple[dict[str, Any], list[str]]:
        self.llamadas.append(("compensar", kwargs))
        return self.detalle, list(self.avisos)


class _Sesion:
    def __init__(self, portal: _Portal) -> None:
        self.portal = portal
        self.portales: list[Any] = []

    async def login(self) -> None:
        return None

    async def open_service(self, servicio: str, **kwargs: Any) -> _Portal:
        self.portales.append(kwargs.get("portal"))
        return self.portal


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self.sesion = sesion

    def arca_session(self, **_: Any) -> Any:
        sesion = self.sesion

        class _Ctx:
            async def __aenter__(self) -> _Sesion:
                return sesion

            async def __aexit__(self, *_: Any) -> bool:
                return False

        return _Ctx()


class _Sink:
    async def progress(self, **_: Any) -> None:
        return None


def _ejecutar(tmp_path: Path, portal: _Portal, **entrada: Any) -> tuple[Any, _Sesion]:
    plugin = CcmaCompensacionPlugin()
    validada = asyncio.run(plugin.validate({"operacion": "compensar", "cuit_representado": CUIT, **entrada}))
    sesion = _Sesion(portal)
    runtime = SimpleNamespace(
        job_id="job-ccma-comp",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia"),
        proxy=None,
        artifact_store=ArtifactStore(tmp_path, slots={}, presign=None),
        event_sink=_Sink(),
        browser_factory=_Fabrica(sesion),
        cancellation=CancellationToken(),
    )
    return asyncio.run(plugin.execute(validada, runtime)), sesion


def test_registro_y_portal() -> None:
    assert isinstance(get_plugin("ccma_compensacion"), CcmaCompensacionPlugin)
    assert portal_para("ccma_compensacion") is CcmaCompensacionPortal
    manifest = CcmaCompensacionPlugin.manifest
    assert manifest.operaciones == ("compensar",)
    assert manifest.idempotency_class == "EFECTO"


def test_validate_defaults_y_descarta_campos_del_sobre() -> None:
    plugin = CcmaCompensacionPlugin()
    _, entrada = asyncio.run(
        plugin.validate(
            {
                "operacion": "compensar",
                "cuit_representado": "20-14713020-2",
                "clave_representante": "x",
                "proxy_request": True,
                "pdf": True,
            }
        )
    )
    assert entrada.representado_cuit == CUIT
    assert entrada.compensar is True
    assert entrada.confirmar is False


def test_validate_rechaza_operacion_y_campos_extra() -> None:
    plugin = CcmaCompensacionPlugin()
    with pytest.raises(InvalidInputError):
        asyncio.run(plugin.validate({"operacion": "consultar", "representado_cuit": CUIT}))
    with pytest.raises(InvalidInputError):
        asyncio.run(plugin.validate({"operacion": "compensar", "representado_cuit": CUIT, "otro": 1}))


def test_execute_compensa_y_pasa_confirmar(tmp_path: Path) -> None:
    detalle = {"saldos": [], "reafectaciones": [{"aplicadas": [], "confirmado": True}], "pagos_sin_destino": [], "errores": []}
    portal = _Portal(detalle)
    resultado, sesion = _ejecutar(tmp_path, portal, confirmar=True)

    assert resultado.result == "OK"
    assert sesion.portales == ["ccma_compensacion"]
    assert resultado.data["compensaciones"] == detalle
    assert resultado.data["total_a_favor"] == "5.00"
    compensar = dict(portal.llamadas)["compensar"]
    assert compensar["confirmar"] is True
    assert compensar["sabana_fresca_inicial"] is True


def test_execute_parcial_si_hay_errores_y_redacta_clave(tmp_path: Path) -> None:
    detalle = {"saldos": [], "reafectaciones": [], "pagos_sin_destino": [], "errores": [{"detalle": "x"}]}
    portal = _Portal(detalle, avisos=["fallo con clave-ficticia"])
    resultado, _ = _ejecutar(tmp_path, portal)

    assert resultado.result == "PARCIAL"
    assert all("clave-ficticia" not in aviso for aviso in resultado.warnings)


def test_execute_sin_compensar_no_toca_reafectaciones(tmp_path: Path) -> None:
    portal = _Portal({})
    resultado, _ = _ejecutar(tmp_path, portal, compensar=False)

    assert resultado.result == "OK"
    assert "compensaciones" not in resultado.data
    assert "compensar" not in dict(portal.llamadas)


def test_helpers_de_compensacion() -> None:
    assert _parse_monto("(1,234.50)") == -1234.5
    movimientos = [
        {"periodo": "02/2024", "impuesto": "20", "concepto": "19", "subconcepto": "19", "debe": "10.00"},
        {"periodo": "01/2024", "impuesto": "21", "concepto": "19", "subconcepto": "19", "debe": "10.00"},
        {"periodo": "01/2024", "impuesto": "20", "concepto": "19", "subconcepto": "19", "debe": "0.00"},
    ]
    origen = {"periodo": "12/2023", "impuesto": "20", "concepto": "19", "subconcepto": "19"}
    destinos = _construir_destinos(movimientos, origen)
    # Mismo impuesto primero, luego otros por orden cronologico; sin saldo deudor no entra.
    assert [(d["periodo"], d["impuesto"]) for d in destinos] == [("02/2024", "20"), ("01/2024", "21")]
