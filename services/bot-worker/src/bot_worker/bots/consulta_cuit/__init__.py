"""Piloto ``consulta_cuit`` (ola 1): consulta sin navegador."""

from bot_worker.bots.consulta_cuit.plugin import ConsultaCuitPlugin
from bot_worker.bots.consulta_cuit.schema import (
    ConsultaCuitInput,
    ConsultaCuitMasivaInput,
)

__all__ = ["ConsultaCuitInput", "ConsultaCuitMasivaInput", "ConsultaCuitPlugin"]
