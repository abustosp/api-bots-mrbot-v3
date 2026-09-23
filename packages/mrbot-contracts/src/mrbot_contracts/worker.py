"""Registro y latido del worker, worker -> central (plan 00, S5.6 y S5.7).

El `worker_id` es UUIDv7 efimero de proceso: un reinicio genera uno nuevo
aunque conserve el nombre logico. La capacidad declarada cumple
`0 <= running <= total <= 5`. El registro unificado (`NodeRegistration`)
acepta la union de nodos ejecutores (`WORKER_NODES`) y nodos
administrativos (`ADMIN_NODES`), discriminada por `kind`.
"""
from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .enums import ExecutionState, WorkerStatus
from .jobs import JobCancellation
from .version import ImageDigest, ProtocolMessage, SemVer, Uuid7


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ImageInfo(StrictModel):
    name: str = Field(min_length=1, max_length=512)
    digest: ImageDigest
    app_version: str = Field(min_length=1, max_length=128)


class SupportedBot(StrictModel):
    bot: str = Field(min_length=1, max_length=128)
    manifest_version: SemVer
    operations: list[str] = Field(min_length=1)


class RuntimeInfo(StrictModel):
    python_version: str = Field(min_length=1, max_length=32)
    playwright_version: str = Field(min_length=1, max_length=32)
    browser_revision: str = Field(min_length=1, max_length=64)


class WorkerRegistration(ProtocolMessage):
    """Alta en la flota, declara capacidad y bots soportados (S5.6)."""

    worker_id: Uuid7
    instance_name: str = Field(min_length=1, max_length=128)
    started_at: AwareDatetime
    image: ImageInfo
    supported_protocol_versions: list[int] = Field(min_length=1)
    supported_bots: list[SupportedBot] = Field(min_length=1)
    total_capacity: int = Field(ge=1, le=5)
    sealed_pubkey_pem: str | None = Field(
        default=None,
        max_length=4096,
        description="Clave RSA pública efímera del worker (PEM) para el sobre sellado",
    )
    runtime: RuntimeInfo

    @model_validator(mode="after")
    def _positive_versions(self) -> WorkerRegistration:
        if any(v < 1 for v in self.supported_protocol_versions):
            raise ValueError("supported_protocol_versions debe contener enteros >= 1")
        return self


Registration = WorkerRegistration


class RegistrationResponse(ProtocolMessage):
    """Respuesta de la central al registro (S5.6)."""

    worker_id: Uuid7
    registration_status: WorkerStatus
    heartbeat_interval_seconds: int = Field(gt=0)
    heartbeat_timeout_seconds: int = Field(gt=0)
    assignment_ack_timeout_seconds: int = Field(gt=0)
    lease_ttl_seconds: int = Field(gt=0)
    server_time: AwareDatetime


class RunningJobInfo(StrictModel):
    job_id: Uuid7
    attempt: int = Field(ge=1)
    execution_state: ExecutionState
    started_at: AwareDatetime
    last_progress_at: AwareDatetime


class ResourceUsage(StrictModel):
    cpu_percent: float = Field(ge=0, le=100)
    memory_bytes: int = Field(ge=0)
    memory_limit_bytes: int = Field(gt=0)
    disk_free_bytes: int = Field(ge=0)
    shm_free_bytes: int = Field(ge=0)
    event_loop_lag_ms: int = Field(ge=0)
    browser_processes: int = Field(ge=0)


class WorkerHeartbeat(ProtocolMessage):
    """Salud, capacidad, cola y recursos, cada 10 s con jitter (S5.7)."""

    worker_id: Uuid7
    sent_at: AwareDatetime
    state: WorkerStatus
    running_jobs: list[RunningJobInfo] = Field(default_factory=list)
    queued_jobs: int = Field(ge=0)
    total_capacity: int = Field(ge=1, le=5)
    free_capacity: int = Field(ge=0, le=5)
    image_version: str = Field(min_length=1, max_length=128)
    image_digest: ImageDigest
    protocol_version_supported: int = Field(ge=1)
    supported_bots: list[SupportedBot] = Field(min_length=1)
    resources: ResourceUsage

    @model_validator(mode="after")
    def _coherent_capacity(self) -> WorkerHeartbeat:
        if len(self.running_jobs) > self.total_capacity:
            raise ValueError("running_jobs no puede exceder total_capacity")
        if self.free_capacity > self.total_capacity:
            raise ValueError("free_capacity no puede exceder total_capacity")
        return self


Heartbeat = WorkerHeartbeat


class HeartbeatResponse(ProtocolMessage):
    """Respuesta de la central al latido (S5.7)."""

    drain_requested: bool
    cancel_requests: list[JobCancellation] = Field(default_factory=list)
    server_time: AwareDatetime


class WorkerNode(StrictModel):
    """Descriptor de un nodo ejecutor dentro del registro unificado."""

    kind: Literal["WORKER_NODE"] = "WORKER_NODE"
    worker_id: Uuid7
    instance_name: str = Field(min_length=1, max_length=128)
    total_capacity: int = Field(ge=1, le=5)


class AdminNode(StrictModel):
    """Descriptor de un nodo administrativo dentro del registro unificado."""

    kind: Literal["ADMIN_NODE"] = "ADMIN_NODE"
    admin_id: Uuid7
    instance_name: str = Field(min_length=1, max_length=128)


WORKER_NODES: TypeAlias = WorkerNode
"""Miembro ejecutor de la union de nodos aceptada por el registro."""

ADMIN_NODES: TypeAlias = AdminNode
"""Miembro administrativo de la union de nodos aceptada por el registro."""


class NodeRegistration(ProtocolMessage):
    """Registro unificado de nodos: acepta la union WORKER_NODES + ADMIN_NODES.

    La central admite en el registro tanto nodos ejecutores como nodos
    administrativos; el discriminante `kind` indica la variante recibida.
    """

    node: Annotated[WORKER_NODES | ADMIN_NODES, Field(discriminator="kind")]
