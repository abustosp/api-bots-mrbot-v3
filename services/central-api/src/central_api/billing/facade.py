"""Fachada tipada de billing hacia jobs y MercadoPago (plan 04 §6-§7).

Reglas: la reserva ocurre en admisión antes de asignar; el payload del
webhook nunca acredita por sí solo (siempre hay reconsulta autenticada);
montos en centavos enteros con contraste obligatorio; idempotencia local
derivada del pago local (``uuid5`` estable) para reintentos de transporte.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Espacio propio y versionado para derivar claves de idempotencia MP.
MP_IDEMPOTENCY_NS = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def mp_idempotency_key(payment_id: str, intento: int) -> str:
    """Deriva ``X-Idempotency-Key`` estable (mismo pago+intento, misma clave)."""
    return str(uuid.uuid5(MP_IDEMPOTENCY_NS, f"payment:{payment_id}:attempt:{int(intento)}"))


def a_monto_mp(centavos: int) -> Decimal:
    """Centavos ARS -> decimal de la API de MercadoPago (nunca float)."""
    return Decimal(centavos) / Decimal(100)


def desde_monto_mp(monto: Decimal | str) -> int:
    """Decimal de la API -> centavos (acepta str; nunca pasa por float)."""
    return int((Decimal(str(monto)) * 100).to_integral_value())


@dataclass
class Payment:
    """Fila local de ``payments``: correlación con el recurso remoto."""

    id: str
    user_id: str
    tipo: str  # CREDIT_PACK | SUBSCRIPTION
    estado: str = "PENDIENTE"  # PENDIENTE|APROBADO|RECHAZADO|REEMBOLSADO|EN_DISPUTA
    credit_product_id: str | None = None
    creditos: int = 0
    precio_centavos: int = 0
    moneda: str = "ARS"
    external_reference: str = ""
    provider_payment_id: str | None = None
    creado_en: datetime = field(default_factory=_utcnow)


PAYMENTS: dict[str, Payment] = {}


def new_external_reference() -> str:
    """Referencia externa no adivinable para correlación con MercadoPago."""
    return f"mrbot-{uuid.uuid4().hex}"


def create_credit_payment(
    user_id: str, product_id: str, idempotency_key: str
) -> Payment:
    """Crea el pago local de un paquete (idempotente por llave y usuario)."""
    from central_api.billing.entitlements import CREDIT_PRODUCTS

    for payment in PAYMENTS.values():
        if payment.user_id == user_id and payment.external_reference == idempotency_key:
            return payment
    product = CREDIT_PRODUCTS.get(product_id)
    if product is None or not product.get("activo"):
        raise ValueError("paquete inexistente o inactivo")
    payment = Payment(
        id=str(uuid.uuid4()),
        user_id=user_id,
        tipo="CREDIT_PACK",
        credit_product_id=product_id,
        creditos=int(product["creditos"]),
        precio_centavos=int(product["precio_centavos"]),
        moneda=str(product.get("moneda", "ARS")),
        external_reference=idempotency_key,
    )
    PAYMENTS[payment.id] = payment
    return payment


def apply_verified_payment(
    payment: Payment, remote: dict, reconsulted: bool
) -> Payment:
    """Acredita un pago solo tras reconsulta con contraste exacto.

    Rechaza (sin acreditar) si no hubo reconsulta, el estado no es
    aprobado, o difieren referencia, importe, moneda o cuenta. La
    acreditación usa ``credit_purchase`` (idempotente, B-3).
    """
    from central_api.billing.reservation import credit_purchase

    if not reconsulted:
        return payment
    if str(remote.get("status")) != "approved":
        payment.estado = "RECHAZADO"
        return payment
    if str(remote.get("external_reference")) != payment.external_reference:
        return payment
    try:
        remote_centavos = desde_monto_mp(remote.get("transaction_amount", "0"))
    except Exception:
        return payment
    if remote_centavos != payment.precio_centavos:
        return payment
    if str(remote.get("currency_id", "ARS")) != payment.moneda:
        return payment
    payment.estado = "APROBADO"
    payment.provider_payment_id = str(remote.get("id"))
    credit_purchase(
        payment.user_id, payment.creditos, payment.id,
        idempotency_key=f"payment:{payment.id}:acreditado",
    )
    return payment


def payload_hash(payload: bytes) -> str:
    """Hash SHA-256 hex del cuerpo crudo (se guarda el hash, no el payload)."""
    return hashlib.sha256(payload or b"").hexdigest()


__all__ = [
    "MP_IDEMPOTENCY_NS",
    "mp_idempotency_key",
    "a_monto_mp",
    "desde_monto_mp",
    "Payment",
    "PAYMENTS",
    "new_external_reference",
    "create_credit_payment",
    "apply_verified_payment",
    "payload_hash",
]
