"""Versiones del worker publicadas en el registry (API HTTP v2 de Docker).

La central consulta ``GET /v2/<repo>/tags/list`` para identificar sola cuál es la última
imagen y qué versiones se pueden aplicar desde el panel. Solo cuenta como versión un tag
de release ``AAAAMMDD_HHMMSS`` (el que genera ``build-and-push.sh``): ``latest`` y otros
tags no son versiones elegibles.

El resultado se cachea ``WORKER_REGISTRY_CACHE_SECONDS``. Si el registry no responde se
conserva la última lista buena y se informa el error: nunca rompe la flota ni el panel.
Está activo solo si se define ``WORKER_IMAGE_REPO``.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urljoin

import httpx

from central_api.settings import get_settings

TAG_VERSION = re.compile(r"^\d{8}_\d{6}$")
TIMEOUT_SEGUNDOS = 4.0
MAX_PAGINAS = 20

#: Transporte inyectable para pruebas (``httpx.MockTransport``); ``None`` = red real.
_TRANSPORT: httpx.BaseTransport | None = None


@dataclass
class EstadoRegistry:
    repo: str = ""
    tags: list[str] = field(default_factory=list)  # versiones, de la más nueva a la más vieja
    consultado: datetime | None = None
    error: str = ""
    _monotonico: float = 0.0


_ESTADO = EstadoRegistry()
_CANDADO = threading.Lock()


def repo() -> str:
    """Repositorio configurado, sin tag ni digest ni esquema."""
    valor = get_settings().worker_image_repo.strip().rstrip("/")
    valor = valor.split("@", 1)[0]
    ultimo = valor.rsplit("/", 1)[-1]
    if ":" in ultimo:
        valor = valor[: -(len(ultimo) - ultimo.index(":"))]
    return valor.removeprefix("https://").removeprefix("http://")


def habilitado() -> bool:
    return bool(repo())


def _url_tags(repositorio: str) -> str:
    host, _, ruta = repositorio.partition("/")
    if not ruta:
        raise ValueError("WORKER_IMAGE_REPO debe incluir host y ruta (host/ruta/imagen)")
    return f"https://{host}/v2/{ruta}/tags/list?n=500"


def _consultar(repositorio: str) -> list[str]:
    ajustes = get_settings()
    auth = (ajustes.registry_user, ajustes.registry_password) if ajustes.registry_user else None
    url = _url_tags(repositorio)
    tags: set[str] = set()
    with httpx.Client(
        timeout=TIMEOUT_SEGUNDOS, verify=ajustes.worker_registry_verify_tls,
        transport=_TRANSPORT, auth=auth,
    ) as cliente:
        for _ in range(MAX_PAGINAS):
            respuesta = cliente.get(url)
            respuesta.raise_for_status()
            tags.update(str(t) for t in (respuesta.json().get("tags") or []))
            siguiente = respuesta.links.get("next", {}).get("url")
            if not siguiente:
                break
            url = urljoin(url, siguiente)
    return sorted((t for t in tags if TAG_VERSION.match(t)), reverse=True)


def refrescar(force: bool = False) -> EstadoRegistry:
    """Actualiza la lista de versiones si está vencida (o con ``force``)."""
    if not habilitado():
        return _ESTADO
    ajustes = get_settings()
    with _CANDADO:
        vigente = (
            _ESTADO.consultado is not None
            and _ESTADO.repo == repo()
            and time.monotonic() - _ESTADO._monotonico < ajustes.worker_registry_cache_seconds
        )
        if vigente and not force:
            return _ESTADO
        try:
            tags = _consultar(repo())
        except (httpx.HTTPError, ValueError) as exc:
            # No se filtra la URL ni credenciales: solo la clase del error.
            _ESTADO.error = f"registry no disponible ({type(exc).__name__})"
            _ESTADO.repo = repo()
            _ESTADO._monotonico = time.monotonic()  # no martillar un registry caído
            return _ESTADO
        _ESTADO.repo = repo()
        _ESTADO.tags = tags
        _ESTADO.error = ""
        _ESTADO.consultado = datetime.now(timezone.utc)
        _ESTADO._monotonico = time.monotonic()
        return _ESTADO


def versiones() -> list[str]:
    """Versiones conocidas, de la más nueva a la más vieja (sin consultar la red)."""
    return list(_ESTADO.tags) if habilitado() and _ESTADO.repo == repo() else []


def latest() -> str | None:
    lista = versiones()
    return lista[0] if lista else None


def reiniciar() -> None:
    """Descarta la caché (pruebas)."""
    global _ESTADO
    _ESTADO = EstadoRegistry()
