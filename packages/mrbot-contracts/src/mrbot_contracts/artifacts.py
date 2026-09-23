"""Artefactos y capacidades de subida (plan 00, S5.8, S5.11, S5.13).

Una URL prefirmada otorga solo subida de objeto y vence. El worker nunca
recibe claves de bucket ni lista de buckets.
"""
from __future__ import annotations

from typing import Literal

from pydantic import (
    AnyUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from .version import ProtocolMessage, Sha256Hex, Uuid7


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ArtifactDescriptor(StrictModel):
    """Metadatos de un objeto ya cargado (resultado S5.11)."""

    artifact_id: Uuid7
    slot: str = Field(min_length=1, max_length=128)
    object_key: str = Field(min_length=1, max_length=1024)
    content_type: str = Field(min_length=1, max_length=128)
    size_bytes: int = Field(ge=0)
    sha256: Sha256Hex

    @field_validator("object_key")
    @classmethod
    def _no_traversal(cls, value: str) -> str:
        if value.startswith("/") or ".." in value.split("/"):
            raise ValueError("object_key no admite rutas absolutas ni traversal")
        return value


class PresignedUpload(StrictModel):
    """Capacidad temporal de carga entregada por la central."""

    artifact_slot: str = Field(min_length=1, max_length=128)
    method: Literal["PUT"] = "PUT"
    url: AnyUrl
    headers: dict[str, str] = Field(default_factory=dict)
    expires_at: AwareDatetime
    max_bytes: int = Field(gt=0)
    object_key: str = Field(min_length=1, max_length=1024)


class PresignedArtifactRequest(StrictModel):
    """Un artefacto para el que el worker pide capacidad (S5.13)."""

    artifact_slot: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=128)
    max_bytes: int = Field(gt=0)
    sha256: Sha256Hex | None = None

    @field_validator("filename")
    @classmethod
    def _sanitized_filename(cls, value: str) -> str:
        if value != value.strip() or "/" in value or "\\" in value or value in (".", ".."):
            raise ValueError("filename debe ser un nombre plano sanitizado")
        if ".." in value:
            raise ValueError("filename no admite traversal")
        return value


class PresignedUploadRequest(ProtocolMessage):
    """Solicitud de URLs prefirmadas, worker -> central (S5.13)."""

    request_id: Uuid7
    job_id: Uuid7
    attempt: int = Field(ge=1)
    artifacts: list[PresignedArtifactRequest] = Field(min_length=1, max_length=20)


class PresignedUploadResponse(ProtocolMessage):
    """Concesion de capacidades de carga, central -> worker (S5.13)."""

    request_id: Uuid7
    uploads: list[PresignedUpload] = Field(min_length=1)
    renewed: bool = False
