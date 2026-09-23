"""Enumeraciones del protocolo central-worker (plan 00, S5).

`JobStatus` es el ciclo de vida logico del trabajo, dueno la central y
visible al cliente, en castellano. `ExecutionState` es la fase tecnica del
intento dentro del worker, solo en memoria y nunca expuesta al cliente.
Confundirlas es un error documentado en S5.7.
"""
from __future__ import annotations

from enum import StrEnum


class JobStatus(StrEnum):
    PENDIENTE = "PENDIENTE"
    ASIGNADO = "ASIGNADO"
    CORRIENDO = "CORRIENDO"
    COMPLETO = "COMPLETO"
    FALLIDO = "FALLIDO"
    CANCELADO = "CANCELADO"


class JobResult(StrEnum):
    OK = "OK"
    PARCIAL = "PARCIAL"
    ERROR = "ERROR"


class WorkerStatus(StrEnum):
    REGISTERING = "REGISTERING"
    READY = "READY"
    BUSY = "BUSY"
    FULL = "FULL"
    DRENANDO = "DRENANDO"
    UNHEALTHY = "UNHEALTHY"
    CAIDO = "CAIDO"


WorkerState = WorkerStatus


class ExecutionState(StrEnum):
    ACCEPTED = "ACCEPTED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    UPLOADING = "UPLOADING"


class JobEventType(StrEnum):
    STARTED = "STARTED"
    PROGRESS = "PROGRESS"
    LEASE_RENEWAL = "LEASE_RENEWAL"
    CANCELLING = "CANCELLING"
    ARTIFACT_UPLOADED = "ARTIFACT_UPLOADED"


class RejectionReason(StrEnum):
    CAPACITY_FULL = "CAPACITY_FULL"
    DRENANDO = "DRENANDO"
    UNSUPPORTED_BOT = "UNSUPPORTED_BOT"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    MANIFEST_MISMATCH = "MANIFEST_MISMATCH"
    IMAGE_VERSION_MISMATCH = "IMAGE_VERSION_MISMATCH"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    INVALID_ENVELOPE = "INVALID_ENVELOPE"
    RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"


class CancellationReason(StrEnum):
    USER_REQUEST = "USER_REQUEST"
    ADMIN_REQUEST = "ADMIN_REQUEST"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    DRAIN_TIMEOUT = "DRAIN_TIMEOUT"
    SYSTEM_RETRY = "SYSTEM_RETRY"


class ErrorCategory(StrEnum):
    VALIDATION = "VALIDATION"
    AUTHENTICATION = "AUTHENTICATION"
    AUTHORIZATION = "AUTHORIZATION"
    DISABLED_SERVICE = "DISABLED_SERVICE"
    EXTERNAL_TIMEOUT = "EXTERNAL_TIMEOUT"
    NAVIGATION = "NAVIGATION"
    QUERY = "QUERY"
    DOWNLOAD = "DOWNLOAD"
    PROCESSING = "PROCESSING"
    STORAGE = "STORAGE"
    CAPTCHA = "CAPTCHA"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    CANCELLATION = "CANCELLATION"
    INTERNAL = "INTERNAL"
