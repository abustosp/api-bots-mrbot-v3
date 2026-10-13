"""Cuenta del cliente: tipo, cupo y consumo (solo lectura).

``GET /mi/cuenta`` lee el estado calculado y nunca lo "arregla": no abre ni
cierra períodos como efecto colateral, y no consume cupo.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from central_api.api.dependencies import require_api_principal
from central_api.billing.entitlements import (
    account_summary,
    current_period,
    hydrate_billing_account,
)
from central_api.security.principals import ApiPrincipal

router = APIRouter()


class PeriodoCuentaRespuesta(BaseModel):
    id: str = Field(description="Identificador del período vigente.", examples=["per_123"])
    inicio: str = Field(description="Inicio del período en formato de fecha y hora ISO 8601.", examples=["2026-10-01T03:00:00Z"])
    fin: str = Field(description="Fin del período en formato de fecha y hora ISO 8601.", examples=["2026-11-01T03:00:00Z"])
    estado: str = Field(description="Estado actual del período.", examples=["ABIERTO"])


class CuentaRespuesta(BaseModel):
    tipo_cuenta: str = Field(description="Categoría asignada a la cuenta.", examples=["PRUEBA"])
    max_requests: int | None = Field(description="Máximo configurado; nulo si no hay tope.", examples=[100])
    limite: int | None = Field(description="Cupo vigente; nulo si es ilimitado.", examples=[100])
    ilimitado: bool = Field(description="Indica si el cupo no tiene límite.", examples=[False])
    renovable: bool = Field(description="Indica si el cupo se renueva por período.", examples=[False])
    consumidas: int = Field(description="Consultas ya consumidas.", examples=[12])
    reservadas: int = Field(description="Consultas reservadas actualmente.", examples=[1])
    disponibles: int | None = Field(description="Consultas disponibles; nulo si el cupo es ilimitado.", examples=[87])
    periodo: PeriodoCuentaRespuesta | None = Field(
        description="Período vigente en cuentas con renovación periódica; nulo si no corresponde.",
        examples=[None],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "tipo_cuenta": "PRUEBA",
                    "max_requests": 100,
                    "limite": 100,
                    "ilimitado": False,
                    "renovable": False,
                    "consumidas": 12,
                    "reservadas": 1,
                    "disponibles": 87,
                    "periodo": None,
                }
            ]
        }
    }


@router.get(
    "/mi/cuenta",
    summary="Consultar el cupo de mi cuenta",
    description=(
        "Muestra el tipo de cuenta, el límite de consultas y el uso consumido, "
        "reservado y disponible. Es una consulta de solo lectura: no modifica "
        "el período ni consume cupo. Requiere una credencial válida cuando la "
        "autenticación de clientes está habilitada."
    ),
    responses={
        200: {
            "model": CuentaRespuesta,
            "description": "Resumen actual de cuenta y cupo. Los valores disponibles pueden ser nulos cuando el cupo es ilimitado.",
        },
        401: {
            "description": "Falta una credencial válida o la cuenta no puede autenticarse. Envía una clave activa asociada a una cuenta habilitada.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        503: {
            "description": "No se puede verificar la cuenta temporalmente. Espera y vuelve a intentarlo.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": {
                            "error_code": "service_not_enabled",
                            "message": "Servicio temporalmente no disponible.",
                        }
                    }
                }
            },
        },
        422: {
            "description": "La solicitud contiene datos o encabezados que no se pudieron interpretar. Revisa el formato de la petición y vuelve a enviarla.",
        },
        500: {
            "description": "No fue posible obtener los datos actuales de cuenta. Reintenta la consulta; si el problema persiste, contacta al soporte de la cuenta.",
            "content": {"text/plain": {"example": "Internal Server Error"}},
        },
    },
)
async def my_account(
    principal: ApiPrincipal = Depends(require_api_principal),
) -> JSONResponse:
    """Devuelve el cupo y el consumo de la cuenta sin modificarlos."""
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
