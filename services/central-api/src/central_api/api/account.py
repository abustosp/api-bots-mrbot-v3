"""Cuenta del cliente: plan, período, consumo y saldo (solo lectura).

``GET /mi/cuenta`` lee el estado calculado de billing y nunca lo "arregla":
no abre, reinicia ni cierra períodos como efecto colateral (plan 02 §5.3,
plan 04 §4). No consume cuota ni créditos.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from central_api.api.dependencies import require_api_principal
from central_api.billing.entitlements import (
    PERIODS,
    current_period,
    get_plan,
)
from central_api.billing.reservation import get_balance
from central_api.security.principals import ApiPrincipal

router = APIRouter()


@router.get("/mi/cuenta")
async def my_account(
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    from central_api.billing.entitlements import hydrate_billing_account

    account = await hydrate_billing_account(principal.user_id)
    period = current_period(principal.user_id)
    plan_code = period.plan_code if period else (account.plan_code or account.account_type.lower())
    plan = get_plan(plan_code)
    return JSONResponse(
        status_code=200,
        content={
            "plan": plan.code if plan else plan_code,
            "tipo_cuenta": account.account_type,
            "max_requests": account.max_requests,
            "requests_included": (
                account.max_requests if account.account_type == "LIBRE"
                else (plan.cuota_mensual if plan and account.account_type in {"TIER", "GRATUITA"} else 0)
            ),
            "account_expires_at": (
                account.expires_at.isoformat().replace("+00:00", "Z")
                if account.expires_at else None
            ),
            "periodo": (
                {
                    "id": period.id,
                    "inicio": period.inicio.isoformat().replace("+00:00", "Z"),
                    "fin": period.fin.isoformat().replace("+00:00", "Z"),
                    "cuota_asignada": period.cuota_asignada,
                    "cuota_reservada": period.cuota_reservada,
                    "cuota_consumida": period.cuota_consumida,
                    "estado": period.estado,
                }
                if period
                else None
            ),
            "saldo_creditos": get_balance(principal.user_id),
            "moneda": "ARS",
            "periodos_históricos": sum(
                1 for p in PERIODS.values() if p.user_id == principal.user_id
            ),
        },
    )
