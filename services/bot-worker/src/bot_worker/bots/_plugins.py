"""Instancias de plugins de la imagen, importadas de forma diferida.

Vive fuera de ``registry.py`` porque el registro importa *todos* los plugins y
los plugins importan la capa de portales; si esas importaciones ocurrieran al
importar el registro, cualquier import que empezara por
``bot_worker.runtime.portals`` (los tests de portal, por ejemplo) cerraria el
ciclo. ``registry.py`` importa este modulo recien en el primer acceso al
registro.
"""

from __future__ import annotations

from bot_worker.bots.apoc.plugin import ApocPlugin
from bot_worker.bots.aportes_en_linea.plugin import (
    AportesEnLineaPlugin,
)
from bot_worker.bots.arba.plugin import ArbaPlugin
from bot_worker.bots.carga_portal_iva.plugin import (
    CargaPortalIvaPlugin,
)
from bot_worker.bots.ccma.plugin import CcmaPlugin
from bot_worker.bots.ccma_compensacion.plugin import (
    CcmaCompensacionPlugin,
)
from bot_worker.bots.certificado_mipyme.plugin import (
    CertificadoMipymePlugin,
)
from bot_worker.bots.compensaciones.plugin import (
    CompensacionesPlugin,
)
from bot_worker.bots.comprobantes.plugin import ComprobantesPlugin
from bot_worker.bots.consulta_cuit.plugin import (
    ConsultaCuitPlugin,
)
from bot_worker.bots.consulta_pagos_vep.plugin import (
    ConsultaPagosVepPlugin,
)
from bot_worker.bots.controladores_fiscales.plugin import (
    ControladoresFiscalesPlugin,
)
from bot_worker.bots.declaracion_en_linea.plugin import (
    DeclaracionEnLineaPlugin,
)
from bot_worker.bots.facturometro.plugin import FacturometroPlugin
from bot_worker.bots.hacienda.plugin import HaciendaPlugin
from bot_worker.bots.libros_portal_iva.plugin import (
    LibrosPortalIvaPlugin,
)
from bot_worker.bots.libro_sueldos_digital.plugin import (
    LibroSueldosDigitalPlugin,
)
from bot_worker.bots.liquidacion_granos.plugin import (
    LiquidacionGranosPlugin,
)
from bot_worker.bots.mis_comprobantes.plugin import (
    MisComprobantesPlugin,
)
from bot_worker.bots.mis_facilidades.plugin import (
    MisFacilidadesPlugin,
)
from bot_worker.bots.mis_retenciones.plugin import (
    MisRetencionesPlugin,
)
from bot_worker.bots.mis_retenciones_iva_simple.plugin import (
    MisRetencionesIvaSimplePlugin,
)
from bot_worker.bots.moa.plugin import MoaPlugin
from bot_worker.bots.pago_devoluciones.plugin import (
    PagoDevolucionesPlugin,
)
from bot_worker.bots.portal_iva.plugin import PortalIvaPlugin
from bot_worker.bots.rcel.plugin import RcelPlugin
from bot_worker.bots.retper_iibb_agip.plugin import (
    RetperIibbAgipPlugin,
)
from bot_worker.bots.retper_iibb_agip.retper_iibb_agip_bot_xvfb import (
    RetperIibbAgipXvfePlugin,
)
from bot_worker.bots.retper_iibb_misiones.plugin import (
    RetperIibbMisionesPlugin,
)
from bot_worker.bots.sct.plugin import SctPlugin
from bot_worker.bots.sifere.plugin import SiferePlugin
from bot_worker.bots.siper.plugin import SiperRealPlugin
from bot_worker.bots.srt.plugin import SrtPlugin
from bot_worker.bots.vep_archivo.plugin import VepArchivoPlugin
from bot_worker.bots.vep_ccma.plugin import VepCcmaPlugin
from bot_worker.bots.registry import BotPlugin

#: Instancias declarativas, una por bot (sin duplicados por nombre).
PLUGINS = (
    ApocPlugin(),  # type: ignore[arg-type]
    AportesEnLineaPlugin(),  # type: ignore[arg-type]
    ArbaPlugin(),  # type: ignore[arg-type]
    CargaPortalIvaPlugin(),  # type: ignore[arg-type]
    CcmaPlugin(),  # type: ignore[arg-type]
    CcmaCompensacionPlugin(),  # type: ignore[arg-type]
    CertificadoMipymePlugin(),  # type: ignore[arg-type]
    CompensacionesPlugin(),  # type: ignore[arg-type]
    ComprobantesPlugin(),  # type: ignore[arg-type]
    ConsultaCuitPlugin(),  # type: ignore[arg-type]
    ConsultaPagosVepPlugin(),  # type: ignore[arg-type]
    ControladoresFiscalesPlugin(),  # type: ignore[arg-type]
    DeclaracionEnLineaPlugin(),  # type: ignore[arg-type]
    FacturometroPlugin(),  # type: ignore[arg-type]
    HaciendaPlugin(),  # type: ignore[arg-type]
    LibrosPortalIvaPlugin(),  # type: ignore[arg-type]
    LibroSueldosDigitalPlugin(),  # type: ignore[arg-type]
    LiquidacionGranosPlugin(),  # type: ignore[arg-type]
    MisComprobantesPlugin(),  # type: ignore[arg-type]
    MisFacilidadesPlugin(),  # type: ignore[arg-type]
    MisRetencionesPlugin(),  # type: ignore[arg-type]
    MisRetencionesIvaSimplePlugin(),  # type: ignore[arg-type]
    MoaPlugin(),  # type: ignore[arg-type]
    PagoDevolucionesPlugin(),  # type: ignore[arg-type]
    PortalIvaPlugin(),  # type: ignore[arg-type]
    RcelPlugin(),  # type: ignore[arg-type]
    RetperIibbAgipPlugin(),  # type: ignore[arg-type]
    RetperIibbMisionesPlugin(),  # type: ignore[arg-type]
    SctPlugin(),  # type: ignore[arg-type]
    SiferePlugin(),  # type: ignore[arg-type]
    SiperRealPlugin(),  # type: ignore[arg-type]
    SrtPlugin(),  # type: ignore[arg-type]
    VepArchivoPlugin(),  # type: ignore[arg-type]
    VepCcmaPlugin(),  # type: ignore[arg-type]
)

#: Variantes por modo de un bot del catálogo (por ejemplo ``<bot>_xvfb``). No son
#: bots nuevos: no entran en ``list_manifests`` ni en la paridad con el catálogo
#: público, y solo se eligen desde ``get_plugin_para_payload``.
VARIANTES = (RetperIibbAgipXvfePlugin(),)  # type: ignore[arg-type]

__all__ = ["PLUGINS", "VARIANTES", "BotPlugin"]
