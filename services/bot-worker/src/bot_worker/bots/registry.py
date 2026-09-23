"""Registro de plugins de la imagen (plan 03-worker S7, plan 07 F3).

El manifiesto de /internal/v1/bots y la seleccion en la ejecucion se
generan desde aqui, no de listas paralelas. Cada bot expone un
``BotManifest`` declarativo y el contrato ``BotPlugin`` (manifiesto +
``validate`` + ``execute``) en vez del executor V2
``execute(request_data, job_id, user_id, db)``.

Todos los bots con directorio propio en ``bots/`` estan registrados
aqui por su plugin real (los pilotos F2 quedaron fusionados: no hay
stubs en linea ni entradas duplicadas).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Mapping, Protocol

from mrbot_contracts.version import PROTOCOL_VERSION as CONTRACT_PROTOCOL_VERSION

if TYPE_CHECKING:
    from bot_worker.runtime.context import BotResult, BotRuntime

# Alias explícito para la prueba stdlib-only y guardia de build. La fuente
# normativa sigue siendo mrbot-contracts.
PROTOCOL_VERSION: int = 1
if PROTOCOL_VERSION != CONTRACT_PROTOCOL_VERSION:  # pragma: no cover - guardia de build
    raise RuntimeError("bot-worker: versión de protocolo desalineada")


@dataclass(frozen=True)
class ArtifactSpec:
    """Artefacto logico que un bot puede producir."""

    nombre: str
    content_types: tuple[str, ...]
    max_bytes: int
    obligatorio: bool


@dataclass(frozen=True)
class BotManifest:
    """Manifiesto declarativo e inmutable de un plugin (S7.1)."""

    nombre: str
    version: str
    operaciones: tuple[str, ...]
    esquema_entrada: dict[str, Any]
    artefactos_produce: tuple[ArtifactSpec, ...]
    timeout_por_defecto_seconds: int
    requiere_credenciales_fiscales: bool
    requiere_proxy: bool
    requiere_captcha: bool
    costo_creditos_sugerido: int
    idempotency_class: Literal["LECTURA", "CONTINUACION", "CARGA", "EFECTO"]
    browser_instances_max: int
    hosts_permitidos: tuple[str, ...]

    def to_status_dict(self) -> dict[str, Any]:
        """Proyeccion para /internal/v1/bots y registro en la central."""
        return {
            "nombre": self.nombre,
            "version": self.version,
            "operaciones": list(self.operaciones),
            "timeout_por_defecto_seconds": self.timeout_por_defecto_seconds,
            "requiere_credenciales_fiscales": self.requiere_credenciales_fiscales,
            "requiere_proxy": self.requiere_proxy,
            "requiere_captcha": self.requiere_captcha,
            "costo_creditos_sugerido": self.costo_creditos_sugerido,
        }


class BotPlugin(Protocol):
    """Contrato que todo bot productivo implementa (S7.2-S7.3)."""

    manifest: BotManifest

    async def validate(self, payload: Mapping[str, Any]) -> Any: ...
    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult: ...


from bot_worker.bots.apoc.plugin import ApocPlugin  # noqa: E402
from bot_worker.bots.aportes_en_linea.plugin import (  # noqa: E402
    AportesEnLineaPlugin,
)
from bot_worker.bots.arba.plugin import ArbaPlugin  # noqa: E402
from bot_worker.bots.carga_portal_iva.plugin import (  # noqa: E402
    CargaPortalIvaPlugin,
)
from bot_worker.bots.ccma.plugin import CcmaPlugin  # noqa: E402
from bot_worker.bots.certificado_mipyme.plugin import (  # noqa: E402
    CertificadoMipymePlugin,
)
from bot_worker.bots.compensaciones.plugin import (  # noqa: E402
    CompensacionesPlugin,
)
from bot_worker.bots.comprobantes.plugin import ComprobantesPlugin  # noqa: E402
from bot_worker.bots.consulta_cuit.plugin import (  # noqa: E402
    ConsultaCuitPlugin,
)
from bot_worker.bots.consulta_pagos_vep.plugin import (  # noqa: E402
    ConsultaPagosVepPlugin,
)
from bot_worker.bots.controladores_fiscales.plugin import (  # noqa: E402
    ControladoresFiscalesPlugin,
)
from bot_worker.bots.declaracion_en_linea.plugin import (  # noqa: E402
    DeclaracionEnLineaPlugin,
)
from bot_worker.bots.facturometro.plugin import FacturometroPlugin  # noqa: E402
from bot_worker.bots.hacienda.plugin import HaciendaPlugin  # noqa: E402
from bot_worker.bots.libros_portal_iva.plugin import (  # noqa: E402
    LibrosPortalIvaPlugin,
)
from bot_worker.bots.liquidacion_granos.plugin import (  # noqa: E402
    LiquidacionGranosPlugin,
)
from bot_worker.bots.mis_comprobantes.plugin import (  # noqa: E402
    MisComprobantesPlugin,
)
from bot_worker.bots.mis_facilidades.plugin import (  # noqa: E402
    MisFacilidadesPlugin,
)
from bot_worker.bots.mis_retenciones.plugin import (  # noqa: E402
    MisRetencionesPlugin,
)
from bot_worker.bots.mis_retenciones_iva_simple.plugin import (  # noqa: E402
    MisRetencionesIvaSimplePlugin,
)
from bot_worker.bots.moa.plugin import MoaPlugin  # noqa: E402
from bot_worker.bots.pago_devoluciones.plugin import (  # noqa: E402
    PagoDevolucionesPlugin,
)
from bot_worker.bots.portal_iva.plugin import PortalIvaPlugin  # noqa: E402
from bot_worker.bots.rcel.plugin import RcelPlugin  # noqa: E402
from bot_worker.bots.retper_iibb_agip.plugin import (  # noqa: E402
    RetperIibbAgipPlugin,
)
from bot_worker.bots.retper_iibb_misiones.plugin import (  # noqa: E402
    RetperIibbMisionesPlugin,
)
from bot_worker.bots.sct.plugin import SctPlugin  # noqa: E402
from bot_worker.bots.sifere.plugin import SiferePlugin  # noqa: E402
from bot_worker.bots.siper.plugin import SiperRealPlugin  # noqa: E402
from bot_worker.bots.srt.plugin import SrtPlugin  # noqa: E402
from bot_worker.bots.vep_archivo.plugin import VepArchivoPlugin  # noqa: E402
from bot_worker.bots.vep_ccma.plugin import VepCcmaPlugin  # noqa: E402

#: Instancias declarativas, una por bot (sin duplicados por nombre).
_PLUGINS: tuple[BotPlugin, ...] = (
    ApocPlugin(),  # type: ignore[arg-type]
    AportesEnLineaPlugin(),  # type: ignore[arg-type]
    ArbaPlugin(),  # type: ignore[arg-type]
    CargaPortalIvaPlugin(),  # type: ignore[arg-type]
    CcmaPlugin(),  # type: ignore[arg-type]
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


def _build_registry(plugins: tuple[BotPlugin, ...]) -> dict[str, BotPlugin]:
    """Indexa por nombre canonico; un duplicado es error, no fusion."""
    registry: dict[str, BotPlugin] = {}
    for plugin in plugins:
        nombre = plugin.manifest.nombre  # type: ignore[union-attr]
        if nombre in registry:
            raise ValueError(f"bot duplicado en registry: {nombre!r}")
        registry[nombre] = plugin
    return registry


REGISTRY: dict[str, BotPlugin] = _build_registry(_PLUGINS)


def get_plugin(nombre: str) -> BotPlugin | None:
    """Devuelve el plugin por nombre canonico o ``None`` si no existe."""
    return REGISTRY.get(nombre)


def list_manifests() -> list[BotManifest]:
    """Manifiestos de la imagen para /internal/v1/bots y el registro."""
    return [plugin.manifest for plugin in REGISTRY.values()]  # type: ignore[union-attr]


def manifest_hash() -> str:
    """Hash corto de los manifiestos para heartbeat y diagnostico."""
    import hashlib
    import json

    manifests = [
        p.manifest.to_status_dict() for p in REGISTRY.values()  # type: ignore[union-attr]
    ]
    return "sha256:" + hashlib.sha256(
        json.dumps(manifests, sort_keys=True).encode()
    ).hexdigest()[:16]
