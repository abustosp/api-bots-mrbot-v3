"""Actualización remota de workers manuales (botón "Actualizar" del panel).

El worker corre sin docker socket ni entorno (W-1, W-3, SEC-3), así que no
puede actualizarse solo. Cada host manual corre además un *updater*
(``services/worker-updater``) que consulta a la central (solo conexiones
salientes) y recrea el contenedor con la imagen objetivo.

Ciclo de una solicitud por nodo::

    ENCOLADA -> DRENANDO -> ACTUALIZANDO -> HECHO | FALLIDO | REVERTIDO

- ``ENCOLADA``: esperando cupo (``WORKER_UPDATE_MAX_PARALLEL``) para no
  dejar la flota sin capacidad durante una actualización masiva.
- ``DRENANDO``: el nodo se pausa (``PAUSED_NODES``, no recibe jobs nuevos) y
  se espera que termine lo que está en vuelo, hasta ``WORKER_UPDATE_DRAIN_SECONDS``.
- ``ACTUALIZANDO``: el updater recibió el comando y está recreando el contenedor.

El estado vive en memoria, como las alertas de flota: son operaciones de
minutos. Si la central se reinicia a mitad de camino, el updater termina
igual y la versión nueva se ve por heartbeat; lo único que se pierde es el
historial de la solicitud.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from central_api.settings import get_settings
from central_api.store import JOBS, PAUSED_NODES, WORKERS, utcnow

ACTIVOS = ("ENCOLADA", "DRENANDO", "ACTUALIZANDO")
TERMINALES = ("HECHO", "FALLIDO", "REVERTIDO")
ESTADOS_REPORTABLES = ("HECHO", "FALLIDO", "REVERTIDO")


@dataclass
class UpdateRequest:
    id: str
    node: str
    image: str
    app_version: str
    image_digest: str
    requested_by: str
    motivo: str
    status: str = "ENCOLADA"
    created_at: datetime = field(default_factory=utcnow)
    drain_started_at: datetime | None = None
    command_sent_at: datetime | None = None
    finished_at: datetime | None = None
    error: str = ""
    # Solo se reanuda al terminar si la pausa la puso esta solicitud.
    paused_by_update: bool = False

    def public(self) -> dict:
        def iso(valor: datetime | None) -> str | None:
            return valor.isoformat() if valor else None

        return {
            "id": self.id,
            "node": self.node,
            "estado": self.status,
            "imagen": self.image,
            "version": self.app_version or None,
            "digest": self.image_digest or None,
            "solicitado_por": self.requested_by,
            "motivo": self.motivo,
            "creada": iso(self.created_at),
            "comando_enviado": iso(self.command_sent_at),
            "finalizada": iso(self.finished_at),
            "error": self.error or None,
        }


UPDATE_REQUESTS: dict[str, UpdateRequest] = {}  # clave: nodo; la última solicitud


def solicitud_activa(node: str) -> UpdateRequest | None:
    """Solicitud vigente del nodo, tras reconciliarla con la realidad.

    Una solicitud deja de estar activa sola cuando (a) el worker ya reporta la versión
    objetivo (p. ej. se actualizó a mano) o (b) nadie la tomó en
    ``WORKER_UPDATE_TIMEOUT_SECONDS`` (no hay updater en el host): así el panel no queda
    en "Actualizando" indefinidamente.
    """
    req = UPDATE_REQUESTS.get(node)
    if req is None or req.status not in ACTIVOS:
        return None
    entrada = WORKERS.get(node)
    version = str(getattr(entrada, "app_version", "") or "").strip()
    if req.app_version and version == req.app_version:
        _cerrar(req, "HECHO")
        return None
    if req.status in ("ENCOLADA", "DRENANDO"):
        limite = timedelta(seconds=get_settings().worker_update_timeout_seconds)
        if utcnow() - req.created_at > limite:
            _cerrar(
                req, "FALLIDO",
                "ningún updater tomó la solicitud: verifique que el host tenga el worker-updater",
            )
            return None
    return req


class VersionNoDisponible(ValueError):
    """La versión pedida no se puede aplicar (sin registry, no publicada o inválida)."""


def objetivo(version: str | None = None) -> tuple[str, str, str]:
    """(imagen, app_version, digest) objetivo; imagen vacía si no hay configurada.

    Con ``WORKER_IMAGE_REPO`` la imagen es ``<repo>:<versión>`` (la última del registry o
    la ``version`` pedida, validada contra los tags publicados). Sin repo se usa
    ``WORKER_IMAGE_REF`` como antes, y no admite elegir versión.
    """
    from central_api import registry
    from central_api.admin.fleet import _latest_version

    settings = get_settings()
    if version is not None:
        if not registry.habilitado():
            raise VersionNoDisponible("elegir versión requiere WORKER_IMAGE_REPO")
        if not registry.TAG_VERSION.match(version):
            raise VersionNoDisponible("la versión debe tener el formato AAAAMMDD_HHMMSS")
        publicadas = registry.versiones()
        if not publicadas:
            raise VersionNoDisponible("no hay lista de versiones del registry disponible")
        if version not in publicadas:
            raise VersionNoDisponible(f"la versión {version} no está publicada en el registry")
        return f"{registry.repo()}:{version}", version, ""
    if registry.habilitado():
        ultima = registry.latest()
        if ultima:
            return f"{registry.repo()}:{ultima}", ultima, ""
    image = settings.worker_image_ref.strip()
    if not image:
        return "", "", ""
    _, digest, app_version = _latest_version()
    if not app_version:
        # Sin WORKER_LATEST_VERSION se deduce del tag de la imagen.
        nombre = image.split("@", 1)[0].rsplit("/", 1)[-1]
        app_version = nombre.split(":", 1)[1] if ":" in nombre else ""
    if not digest and "@" in image:
        digest = image.split("@", 1)[1]
    return image, app_version or "", digest or ""


def _en_vuelo(node: str) -> int:
    entrada = WORKERS.get(node)
    corriendo = entrada.running_jobs + entrada.reserved_slots if entrada else 0
    asignados = sum(
        1 for j in JOBS.values()
        if j.worker_node == node and j.status in ("ASIGNADO", "CORRIENDO")
    )
    return max(corriendo, asignados)


def _cerrar(req: UpdateRequest, estado: str, error: str = "") -> None:
    req.status = estado
    req.error = error
    req.finished_at = utcnow()
    if req.paused_by_update:
        PAUSED_NODES.discard(req.node)
        req.paused_by_update = False


def _vencer_atascadas(ahora: datetime) -> None:
    limite = timedelta(seconds=get_settings().worker_update_timeout_seconds)
    for req in UPDATE_REQUESTS.values():
        if req.status == "ACTUALIZANDO" and req.command_sent_at and ahora - req.command_sent_at > limite:
            _cerrar(req, "FALLIDO", "el updater no reportó el resultado a tiempo")


def crear_solicitud(
    node: str, *, solicitud_id: str, actor: str, motivo: str, version: str | None = None
) -> UpdateRequest:
    image, app_version, digest = objetivo(version)
    req = UpdateRequest(
        id=solicitud_id, node=node, image=image, app_version=app_version,
        image_digest=digest, requested_by=actor, motivo=motivo,
    )
    UPDATE_REQUESTS[node] = req
    return req


def _cupo_libre() -> bool:
    en_curso = sum(1 for r in UPDATE_REQUESTS.values() if r.status in ("DRENANDO", "ACTUALIZANDO"))
    return en_curso < get_settings().worker_update_max_parallel


def comando_para(node: str) -> dict:
    """Respuesta al polling del updater: avanza la máquina de estados del nodo."""
    ahora = utcnow()
    _vencer_atascadas(ahora)
    req = solicitud_activa(node)
    if req is None:
        return {"action": "none"}
    if req.status == "ENCOLADA":
        if not _cupo_libre():
            return {"action": "none", "estado": req.status}
        req.status = "DRENANDO"
        req.drain_started_at = ahora
        if node not in PAUSED_NODES:
            PAUSED_NODES.add(node)
            req.paused_by_update = True
    if req.status == "DRENANDO":
        espera = ahora - (req.drain_started_at or ahora)
        if _en_vuelo(node) > 0 and espera < timedelta(seconds=get_settings().worker_update_drain_seconds):
            return {"action": "none", "estado": req.status}
        req.status = "ACTUALIZANDO"
        req.command_sent_at = ahora
    # ACTUALIZANDO: se reenvía el mismo comando (idempotente) si el updater reinició.
    return {
        "action": "update",
        "request_id": req.id,
        "image": req.image,
        "app_version": req.app_version,
        "image_digest": req.image_digest,
    }


def registrar_resultado(node: str, request_id: str, estado: str, error: str) -> UpdateRequest | None:
    req = UPDATE_REQUESTS.get(node)
    if req is None or req.id != request_id or req.status not in ACTIVOS:
        return None
    _cerrar(req, estado, error[:500])
    return req


def cancelar_solicitud(node: str, actor: str) -> UpdateRequest | None:
    """Cancela la solicitud activa del nodo (nunca interrumpe un ``ACTUALIZANDO`` ya enviado)."""
    req = solicitud_activa(node)
    if req is None or req.status == "ACTUALIZANDO":
        return None
    _cerrar(req, "FALLIDO", f"cancelada por {actor}")
    return req
