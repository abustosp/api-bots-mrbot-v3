"""Cuenta del cliente: tipo, cupo y consumo (solo lectura).

``GET /mi/cuenta`` lee el estado calculado y nunca lo "arregla": no abre ni
cierra períodos como efecto colateral, y no consume cupo.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from central_api.api.dependencies import require_api_principal
from central_api.billing.entitlements import (
    account_summary,
    current_period,
    hydrate_billing_account,
)
from central_api.security.principals import ApiPrincipal

router = APIRouter()


@router.get("/mi/cuenta")
async def my_account(
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    account = await hydrate_billing_account(principal.user_id)
    resumen = account_summary(account, current_period(principal.user_id))
    return JSONResponse(
        status_code=200,
        content={
            "tipo_cuenta": resumen["tipo_cuenta"],
            "max_requests": account.max_requests,
            "limite": resumen["limite"],
            "ilimitado": resumen["ilimitado"],
            "renovable": resumen["renovable"],
            "consumidas": resumen["consumidas"],
            "reservadas": resumen["reservadas"],
            "disponibles": resumen["disponibles"],
            "periodo": resumen["periodo"],
        },
    )
