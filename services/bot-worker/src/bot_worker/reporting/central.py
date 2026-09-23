"""Llamadores del worker hacia la central: presign y cancel-ack.

El sobre ya trae slots con URL prefirmada; cuando un slot no trae URL
(artefacto adicional o URL vencida) el worker pide una nueva con
``POST /internal/v1/jobs/{job_id}/artifacts/presign`` y nunca con
credenciales de bucket (W-1). La cancelación cooperativa se confirma con
``POST /internal/v1/jobs/{job_id}/cancel-ack``.

Nada aquí toca base de datos, ORM ni secretos: el cliente HTTP se inyecta
y las URL firmadas jamás se escriben en logs.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("bot_worker.central")


class PresignError(Exception):
    """No pudo obtenerse una URL prefirmada (transporte o rechazo)."""


def _headers(worker_node: str, service_token: str = "") -> dict[str, str]:
    """Identidad del worker + token de servicio del registro (en memoria)."""
    headers: dict[str, str] = {}
    if worker_node:
        headers["X-Worker-Node"] = worker_node
    if service_token:
        headers["Authorization"] = f"Bearer {service_token}"
    return headers


async def request_presign(
    client: Any,
    base_url: str,
    *,
    job_id: str,
    artifact_id: str,
    content_type: str,
    size_bytes: int,
    worker_node: str = "",
    timeout_seconds: float = 3.0,
    service_token: str = "",
    sealed_privkey: bytes | None = None,
) -> dict[str, Any]:
    """Pide una URL prefirmada de subida para un artefacto.

    Cuerpo exacto de la central: ``{artifact_id, content_type, size_bytes}``.
    Responde en claro ``{upload_id, object_key, upload_url, expires_at,
    required_headers, sealed: False}`` o sellada con la pública efímera del
    worker ``{sealed: True, sealed_section}``; en ese caso se abre en memoria
    con ``sealed_privkey`` y falla cerrado si no abre. Lanza
    :class:`PresignError` si el transporte falla o la central rechaza
    (4xx/5xx). La URL nunca se loguea.
    """
    url = f"{base_url.rstrip('/')}/internal/v1/jobs/{job_id}/artifacts/presign"
    body = {
        "artifact_id": artifact_id,
        "content_type": content_type,
        "size_bytes": size_bytes,
    }
    try:
        resp = await client.post(
            url,
            json=body,
            headers=_headers(worker_node, service_token),
            timeout=timeout_seconds,
        )
    except Exception as exc:
        raise PresignError(
            f"presign sin respuesta para artefacto {artifact_id}"
        ) from exc
    if resp.status_code >= 400:
        raise PresignError(
            f"presign rechazado para artefacto {artifact_id}: HTTP {resp.status_code}"
        )
    try:
        data = resp.json()
    except Exception as exc:
        raise PresignError(
            f"presign con respuesta ilegible para artefacto {artifact_id}"
        ) from exc
    if not isinstance(data, dict):
        raise PresignError(
            f"presign con respuesta ilegible para artefacto {artifact_id}"
        )
    if data.get("sealed") and isinstance(data.get("sealed_section"), dict):
        if sealed_privkey is None:
            raise PresignError(
                f"presign sellado sin clave para artefacto {artifact_id}"
            )
        from bot_worker.runtime.sealed import (
            SealedEnvelopeError,
            decrypt_sealed_section,
        )

        try:
            data = decrypt_sealed_section(sealed_privkey, data["sealed_section"])
        except SealedEnvelopeError as exc:
            raise PresignError(
                f"presign sellado inválido para artefacto {artifact_id}"
            ) from exc
    if not isinstance(data, dict) or not data.get("upload_url"):
        raise PresignError(
            f"presign sin upload_url para artefacto {artifact_id}"
        )
    log.debug("presign ok para artefacto %s", artifact_id)
    return data


async def send_cancel_ack(
    client: Any,
    base_url: str,
    *,
    job_id: str,
    accepted: bool = True,
    attempt: int = 0,
    lease_id: str = "",
    cancel_request_id: str = "",
    worker_node: str = "",
    timeout_seconds: float = 3.0,
    service_token: str = "",
) -> bool:
    """Confirma la cancelación cooperativa ante la central (best-effort).

    Cuerpo: ``{accepted, attempt, lease_id, cancel_request_id}``; la central
    solo exige ``accepted`` y el resto es correlación. Devuelve True si la
    central lo recibió (HTTP < 500); False si el transporte falló. Nunca
    lanza: un acuse perdido no debe tumbar el cleanup local.
    """
    if client is None:
        return False
    url = f"{base_url.rstrip('/')}/internal/v1/jobs/{job_id}/cancel-ack"
    body = {
        "accepted": accepted,
        "attempt": attempt,
        "lease_id": lease_id,
        "cancel_request_id": cancel_request_id,
    }
    try:
        resp = await client.post(
            url,
            json=body,
            headers=_headers(worker_node, service_token),
            timeout=timeout_seconds,
        )
        return resp.status_code < 500
    except Exception:
        log.debug("cancel-ack descartado (transporte)")
        return False


__all__ = ["PresignError", "request_presign", "send_cancel_ack"]
