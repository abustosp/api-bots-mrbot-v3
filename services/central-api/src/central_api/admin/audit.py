"""Auditoría append-only del panel (``audit_log`` en memoria del esqueleto).

Cada acción administrativa sensible registra quién, qué, cuándo, sobre qué,
desde dónde y el resultado. La lista es solo de agregado: no existe ninguna
ruta de UPDATE ni DELETE. Fases posteriores la persisten en PostgreSQL con
una cuenta sin permisos de UPDATE/DELETE.
"""

from __future__ import annotations

import random
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from fastapi import APIRouter, Header

from central_api.admin._common import redactar_metadata, require_admin
from central_api.settings import get_settings
from central_api.store import utcnow

router = APIRouter()


def nuevo_uuid7() -> str:
    """Genera un UUIDv7 (tiempo ms + aleatorio) sin dependencias externas."""
    ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand_a = random.getrandbits(12)
    rand_b = random.getrandbits(62)
    valor = (ms << 80) | (7 << 76) | (rand_a << 64) | (2 << 60) | rand_b
    hex_ = f"{valor:032x}"
    return f"{hex_[:8]}-{hex_[8:12]}-{hex_[12:16]}-{hex_[16:20]}-{hex_[20:]}"


@dataclass
class AuditEvent:
    """Evento inmutable de auditoría administrativa."""

    id: str
    occurred_at: str
    actor_type: str
    actor_id: str
    action: str
    target_type: str = ""
    target_id: str = ""
    request_id: str = ""
    remote_addr: str = ""
    user_agent: str = ""
    result: str = "success"
    reason: str = ""
    metadata_redacted: dict[str, Any] = field(default_factory=dict)


AUDIT_LOG: list[AuditEvent] = []


def log_event(
    action: str,
    *,
    actor_id: str = "admin:token",
    target_type: str = "",
    target_id: str = "",
    request_id: str = "",
    result: str = "success",
    reason: str = "",
    metadata: dict[str, Any] | None = None,
    actor_type: str = "admin_user",
) -> AuditEvent:
    """Agrega un evento con metadatos ya redactados; nunca edita ni borra."""
    evento = AuditEvent(
        id=nuevo_uuid7(),
        occurred_at=utcnow().isoformat(),
        actor_type=actor_type,
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=str(target_id or ""),
        request_id=request_id or "",
        result=result,
        reason=reason or "",
        metadata_redacted=dict(redactar_metadata(metadata or {})),
    )
    AUDIT_LOG.append(evento)
    return evento


@router.get("/audit")
def listar_auditoria(
    authorization: str | None = Header(default=None),
    accion: str = "",
    actor: str = "",
    objetivo: str = "",
    resultado: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Lista eventos de auditoría con filtros y paginación por offset."""
    require_admin(authorization)
    top = max(1, min(limit, get_settings().admin_audit_page_size * 4))
    eventos = list(AUDIT_LOG)
    if accion:
        eventos = [e for e in eventos if e.action.startswith(accion)]
    if actor:
        eventos = [e for e in eventos if actor in e.actor_id]
    if objetivo:
        eventos = [e for e in eventos if objetivo in f"{e.target_type}:{e.target_id}"]
    if resultado:
        eventos = [e for e in eventos if e.result.lower() == resultado.lower()]
    total = len(eventos)
    pagina = eventos[max(0, offset) : max(0, offset) + top]
    return {"success": True, "total": total, "eventos": [asdict(e) for e in pagina]}
