"""Webhook idempotente de MercadoPago (plan 04 §7.6-§7.8, invariante B-3).

Contrato: el handler valida la firma ``x-signature`` (``ts,v1`` hex sobre
``id:<data.id>;request-id:<...>;ts:<ts>;``), persiste el evento crudo con
``UNIQUE(provider, provider_event_id)`` usando el campo ``id`` de la
notificación (no ``data.id``), responde 200/201 rápido y procesa aparte con
reconsulta obligatoria. Firma inválida: 401 sin procesar. Reintentos del
proveedor (cada 15 min, luego creciente) nunca duplican saldo.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


PROVIDER = "mercadopago"


@dataclass
class PaymentEvent:
    """Fila de ``payment_events`` con idempotencia por evento de notificación."""

    id: str
    provider_event_id: str
    tipo: str
    provider_resource_id: str | None = None
    firma_valida: bool = False
    estado_proceso: str = "RECIBIDO"  # RECIBIDO|PROCESADO|REINTENTAR|RECHAZADO
    error_sanitizado: str | None = None
    payload_hash: str = ""
    procesado_en: datetime | None = None
    recibido_en: datetime = field(default_factory=_utcnow)


EVENTS: dict[tuple[str, str], PaymentEvent] = {}


def build_manifest(
    data_id: str | None, request_id: str | None, ts: str
) -> str:
    """Manifiesto firmado: pares presentes terminados en ``;`` (ver §7.6)."""
    parts: list[str] = []
    if data_id:
        parts.append(f"id:{data_id.lower()};")
    if request_id:
        parts.append(f"request-id:{request_id};")
    parts.append(f"ts:{ts};")
    return "".join(parts)


def is_authentic(
    x_signature: str | None, x_request_id: str | None,
    data_id: str | None, secret: str,
) -> bool:
    """Valida la firma HMAC-SHA256 hex en tiempo constante (solo ``v1``)."""
    if not x_signature or not secret:
        return False
    fields: dict[str, str] = {}
    for piece in x_signature.split(","):
        if "=" in piece:
            key, _, value = piece.partition("=")
            fields[key.strip()] = value.strip()
    ts, received = fields.get("ts", ""), fields.get("v1", "")
    if not ts or not received:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        build_manifest(data_id, x_request_id, ts).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(received, expected)


def register_event(
    body: dict, raw: bytes, firma_valida: bool
) -> tuple[PaymentEvent, bool]:
    """Inserta el evento (``ON CONFLICT DO NOTHING``); indica si es nuevo."""
    event_id = str(body.get("id", ""))
    data = body.get("data") or {}
    resource_id = str(data.get("id")) if data.get("id") is not None else None
    key = (PROVIDER, event_id or f"sin-id:{hashlib.sha256(raw).hexdigest()[:16]}")
    existing = EVENTS.get(key)
    if existing is not None:
        return existing, False
    event = PaymentEvent(
        id=key[1],
        provider_event_id=key[1],
        tipo=str(body.get("type", "")),
        provider_resource_id=resource_id,
        firma_valida=firma_valida,
        estado_proceso="RECIBIDO" if firma_valida else "RECHAZADO",
        payload_hash=hashlib.sha256(raw or b"").hexdigest(),
    )
    EVENTS[key] = event
    return event, True


def mark_processed(event: PaymentEvent, ok: bool, error: str | None = None) -> None:
    """Marca el evento tras el commit local (con error ya saneado)."""
    event.estado_proceso = "PROCESADO" if ok else "REINTENTAR"
    event.error_sanitizado = error
    if ok:
        event.procesado_en = _utcnow()


__all__ = [
    "PROVIDER",
    "PaymentEvent",
    "EVENTS",
    "build_manifest",
    "is_authentic",
    "register_event",
    "mark_processed",
]
