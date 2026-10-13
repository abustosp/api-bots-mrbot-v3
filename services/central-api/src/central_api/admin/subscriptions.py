"""Administración de cuentas de la API v3.

No hay pagos, créditos ni conciliación: el administrador asigna a cada usuario
uno de tres tipos de cuenta.

* ``PRUEBA``: 100 requests en total, no renovables.
* ``TESTING``: requests ilimitadas.
* ``NORMAL``: ``max_requests`` por mes, renovable.

Toda asignación exige motivo y deja auditoría.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Header, HTTPException, Path
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.admin.users import USERS
from central_api.billing.entitlements import MAX_REQUESTS_LIMIT

router = APIRouter()

_AUTH_RESPONSES = {
    401: {
        "description": "Falta una credencial administrativa Bearer. Envíe Authorization: Bearer <ADMIN_TOKEN>.",
        "content": {"application/json": {"example": {"detail": "Falta Bearer [REDACTED]"}}},
    },
    403: {
        "description": "La credencial administrativa es incorrecta o no está configurada. Verifique el token y habilite la administración antes de reintentar.",
        "content": {
            "application/json": {
                "examples": {
                    "token_invalido": {"value": {"detail": "ADMIN_TOKEN inválido"}},
                    "administracion_no_configurada": {"value": {"detail": "ADMIN_TOKEN no configurado"}},
                }
            }
        },
    },
}

_USER_NOT_FOUND = {
    404: {
        "description": "No existe una cuenta asociada al identificador de usuario indicado. Verifique el ID y consulte nuevamente.",
        "content": {"application/json": {"example": {"detail": "usuario no encontrado"}}},
    }
}

_SERVICE_UNAVAILABLE = {
    503: {
        "description": "No se pudo leer o actualizar la información de cuenta o períodos. Compruebe la disponibilidad del servicio y vuelva a intentarlo más tarde.",
        "content": {"application/json": {"example": {"detail": "Servicio no disponible"}}},
    }
}

_VALIDATION_RESPONSE = {
    422: {
        "description": (
            "No se pudo interpretar la forma de un parámetro antes de procesar "
            "la operación. Revise los parámetros y cabeceras indicados en el "
            "detalle; las reglas de negocio se explican en cada operación."
        ),
        "content": {
            "application/json": {
                "example": {
                    "detail": [
                        {"loc": ["path", "user_id"], "msg": "Input should be a valid string", "type": "string_type"}
                    ]
                }
            }
        },
    }
}


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
    account_type: Literal["PRUEBA", "TESTING", "NORMAL"] = Field(
        description="Tipo de cuenta asignado al usuario: PRUEBA, TESTING o NORMAL.",
        examples=["NORMAL"],
    )
    max_requests: int | None = Field(
        default=None,
        ge=1,
        le=MAX_REQUESTS_LIMIT,
        description="Cupo mensual entre 1 y 10000000. Obligatorio para NORMAL; se ignora para PRUEBA y TESTING.",
        examples=[1000],
    )
    motivo: str = Field(
        default="",
        description="Motivo requerido para auditar el cambio; debe tener entre 10 y 500 caracteres.",
        examples=["Ajuste de cupo mensual"],
    )


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


@router.get(
    "/billing/accounts/{user_id}",
    summary="Consultar el tipo de cuenta y su consumo",
    description=(
        "Devuelve el tipo de cuenta del usuario, su cupo y el uso reservado y "
        "consumido. Para una cuenta NORMAL también informa el período mensual "
        "vigente, cuando existe. Requiere autorización administrativa Bearer."
    ),
    responses={
        200: {
            "description": "Datos de cuenta, límites efectivos y resumen del cupo vigente.",
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": {
                        "success": True,
                        "cuenta": {
                            "usuario": {
                                "id": "11111111-1111-4111-8111-111111111111",
                                "email": "operador@example.com",
                                "account_type": "NORMAL",
                            },
                            "tipo_cuenta": {
                                "account_type": "NORMAL",
                                "max_requests": 1000,
                                "requests_reserved": 4,
                                "requests_consumed": 120,
                                "limite_efectivo": 1000,
                                "disponibles": 876,
                                "ilimitado": False,
                                "renovable": True,
                            },
                            "cuota": {
                                "tipo_cuenta": "NORMAL",
                                "limite": 1000,
                                "ilimitado": False,
                                "renovable": True,
                                "consumidas": 120,
                                "reservadas": 4,
                                "disponibles": 876,
                                "periodo": {
                                    "id": "period-id",
                                    "inicio": "2026-10-01T03:00:00Z",
                                    "fin": "2026-11-01T03:00:00Z",
                                    "estado": "ABIERTO",
                                },
                            },
                        },
                    },
                }
            },
        },
        **_AUTH_RESPONSES,
        **_USER_NOT_FOUND,
        **_SERVICE_UNAVAILABLE,
        **_VALIDATION_RESPONSE,
    },
)
async def ver_cuenta(
    user_id: Annotated[
        str,
        Path(description="Identificador del usuario cuya cuenta se consulta.", examples=["11111111-1111-4111-8111-111111111111"]),
    ],
    authorization: str | None = Header(default=None),
) -> dict:
    """Consulta el tipo de cuenta y el estado de su cupo."""
    require_admin(authorization)
    await hidratar_usuario_y_cuenta(user_id)
    return {"success": True, "cuenta": _vista_cuenta(user_id)}


@router.post(
    "/billing/accounts/{user_id}/account-type",
    summary="Asignar o cambiar el tipo de cuenta",
    description=(
        "Asigna PRUEBA, TESTING o NORMAL a un usuario y registra el motivo de "
        "la decisión. NORMAL requiere max_requests entre 1 y 10000000. "
        "Reasignar el mismo tipo conserva el consumo; cambiar de tipo reinicia "
        "los contadores. Requiere autorización administrativa Bearer."
    ),
    responses={
        200: {
            "description": "Confirma la nueva asignación y devuelve el estado actualizado del cupo.",
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": {
                        "success": True,
                        "cuenta": {
                            "usuario": {
                                "id": "11111111-1111-4111-8111-111111111111",
                                "email": "operador@example.com",
                                "account_type": "NORMAL",
                            },
                            "tipo_cuenta": {
                                "account_type": "NORMAL",
                                "max_requests": 1000,
                                "requests_reserved": 0,
                                "requests_consumed": 0,
                                "limite_efectivo": 1000,
                                "disponibles": 1000,
                                "ilimitado": False,
                                "renovable": True,
                            },
                            "cuota": {
                                "tipo_cuenta": "NORMAL",
                                "limite": 1000,
                                "ilimitado": False,
                                "renovable": True,
                                "consumidas": 0,
                                "reservadas": 0,
                                "disponibles": 1000,
                                "periodo": None,
                            },
                        },
                    },
                }
            },
        },
        400: {
            "description": "El motivo falta o no tiene entre 10 y 500 caracteres. Añada un motivo válido y vuelva a enviar el cambio.",
            "content": {
                "application/json": {
                    "examples": {
                        "motivo_obligatorio": {"value": {"detail": "motivo obligatorio"}},
                        "motivo_fuera_de_rango": {"value": {"detail": "motivo debe tener entre 10 y 500 caracteres"}},
                    }
                }
            },
        },
        **_USER_NOT_FOUND,
        422: {
            "description": (
                "El cuerpo no cumple el contrato: el tipo debe ser PRUEBA, "
                "TESTING o NORMAL; max_requests debe estar entre 1 y 10000000; "
                "y NORMAL requiere que max_requests esté informado. Corrija "
                "el cuerpo según el tipo de cuenta solicitado."
            ),
            "content": {
                "application/json": {
                    "examples": {
                        "tipo_no_admitido": {
                            "value": {
                                "detail": [
                                    {"loc": ["body", "account_type"], "msg": "Input should be 'PRUEBA', 'TESTING' or 'NORMAL'", "type": "literal_error"}
                                ]
                            }
                        },
                        "cupo_normal_ausente": {
                            "value": {"detail": "max_requests es obligatorio para NORMAL (1 a 10000000)"}
                        },
                    }
                }
            },
        },
        **_AUTH_RESPONSES,
        **_SERVICE_UNAVAILABLE,
    },
)
async def cambiar_tipo_cuenta(
    user_id: Annotated[
        str,
        Path(description="Identificador del usuario que recibirá la asignación.", examples=["11111111-1111-4111-8111-111111111111"]),
    ],
    body: CambiarTipoCuentaBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID", description="Identificador opcional para relacionar la operación con una solicitud del cliente.", examples=["req-20261012-001"]),
) -> dict:
    """Asigna un tipo de cuenta con motivo obligatorio y registro de auditoría.

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


@router.post(
    "/billing/periods/close-expired",
    summary="Cerrar períodos mensuales vencidos",
    description=(
        "Cierra los períodos mensuales cuyo vencimiento ya pasó y devuelve "
        "sus identificadores. No reabre períodos ni asigna cupo, y no es un "
        "requisito para que una cuenta NORMAL use el período siguiente. "
        "Requiere autorización administrativa Bearer."
    ),
    responses={
        200: {
            "description": "Lista de identificadores de los períodos que se cerraron; puede estar vacía.",
            "content": {
                "application/json": {
                    "schema": {"type": "object"},
                    "example": {"success": True, "cerrados": ["period-id"]},
                }
            },
        },
        **_VALIDATION_RESPONSE,
        **_AUTH_RESPONSES,
        **_SERVICE_UNAVAILABLE,
    },
)
async def cerrar_periodos_vencidos(
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID", description="Identificador opcional para relacionar la operación con una solicitud del cliente.", examples=["req-20261012-002"]),
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
