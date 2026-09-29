"""La operación del sobre decide el branch de Mis Comprobantes.

La central fija el verbo canónico en el sobre y no lo repite en el payload; el
worker lo inyecta con ``validation_payload``. Sin esa inyección los plugins
elegían su modelo con ``payload.get("operacion", "consultar")`` y toda operación
distinta de ``consultar`` corría el branch de descarga: ``solicitar`` pedía dos
CSV en lugar de devolver los ``ids_consulta``.

Estas pruebas fijan el contrato verificado de punta a punta contra la central y
el worker reales: ``solicitar`` no descarga ni sube artefactos, y el verbo del
sobre manda sobre el cuerpo.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.comprobantes.plugin import ComprobantesPlugin
from bot_worker.bots.mis_comprobantes.plugin import MisComprobantesPlugin
from bot_worker.main import JobEnvelope, validation_payload

CUIT = "20320160856"
CLAVE_FICTICIA = "clave-ficticia"
OPERACIONES = ("consultar", "solicitar", "historial")
PLUGINS = {
    "mis_comprobantes": MisComprobantesPlugin,
    "comprobantes": ComprobantesPlugin,
}


def _cuerpo(operacion: str) -> dict[str, Any]:
    """Cuerpo plano como el que persiste la central tras normalizar."""
    cuerpo: dict[str, Any] = {
        "representado_cuit": CUIT,
        "representado_nombre": "Empresa de Prueba",
        "fecha_desde": "01/01/2026",
        "fecha_hasta": "31/01/2026",
        "emitidos": True,
        "recibidos": True,
    }
    if operacion != "solicitar":
        cuerpo["incluir_json"] = True
        cuerpo["subir_csv"] = True
    return cuerpo


def _sobre(bot: str, operacion: str) -> JobEnvelope:
    return JobEnvelope(
        protocol_version=1,
        job_id="01a0eb2e-0000-7000-8000-000000000000",
        attempt=1,
        lease_id="01a0eb2e-0001-7000-8000-000000000000",
        bot=bot,
        plugin=bot,
        operation=operacion,
        payload=_cuerpo(operacion),
    )


@pytest.mark.parametrize("bot", sorted(PLUGINS))
@pytest.mark.parametrize("operacion", OPERACIONES)
def test_el_verbo_del_sobre_manda_sobre_el_default_del_plugin(
    bot: str, operacion: str
) -> None:
    plugin = PLUGINS[bot]()
    vista = validation_payload(_sobre(bot, operacion))

    validado = asyncio.run(plugin.validate(vista))

    assert validado[0] == operacion
    assert type(validado[1]).__name__.endswith("Input")


class _ServicioSolicitar:
    """Registra qué pidió el plugin: consulta asíncrona, nunca descarga."""

    def __init__(self) -> None:
        self.consultas: list[tuple[str, str, str]] = []
        self.representados: list[str] = []

    async def seleccionar_representado(self, cuit: str) -> None:
        self.representados.append(cuit)

    async def solicitar_consulta(self, *, tipo: str, desde: str, hasta: str) -> str:
        self.consultas.append((tipo, desde, hasta))
        return f"id-{tipo}"

    async def descargar_csv(self, **kwargs: Any) -> None:  # pragma: no cover
        raise AssertionError("solicitar no debe descargar planillas")


class _Sesion:
    def __init__(self, servicio: _ServicioSolicitar) -> None:
        self.servicio = servicio

    async def login(self) -> None:
        pass

    async def open_service(self, servicio: str, **kwargs: Any) -> _ServicioSolicitar:
        return self.servicio


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self.sesion = sesion

    def arca_session(self, **kwargs: Any) -> Any:
        sesion = self.sesion

        class _Contexto:
            async def __aenter__(self) -> _Sesion:
                return sesion

            async def __aexit__(self, *args: Any) -> bool:
                return False

        return _Contexto()


class _Sink:
    async def progress(self, **kwargs: Any) -> None:
        pass


@pytest.mark.parametrize("bot", sorted(PLUGINS))
def test_solicitar_devuelve_ids_y_no_produce_artefactos(
    bot: str, tmp_path: Path
) -> None:
    from bot_worker.runtime.context import ArtifactStore, BotRuntime, CancellationToken

    def _presign(*args: Any, **kwargs: Any) -> dict[str, Any]:  # pragma: no cover
        raise AssertionError("solicitar no sube artefactos")

    async def _ejecutar() -> tuple[Any, _ServicioSolicitar]:
        servicio = _ServicioSolicitar()
        plugin = PLUGINS[bot]()
        validado = await plugin.validate(validation_payload(_sobre(bot, "solicitar")))
        runtime = BotRuntime(
            job_id=f"job-{bot}-solicitar",
            work_dir=tmp_path,
            deadline=SimpleNamespace(remaining_seconds=lambda: 60),
            credentials=SimpleNamespace(
                clave=CLAVE_FICTICIA, cuit_representante=CUIT
            ),
            proxy=None,
            artifact_store=ArtifactStore(tmp_path, slots={}, presign=_presign),
            event_sink=_Sink(),
            browser_factory=_Fabrica(_Sesion(servicio)),
            cancellation=CancellationToken(),
        )
        return await plugin.execute(validado, runtime), servicio

    resultado, servicio = asyncio.run(_ejecutar())

    assert resultado.result == "OK"
    assert resultado.artifacts == []
    assert servicio.representados == [CUIT]
    assert servicio.consultas == [
        ("emitidos", "01/01/2026", "31/01/2026"),
        ("recibidos", "01/01/2026", "31/01/2026"),
    ]
    assert resultado.data["ids_consulta"] == {
        "emitidos": "id-emitidos",
        "recibidos": "id-recibidos",
    }
    assert list(tmp_path.iterdir()) == []
