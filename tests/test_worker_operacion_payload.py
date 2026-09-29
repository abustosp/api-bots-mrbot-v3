"""La operación del sobre llega a ``plugin.validate`` como ``payload["operacion"]``.

Regresión de ``consulta_cuit/consultar_masivo``: la central fija el verbo
canónico en el sobre y a propósito no lo repite en el payload, mientras que
los plugins eligen su modelo de entrada leyendo ``payload["operacion"]``.
Sin la inyección del verbo, esa operación (y cualquier otra distinta del
default del plugin) se validaba contra el modelo equivocado y el job
terminaba ``ENVELOPE_INVALID`` sin llegar a ejecutarse.
"""
from __future__ import annotations

import asyncio

from bot_worker.bots.consulta_cuit.plugin import ConsultaCuitPlugin
from bot_worker.bots.consulta_cuit.schema import ConsultaCuitMasivaInput
from bot_worker.bots.registry import REGISTRY
from bot_worker.main import JobEnvelope, validation_payload

_CUITS = ["20123456786", "27123456785"]


def test_payload_de_validacion_incluye_el_verbo_del_sobre():
    """El payload del sobre, sin ``operacion``, se completa con su verbo."""
    env = JobEnvelope(operation="consultar_masivo", payload={"cuits": list(_CUITS)})
    payload = validation_payload(env)
    assert payload["operacion"] == "consultar_masivo"
    assert payload["cuits"] == _CUITS


def test_consulta_cuit_masivo_valida_con_el_payload_del_sobre():
    """``cuits`` se valida con el modelo masivo y no con el individual."""
    env = JobEnvelope(operation="consultar_masivo", payload={"cuits": list(_CUITS)})
    operacion, entrada = asyncio.run(
        ConsultaCuitPlugin().validate(validation_payload(env))
    )
    assert operacion == "consultar_masivo"
    assert isinstance(entrada, ConsultaCuitMasivaInput)
    assert entrada.cuits == _CUITS


def test_el_verbo_del_sobre_manda_sobre_el_cuerpo():
    """Un ``operacion`` enviado en el cuerpo no cambia la del sobre."""
    env = JobEnvelope(
        operation="consultar_masivo",
        payload={"cuits": list(_CUITS), "operacion": "consultar"},
    )
    assert validation_payload(env)["operacion"] == "consultar_masivo"


def test_todo_verbo_del_manifiesto_es_seleccionable_por_payload():
    """Cada verbo admisible en el sobre existe en el ``validate`` del plugin.

    La admisión acepta cualquier verbo del manifiesto; con la inyección del
    verbo ese valor llega al selector de modelo del plugin, que debe
    reconocerlo en lugar de responder ``operacion desconocida``.
    """
    desconocidos: list[str] = []
    for nombre, plugin in REGISTRY.items():
        for verbo in plugin.manifest.operaciones:
            try:
                asyncio.run(plugin.validate({"operacion": verbo}))
            except Exception as exc:  # noqa: BLE001 - solo interesa el selector
                if "operacion desconocida" in str(exc):
                    desconocidos.append(f"{nombre}/{verbo}")
    assert not desconocidos
