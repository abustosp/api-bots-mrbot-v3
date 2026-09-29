"""Variante del bot ``retper_iibb_agip`` con pantalla virtual (Xvfb).

El perímetro de AGIP trata distinto al navegador sin display: medido el
29/09/2026, headless falla por momentos en el hop
``lb.agip.gob.ar/gestionArciba/cc/redir`` (página de bloqueo o timeout) mientras
que con una pantalla el flujo completo consulta y descarga. Esta variante corre
**el mismo flujo** que ``plugin.py`` (mismos selectores, mismo parseo, mismos
artefactos) y lo único que cambia es el modo del navegador: la fábrica levanta un
Xvfb si hace falta y lanza Chromium con interfaz.

Se elige solo cuando el pedido trae ``vp`` en verdadero
(``bots.registry.get_plugin_para_payload``); con el atributo ausente, ``null`` o
``false`` el job sigue por el camino de siempre.
"""

from __future__ import annotations

from dataclasses import replace

from bot_worker.bots.retper_iibb_agip.plugin import RetperIibbAgipPlugin

#: Nombre canónico de esta variante (``<bot>_xvfb``).
NOMBRE_VARIANTE = "retper_iibb_agip_xvfb"


class RetperIibbAgipXvfePlugin(RetperIibbAgipPlugin):
    """Mismo bot AGIP, con el navegador en una pantalla virtual."""

    manifest = replace(
        RetperIibbAgipPlugin.manifest,
        nombre=NOMBRE_VARIANTE,
        pantalla_virtual=True,
    )


__all__ = ["NOMBRE_VARIANTE", "RetperIibbAgipXvfePlugin"]
