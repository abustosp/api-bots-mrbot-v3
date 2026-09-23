"""Errores internos y su mapeo a HTTP (plan 00, S5.16).

La taxonomia de `error_category` (disciplina V2 de errores publicos) vive en
`enums.ErrorCategory`. Este modulo cubre el sobre de error del protocolo, los
codigos internos y su traduccion a estado HTTP. Nada de esto se refleja
directamente en la API publica.
"""
from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from .version import ProtocolMessage, Uuid7


class InternalErrorCode(StrEnum):
    WORKER_ID_CONFLICT = "WORKER_ID_CONFLICT"
    INVALID_ENVELOPE = "INVALID_ENVELOPE"
    INVALID_SERVICE_SIGNATURE = "INVALID_SERVICE_SIGNATURE"
    STALE_REQUEST = "STALE_REQUEST"
    WORKER_NOT_AUTHORIZED = "WORKER_NOT_AUTHORIZED"
    WORKER_NOT_FOUND = "WORKER_NOT_FOUND"
    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    CAPACITY_FULL = "CAPACITY_FULL"
    DRENANDO = "DRENANDO"
    STALE_ATTEMPT = "STALE_ATTEMPT"
    LEASE_EXPIRED = "LEASE_EXPIRED"
    DEADLINE_EXPIRED = "DEADLINE_EXPIRED"
    UNSUPPORTED_BOT = "UNSUPPORTED_BOT"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    MANIFEST_MISMATCH = "MANIFEST_MISMATCH"
    IMAGE_VERSION_MISMATCH = "IMAGE_VERSION_MISMATCH"
    PROTOCOL_MISMATCH = "PROTOCOL_MISMATCH"
    PROTOCOL_VERSION_MISMATCH = "PROTOCOL_VERSION_MISMATCH"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_CONTROL_ERROR = "INTERNAL_CONTROL_ERROR"
    RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"


ERROR_HTTP_STATUS: dict[InternalErrorCode, int] = {
    InternalErrorCode.INVALID_ENVELOPE: 400,
    InternalErrorCode.INVALID_SERVICE_SIGNATURE: 401,
    InternalErrorCode.STALE_REQUEST: 401,
    InternalErrorCode.WORKER_NOT_AUTHORIZED: 403,
    InternalErrorCode.WORKER_NOT_FOUND: 404,
    InternalErrorCode.JOB_NOT_FOUND: 404,
    InternalErrorCode.CAPACITY_FULL: 409,
    InternalErrorCode.DRENANDO: 409,
    InternalErrorCode.STALE_ATTEMPT: 409,
    InternalErrorCode.WORKER_ID_CONFLICT: 409,
    InternalErrorCode.LEASE_EXPIRED: 410,
    InternalErrorCode.DEADLINE_EXPIRED: 410,
    InternalErrorCode.UNSUPPORTED_BOT: 422,
    InternalErrorCode.UNSUPPORTED_OPERATION: 422,
    InternalErrorCode.MANIFEST_MISMATCH: 422,
    InternalErrorCode.IMAGE_VERSION_MISMATCH: 422,
    InternalErrorCode.PROTOCOL_MISMATCH: 422,
    InternalErrorCode.PROTOCOL_VERSION_MISMATCH: 426,
    InternalErrorCode.RATE_LIMITED: 429,
    InternalErrorCode.INTERNAL_CONTROL_ERROR: 500,
    InternalErrorCode.RUNTIME_UNAVAILABLE: 503,
}


def http_status_for(code: InternalErrorCode) -> int:
    """Traduce un codigo interno a su estado HTTP (S5.16)."""
    return ERROR_HTTP_STATUS[code]


class ErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: InternalErrorCode
    message: str = Field(min_length=1, max_length=512)
    retryable: bool
    request_id: Uuid7


class ProtocolError(ProtocolMessage):
    """Sobre de error de toda respuesta interna fallida (S5.16)."""

    error: ErrorDetail
