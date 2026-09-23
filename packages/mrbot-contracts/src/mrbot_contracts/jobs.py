"""Mensajes de job central <-> worker (plan 00, S5.8 a S5.12).

`JobEnvelope` es el sobre autocontenido: todo dato necesario para ejecutar
esta en la asignacion. `credentials` y `proxy` son materiales efimeros solo
en memoria del worker; nunca se persisten ni se copian a eventos, resultados
o logs.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import (
    AnyUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from .artifacts import ArtifactDescriptor, PresignedUpload
from .enums import (
    CancellationReason,
    ErrorCategory,
    ExecutionState,
    JobEventType,
    JobResult,
    JobStatus,
    RejectionReason,
)
from .version import ProtocolMessage, SemVer, Uuid7, check_json_size


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BotTarget(StrictModel):
    slug: str = Field(min_length=1, max_length=128)
    manifest_version: SemVer
    image_version: str = Field(min_length=1, max_length=128)


class EphemeralCredentials(StrictModel):
    kind: str = Field(min_length=1, max_length=64)
    values: dict[str, str] = Field(min_length=1)
    expires_at: AwareDatetime


class ProxyConfig(StrictModel):
    url: AnyUrl
    username: str | None = Field(default=None, min_length=1, max_length=256)
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    region: str | None = Field(default=None, min_length=1, max_length=64)


class JobTiming(StrictModel):
    assigned_at: AwareDatetime
    start_deadline: AwareDatetime
    execution_deadline: AwareDatetime
    timeout_seconds: int = Field(ge=1, le=1800)
    lease_ttl_seconds: int = Field(ge=30, le=300)

    @model_validator(mode="after")
    def _ordered_deadlines(self) -> JobTiming:
        if self.start_deadline <= self.assigned_at:
            raise ValueError("start_deadline debe ser posterior a assigned_at")
        if self.execution_deadline < self.start_deadline:
            raise ValueError("execution_deadline no puede ser anterior a start_deadline")
        return self


class CallbackRoutes(StrictModel):
    base_url: AnyUrl
    events_path: str = Field(min_length=1, max_length=512)
    result_path: str = Field(min_length=1, max_length=512)
    presign_path: str = Field(min_length=1, max_length=512)


class SealedSection(StrictModel):
    """Sección sensible cifrada, central -> worker (híbrido RSA-OAEP-SHA256 + Fernet).

    El worker publica una clave RSA efímera en su registro; la central cifra
    con ella la sección sensible de cada asignación (credenciales fiscales,
    URLs prefirmadas de subida). Solo ese worker puede abrirla, en memoria,
    sin persistirla ni copiarla a eventos, resultados o logs. Cambio compatible:
    campo opcional nuevo.
    """

    alg: Literal["RSA-OAEP-SHA256+Fernet"] = "RSA-OAEP-SHA256+Fernet"
    enc_key_b64: str = Field(min_length=1, max_length=4096)
    blob_b64: str = Field(min_length=1, max_length=1_048_576)


class JobEnvelope(ProtocolMessage):
    """Sobre autocontenido de un intento, central -> worker (S5.8)."""

    assignment_id: Uuid7
    job_id: Uuid7
    attempt: int = Field(ge=1)
    bot: BotTarget
    operation: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any]
    credentials: EphemeralCredentials | None = None
    sealed: SealedSection | None = None
    proxy: ProxyConfig | None = None
    timing: JobTiming
    upload_urls: list[PresignedUpload] = Field(default_factory=list)
    callback: CallbackRoutes
    idempotency_key: str = Field(min_length=1, max_length=256)
    job_protocol_version: int = Field(ge=1)

    @field_validator("payload")
    @classmethod
    def _bounded_payload(cls, value: dict[str, Any]) -> dict[str, Any]:
        check_json_size(value, "payload")
        return value


JobAssignment = JobEnvelope


class JobAcceptance(ProtocolMessage):
    """Aceptacion o rechazo como respuesta a la asignacion (S5.9)."""

    assignment_id: Uuid7
    job_id: Uuid7
    attempt: int = Field(ge=1)
    accepted: bool
    reason_code: RejectionReason | None = None
    reason_detail: str | None = Field(default=None, max_length=512)
    queue_position: int | None = Field(default=None, ge=0)
    accepted_at: AwareDatetime

    @model_validator(mode="after")
    def _coherent_verdict(self) -> JobAcceptance:
        if self.accepted and self.reason_code is not None:
            raise ValueError("accepted=true no admite reason_code")
        if not self.accepted and self.reason_code is None:
            raise ValueError("accepted=false exige reason_code")
        return self


Acceptance = JobAcceptance


class JobProgressEvent(ProtocolMessage):
    """Evento de progreso, worker -> central (S5.10)."""

    event_id: Uuid7
    job_id: Uuid7
    attempt: int = Field(ge=1)
    worker_id: Uuid7
    sequence: int = Field(ge=1)
    event_type: JobEventType
    occurred_at: AwareDatetime
    stage: str = Field(min_length=1, max_length=128)
    progress_percent: int | None = Field(default=None, ge=0, le=100)
    message: str = Field(default="", max_length=256)
    metrics: dict[str, Any] = Field(default_factory=dict)


ProgressEvent = JobProgressEvent


class ExecutionMetrics(StrictModel):
    queued_ms: int = Field(ge=0)
    run_ms: int = Field(ge=0)
    browser_wait_ms: int = Field(ge=0)
    upload_ms: int = Field(ge=0)
    peak_memory_bytes: int = Field(ge=0)
    browser_restarts: int = Field(ge=0)


class JobResultReport(ProtocolMessage):
    """Resultado terminal de un intento, worker -> central (S5.11)."""

    result_id: Uuid7
    job_id: Uuid7
    attempt: int = Field(ge=1)
    worker_id: Uuid7
    status: JobStatus
    result: JobResult | None = None
    error_category: ErrorCategory | None = None
    internal_diagnostic: str | None = Field(default=None, max_length=4096)
    artifacts: list[ArtifactDescriptor] = Field(default_factory=list)
    data_payload: dict[str, Any] | None = None
    execution_metrics: ExecutionMetrics | None = None
    finished_at: AwareDatetime

    @field_validator("data_payload")
    @classmethod
    def _bounded_data(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is not None:
            check_json_size(value, "data_payload")
        return value

    @model_validator(mode="after")
    def _coherent_outcome(self) -> JobResultReport:
        if self.status not in (JobStatus.COMPLETO, JobStatus.FALLIDO, JobStatus.CANCELADO):
            raise ValueError("status debe ser terminal: COMPLETO, FALLIDO o CANCELADO")
        if self.status is JobStatus.COMPLETO and self.result not in (JobResult.OK, JobResult.PARCIAL):
            raise ValueError("COMPLETO exige result OK o PARCIAL")
        if self.status is JobStatus.FALLIDO and self.result is not JobResult.ERROR:
            raise ValueError("FALLIDO exige result ERROR")
        if self.status is JobStatus.CANCELADO and (
            self.result is not None or self.error_category is not None
        ):
            raise ValueError("CANCELADO exige result y error_category nulos")
        if self.result is JobResult.ERROR and self.error_category is None:
            raise ValueError("result ERROR exige error_category")
        if self.result in (JobResult.OK, JobResult.PARCIAL) and self.error_category is not None:
            raise ValueError("result OK/PARCIAL no admite error_category")
        return self


ResultReport = JobResultReport


class JobCancellation(ProtocolMessage):
    """Cancelacion cooperativa, central -> worker (S5.12)."""

    cancellation_id: Uuid7
    job_id: Uuid7
    attempt: int = Field(ge=1)
    reason: CancellationReason
    requested_at: AwareDatetime
    grace_seconds: int = Field(ge=0, le=60)


Cancellation = JobCancellation


class CancellationAck(ProtocolMessage):
    """Confirmacion de cancelacion, worker -> central (S5.12)."""

    cancellation_id: Uuid7
    job_id: Uuid7
    attempt: int = Field(ge=1)
    accepted: bool
    state: ExecutionState
