"""Contexto de ejecucion inyectado a los plugins.

Los efectos laterales viven aqui; los plugins nunca reciben el request
HTTP, la configuracion global, un ORM ni el cliente de la central.
"""

from __future__ import annotations

import asyncio
import mimetypes
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol


@dataclass(repr=False)
class FiscalCredentials:
    """Bundle efimero: solo memoria del job, sin serializacion JSON."""

    cuit_representante: str
    clave: str

    def __repr__(self) -> str:  # noqa: D105
        return "FiscalCredentials(<redacted>)"


@dataclass(repr=False)
class ProxyConfig:
    mode: str
    host: str | None = None
    username: str | None = None
    password: str | None = None
    country: str = "ar"

    def __repr__(self) -> str:  # noqa: D105
        return f"ProxyConfig(mode={self.mode!r}, host=<redacted>)"


@dataclass
class DeadlineBudget:
    deadline_epoch: float

    def remaining_seconds(self, maximum: float | None = None) -> float:
        import time

        remaining = max(0.0, self.deadline_epoch - time.time())
        return min(remaining, maximum) if maximum is not None else remaining

    def remaining_ms(self, maximum: int | None = None) -> int:
        return int(self.remaining_seconds(maximum) * 1000)


class CancellationToken:
    """Disparado por cancelacion cooperativa, deadline o SIGTERM."""

    def __init__(self) -> None:
        self._event = asyncio.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    async def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise JobCancelled("cancelacion solicitada")


class JobCancelled(Exception):
    """El plugin detecto cancelacion: el supervisor informa el estado."""


@dataclass(frozen=True)
class BotError:
    category: str
    internal_diagnostic: str
    retryable: bool


@dataclass
class BotResult:
    result: Literal["OK", "PARCIAL", "ERROR"]
    data: dict[str, Any] = field(default_factory=dict)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[BotError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, int | float | str | bool] = field(default_factory=dict)


@dataclass
class ArtifactSlot:
    artifact_id: str
    put_url: str
    object_key: str
    max_bytes: int = 52_428_800
    content_types: tuple[str, ...] = ("application/octet-stream",)


def _contenido_tipo(
    nombre: str, declarados: tuple[str, ...] | list[str] | None
) -> str:
    """MIME para el presign: primero el declarado en el slot o manifiesto."""
    if declarados and tuple(declarados) != ("application/octet-stream",):
        return str(declarados[0])
    estimado, _ = mimetypes.guess_type(nombre)
    return estimado or "application/octet-stream"


def _clave_artefacto(nombre: str) -> str:
    """Normaliza el nombre de un artefacto para compararlo entre capas.

    Los manifiestos declaran nombres legibles (``emitidos.csv``) y los plugins
    suben con identificadores (``emitidos_csv``): la comparación ignora esa
    diferencia de separador.
    """
    return str(nombre).strip().lower().replace(".", "_")


def _declarado_por_el_manifiesto(
    artifact_id: str, declarados: Mapping[str, tuple[tuple[str, ...], int]]
) -> tuple[tuple[str, ...], int] | None:
    """Límites del artefacto si el manifiesto del plugin lo declara.

    Acepta coincidencia normalizada y, para nombres sin extensión
    (``sct_reporte``), los identificadores derivados con sufijo
    (``sct_reporte_deudas_csv``).
    """
    clave = _clave_artefacto(artifact_id)
    for nombre, limites in declarados.items():
        normalizado = _clave_artefacto(nombre)
        if clave == normalizado:
            return limites
        if "." not in str(nombre) and clave.startswith(f"{normalizado}_"):
            return limites
    return None


class ArtifactStore:
    """Solo sube a slots prefirmados del sobre; sin credenciales de bucket."""

    def __init__(
        self,
        work_dir: Path,
        slots: Mapping[str, ArtifactSlot],
        presign: Callable[[str, str, int], Awaitable[dict[str, Any]]] | None = None,
        http_client: Any = None,
        declarados: Mapping[str, tuple[tuple[str, ...], int]] | None = None,
    ) -> None:
        self._work_dir = work_dir.resolve()
        self._slots = dict(slots)
        # ``presign`` renueva la URL cuando el slot no trae una (artefacto
        # adicional o URL vencida): recibe (artifact_id, content_type,
        # size_bytes) y devuelve al menos {upload_url, object_key}.
        self._presign = presign
        self._http_client = http_client
        # Catálogo del manifiesto del plugin: habilita el presign bajo demanda
        # sin depender de que el sobre enumere cada artefacto por bot.
        self._declarados = dict(declarados or {})

    def resolve(self, rel: str) -> Path:
        candidate = (self._work_dir / rel).resolve()
        if candidate != self._work_dir and self._work_dir not in candidate.parents:
            raise ValueError("ruta fuera de work_dir")
        if candidate.is_symlink():
            raise ValueError("symlinks no permitidos")
        return candidate

    async def upload(
        self, artifact_id: str, rel_path: str, client: Any = None
    ) -> dict[str, Any]:
        import hashlib

        slot = self._slots.get(artifact_id)
        if slot is None:
            limites = _declarado_por_el_manifiesto(artifact_id, self._declarados)
            if limites is None:
                raise ValueError(f"artifact_id no autorizado: {artifact_id}")
            _, max_bytes = limites
            # Sin tipos declarados: el MIME se estima por la extensión real del
            # archivo, que es la que la central firma en el presign.
            slot = ArtifactSlot(
                artifact_id=artifact_id,
                put_url="",
                object_key="",
                max_bytes=max_bytes,
            )
            self._slots[artifact_id] = slot
        path = self.resolve(rel_path)
        if not path.is_file() or path.is_symlink():
            raise ValueError("artefacto no es archivo regular de work_dir")
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                digest.update(chunk)
                size += len(chunk)
        if size > slot.max_bytes:
            raise ValueError("artefacto excede max_bytes del slot")
        content_type = _contenido_tipo(
            path.name, getattr(slot, "content_types", None)
        )
        if not getattr(slot, "put_url", "") and self._presign is not None:
            # Slot sin URL: se pide una prefirmada a la central antes de subir.
            renovado = await self._presign(artifact_id, content_type, size)
            if renovado.get("upload_url"):
                slot.put_url = renovado["upload_url"]
            if renovado.get("object_key"):
                slot.object_key = renovado["object_key"]
        http = client if client is not None else self._http_client
        if http is not None and getattr(slot, "put_url", ""):
            # PUT a la URL prefirmada, sin firmas propias ni claves de bucket.
            with open(path, "rb") as fh:
                await http.put(
                    slot.put_url,
                    content=fh.read(),
                    headers={"Content-Type": content_type},
                )
        return {
            "artifact_id": artifact_id,
            "object_key": slot.object_key,
            "name": path.name,
            "size_bytes": size,
            "content_type": content_type,
            "sha256": digest.hexdigest(),
        }


class EventSink(Protocol):
    async def progress(
        self, phase: str, percent: int, message: str
    ) -> None: ...


class BrowserFactory(Protocol):
    def arca_session(
        self, credentials: FiscalCredentials | None, **kwargs: Any
    ) -> Any: ...


@dataclass
class BotRuntime:
    job_id: str
    work_dir: Path
    deadline: DeadlineBudget
    credentials: FiscalCredentials | None
    proxy: ProxyConfig | None
    artifact_store: ArtifactStore
    event_sink: EventSink
    browser_factory: BrowserFactory
    cancellation: CancellationToken
