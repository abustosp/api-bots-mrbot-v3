"""Administración de cuentas de la API v3.

No hay pagos, créditos ni conciliación: el administrador asigna a cada usuario
uno de tres tipos de cuenta.

* ``PRUEBA``: 100 requests en total, no renovables.
* ``TESTING``: requests ilimitadas.
* ``NORMAL``: ``max_requests`` por mes, renovable.

Toda asignación exige motivo y deja auditoría.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.admin.users import USERS
from central_api.billing.entitlements import MAX_REQUESTS_LIMIT

router = APIRouter()


def _vista_cuenta(user_id: str) -> dict:
    from central_api.billing.entitlements import (
        account_summary,
        current_period,
        get_billing_account,
    )

    usuario = USERS.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    account = get_billing_account(user_id)
    resumen = account_summary(account, current_period(user_id))
    return {
        "usuario": {
            "id": usuario.id,
            "email": usuario.email,
            "account_type": account.account_type,
        },
        "tipo_cuenta": {
            "account_type": account.account_type,
            "max_requests": account.max_requests,
            "requests_reserved": account.requests_reserved,
            "requests_consumed": account.requests_consumed,
            "limite_efectivo": resumen["limite"],
            "disponibles": resumen["disponibles"],
            "ilimitado": resumen["ilimitado"],
            "renovable": resumen["renovable"],
        },
        "cuota": resumen,
    }


class CambiarTipoCuentaBody(BaseModel):
    account_type: Literal["PRUEBA", "TESTING", "NORMAL"]
    max_requests: int | None = Field(
        default=None,
        ge=1,
        le=MAX_REQUESTS_LIMIT,
        description="Cupo mensual. Obligatorio para NORMAL; se ignora en los demás.",
    )
    motivo: str = ""


async def hidratar_usuario_y_cuenta(user_id: str) -> None:
    """Carga usuario y cuenta desde PostgreSQL cuando está activo."""
    from central_api.admin.users import _usuario_pg_por_id
    from central_api.billing.entitlements import hydrate_billing_account
    from central_api.db import db_configurado

    if user_id not in USERS:
        user = await _usuario_pg_por_id(user_id)
        if user is not None:
            USERS[user_id] = user
    if not db_configurado():
        return
    try:
        account = await hydrate_billing_account(user_id)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    user = USERS.get(user_id)
    if user is not None:
        user.account_type = account.account_type
        user.max_requests = account.max_requests


@router.get("/billing/accounts/{user_id}")
async def ver_cuenta(
    user_id: str, authorization: str | None = Header(default=None)
) -> dict:
    """Tipo de cuenta, cupo y consumo de un usuario."""
    require_admin(authorization)
    await hidratar_usuario_y_cuenta(user_id)
    return {"success": True, "cuenta": _vista_cuenta(user_id)}


@router.post("/billing/accounts/{user_id}/account-type")
async def cambiar_tipo_cuenta(
    user_id: str,
    body: CambiarTipoCuentaBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Asigna PRUEBA, TESTING o NORMAL con motivo obligatorio y auditoría.

    Reasignar el mismo tipo conserva el consumo. Cambiar de tipo reinicia los
    contadores del usuario.
    """
    from central_api.billing.entitlements import assign_account, get_billing_account

    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    await hidratar_usuario_y_cuenta(user_id)
    usuario = USERS.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    anterior = get_billing_account(user_id)
    anterior_tipo, anterior_cupo = anterior.account_type, anterior.max_requests
    try:
        account = await assign_account(
            user_id, body.account_type, max_requests=body.max_requests
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except RuntimeError as exc:
        if "no encontrado" in str(exc):
            raise HTTPException(status_code=404, detail=str(exc)) from None
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    usuario.account_type = account.account_type
    usuario.max_requests = account.max_requests
    usuario.plan = account.account_type.lower()
    log_event(
        "billing.account_type.changed",
        actor_id=actor,
        target_type="user",
        target_id=user_id,
        request_id=request_id or "",
        reason=motivo,
        metadata={
            "anterior": anterior_tipo,
            "anterior_max_requests": anterior_cupo,
            "nuevo": account.account_type,
            "max_requests": account.max_requests,
        },
    )
    return {"success": True, "cuenta": _vista_cuenta(user_id)}


@router.post("/billing/periods/close-expired")
async def cerrar_periodos_vencidos(
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Cierra los períodos mensuales vencidos (sin reabrir ni regalar cupo).

    El período siguiente se abre solo con la primera reserva del mes, así que
    este cierre es solo higiene: nunca hace falta para que un NORMAL se renueve.
    """
    actor = require_admin(authorization)
    from central_api.db import db_configurado

    if db_configurado():
        from central_api.billing.postgres import close_expired_periods_in_db

        try:
            cerrados = await close_expired_periods_in_db()
        except Exception:
            raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    else:
        from central_api.billing.entitlements import close_expired_periods

        cerrados = close_expired_periods()
    log_event(
        "billing.periods.closed", actor_id=actor, target_type="billing",
        target_id="periods", request_id=request_id or "",
        metadata={"cerrados": cerrados},
    )
    return {"success": True, "cerrados": cerrados}
