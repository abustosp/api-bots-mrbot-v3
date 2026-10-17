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

import importlib
from dataclasses import dataclass
from types import ModuleType
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
    #: Argumentos de lanzamiento e init script que reducen las señales de
    #: automatización (portado de la V1). No cambia la identidad declarada por
    #: el navegador: sigue siendo headless.
    stealth: bool = False
    #: Canal de Playwright a usar (por ejemplo ``chromium`` para forzar el
    #: binario completo en lugar del shell recortado que se elige por defecto en
    #: headless). Vacío significa el comportamiento por defecto.
    canal_navegador: str = ""
    #: Corre el navegador con pantalla virtual (Xvfb): la fábrica levanta un
    #: display si hace falta y lanza Chromium con interfaz. Apagado por defecto;
    #: lo activa la variante ``_xvfb`` del bot cuando el pedido trae ``pv``.
    pantalla_virtual: bool = False

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


def _build_registry(plugins: tuple[BotPlugin, ...]) -> dict[str, BotPlugin]:
    """Indexa por nombre canonico; un duplicado es error, no fusion."""
    registry: dict[str, BotPlugin] = {}
    for plugin in plugins:
        nombre = plugin.manifest.nombre  # type: ignore[union-attr]
        if nombre in registry:
            raise ValueError(f"bot duplicado en registry: {nombre!r}")
        registry[nombre] = plugin
    return registry


def _cargar_plugins() -> ModuleType:
    """Importa ``bots/_plugins`` en el primer uso (evita el ciclo con portales).

    El modulo de instancias importa todos los plugins y los plugins importan
    la capa de portales. Hacerlo al importar este archivo cerraba el ciclo
    ``portals.base`` -> ``bots`` -> ``registry`` -> plugins -> ``portals``, asi
    que cualquier import que empezara por ``bot_worker.runtime.portals``
    fallaba con un ImportError de importacion parcial.
    """
    return importlib.import_module("bot_worker.bots._plugins")


_REGISTRY: dict[str, BotPlugin] | None = None
_VARIANTES: dict[str, BotPlugin] | None = None


def registro() -> dict[str, BotPlugin]:
    """Registro de bots de la imagen, construido en el primer acceso."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry(_cargar_plugins().PLUGINS)
    return _REGISTRY


def variantes() -> dict[str, BotPlugin]:
    """Variantes por modo (por ejemplo ``<bot>_xvfb``), en el primer acceso."""
    global _VARIANTES
    if _VARIANTES is None:
        _VARIANTES = _build_registry(_cargar_plugins().VARIANTES)
    return _VARIANTES


def __getattr__(name: str) -> Any:
    """Mantiene ``REGISTRY``/``VARIANTES`` como atributos del modulo."""
    if name == "REGISTRY":
        return registro()
    if name == "VARIANTES":
        return variantes()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_plugin(nombre: str) -> BotPlugin | None:
    """Devuelve el plugin por nombre canónico, o la variante si es su nombre."""
    return registro().get(nombre) or variantes().get(nombre)


def pide_pantalla_virtual(payload: Any) -> bool:
    """True si el pedido activa la variante con Xvfb mediante ``pv``.

    Acepta solo valores explícitos de verdad: ausente, ``None`` y ``False``
    mantienen el camino de siempre, para no cambiarle el comportamiento a los
    clientes que no mandan el atributo.
    """
    if not isinstance(payload, Mapping):
        return False
    valor = payload.get("pv")
    if isinstance(valor, str):
        return valor.strip().lower() in {"1", "true", "si", "yes", "on"}
    return bool(valor)


def get_plugin_para_payload(nombre: str, payload: Any) -> BotPlugin | None:
    """Plugin del job: variante ``<bot>_xvfb`` cuando el pedido pide Xvfb.

    La convención es de plataforma: cualquier bot que registre un plugin
    ``<bot>_xvfb`` obtiene la alternativa con pantalla virtual cuando el pedido
    trae ``pv`` en verdadero. Si la variante no está registrada, se usa el bot
    normal y nada cambia.
    """
    if pide_pantalla_virtual(payload):
        variante = variantes().get(f"{nombre}_xvfb")
        if variante is not None:
            return variante
    return registro().get(nombre)


def list_manifests() -> list[BotManifest]:
    """Manifiestos de la imagen para /internal/v1/bots y el registro."""
    return [p.manifest for p in registro().values()]  # type: ignore[union-attr]


def manifest_hash() -> str:
    """Hash corto de los manifiestos para heartbeat y diagnostico."""
    import hashlib
    import json

    manifests = [
        p.manifest.to_status_dict() for p in registro().values()  # type: ignore[union-attr]
    ]
    return "sha256:" + hashlib.sha256(
        json.dumps(manifests, sort_keys=True).encode()
    ).hexdigest()[:16]
