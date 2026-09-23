"""Bot ``srt``: alicuotas ART en E-Servicios SRT."""

from bot_worker.bots.srt.plugin import SrtPlugin, es_respuesta_sin_datos
from bot_worker.bots.srt.schema import SrtAlicuotasInput

__all__ = ["SrtAlicuotasInput", "SrtPlugin", "es_respuesta_sin_datos"]
