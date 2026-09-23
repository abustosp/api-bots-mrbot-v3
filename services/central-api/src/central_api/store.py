"""Almacén en memoria del esqueleto F0-F2.

Fases posteriores lo reemplazan por PostgreSQL (único cliente: esta central,
invariante I-4). La forma ya respeta la regla del esqueleto: de cada worker
SOLO se guarda su dirección ``ip:port`` + estado reportado por HTTP; el
detalle vivo (carga, salud) se inspecciona contra el worker por HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Job:
    id: str
    bot: str
    operation: str
    payload: dict
    credentials: dict = field(default_factory=dict)  # efímeras: nunca se persisten en PG
    status: str = "PENDIENTE"
    worker_node: str | None = None
    assignment_attempt: int = 0
    created_at: datetime = field(default_factory=utcnow)
    result: dict | None = None


@dataclass
class WorkerEntry:
    node: str  # "ip:port", única identidad que la central persiste del worker
    worker_id: str = ""
    sealed_pubkey_pem: str = ""  # RSA pública efímera para el sobre sellado
    status: str = "REGISTRANDO"
    capabilities: list[str] = field(default_factory=list)
    running_jobs: int = 0
    reserved_slots: int = 0
    capacity: int = 5
    last_heartbeat_at: datetime | None = None


JOBS: dict[str, Job] = {}
WORKERS: dict[str, WorkerEntry] = {}  # clave: "ip:port"

# Nodos agregados en caliente desde el panel de administración
# (POST /admin/workers). Vive en memoria en el esqueleto; fases posteriores
# lo persisten en PostgreSQL. Se suma a WORKER_NODES, nunca lo reemplaza.
ADMIN_NODES: set[str] = set()


def new_job_id() -> str:
    """Genera el ID del job en memoria como UUIDv7 (plan 02 §3.3, I-1/I-2).

    Igual que la fila canónica de PostgreSQL (``JobRepository.create``):
    UUID ordenable sin IDs correlativos. Importe diferido para mantener
    este módulo liviano (sin SQLAlchemy en importadores del borde interno).
    """
    from central_api.models.base import new_uuid7

    return str(new_uuid7())
