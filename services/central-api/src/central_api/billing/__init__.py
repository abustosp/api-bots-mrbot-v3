"""Dominio de facturación de central-api (plan 04, invariantes B-1 a B-4).

El ``bot-worker`` no conoce precios, planes, cuotas, créditos ni MercadoPago:
todo el dinero vive aquí. Montos en centavos de ARS (entero, nunca float);
la conversión a decimal de la API de MercadoPago vive solo en ``facade``.
"""

from central_api.billing.entitlements import (
    CREDIT_PRODUCTS,
    PLANS,
    PlanTier,
    close_expired_periods,
    force_period,
    get_plan,
)
from central_api.billing.reconciliation import comparar_pago, conciliar_lote
from central_api.billing.reservation import (
    adjust_credits,
    confirm_usage,
    get_balance,
    get_usage,
    release_usage,
    reserve_for_job,
)

__all__ = [
    "CREDIT_PRODUCTS",
    "PLANS",
    "PlanTier",
    "close_expired_periods",
    "force_period",
    "get_plan",
    "comparar_pago",
    "conciliar_lote",
    "adjust_credits",
    "confirm_usage",
    "get_balance",
    "get_usage",
    "release_usage",
    "reserve_for_job",
]
