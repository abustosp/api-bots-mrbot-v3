"""Suscripciones y cobros en el panel: consume billing sin lógica paralela.

- Vista de cuenta comercial por usuario: plan, suscripción, período, cuota,
  uso reservado/confirmado/disponible, saldo, pagos, eventos y discrepancias.
- El reproceso de webhooks es idempotente por ID de evento.
- Conciliar compara proveedor, pagos y eventos sin editar el hecho origen:
  genera un evento compensatorio o de reconciliación.
- La exportación financiera imaginaria exigiría permiso específico; aquí la
  descarga queda auditada como evento.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
import uuid
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.admin.users import USERS, aplicar_credito
from central_api.store import utcnow

router = APIRouter()

CATALOGO_PLANES = (
    "free", "gratuita", "basico", "pro", "empresa",
    "tier-100", "tier-500", "tier-1000", "tier-2500", "tier-5000", "tier-10000",
)


@dataclass
class Subscription:
    """Suscripción de un usuario con período y cuota vigentes."""

    user_id: str
    plan: str = "free"
    estado: str = "activa"
    periodo_inicio: str = ""
    periodo_fin: str = ""
    cuota: int = 0
    uso_reservado: int = 0
    uso_confirmado: int = 0


@dataclass
class Payment:
    """Pago local con su referencia externa de MercadoPago."""

    id: str
    user_id: str
    importe: int = 0
    moneda: str = "ARS"
    estado_local: str = "pendiente"
    estado_proveedor: str = ""
    externo_id: str = ""
    actualizado_en: str = ""


@dataclass
class Discrepancy:
    """Caso operativo de conciliación con dueño, nota y SLA."""

    id: str
    payment_id: str
    motivo: str
    dueno: str
    nota: str
    sla: str
    estado: str = "abierta"
    creada_en: str = ""


SUBSCRIPTIONS: dict[str, Subscription] = {}
PAYMENTS: dict[str, Payment] = {}
# Eventos de webhook procesados por ID (idempotencia B-3).
PAYMENT_EVENTS: dict[str, dict] = {}
RECONCILIATIONS: list[dict] = []
DISCREPANCIES: list[Discrepancy] = []


def _cuenta(user_id: str) -> dict:
    from central_api.billing.entitlements import (
        current_period,
        get_billing_account,
        get_plan,
    )

    usuario = USERS.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    sub = SUBSCRIPTIONS.get(user_id) or Subscription(user_id=user_id, plan=usuario.plan)
    disponible = max(0, sub.cuota - sub.uso_reservado - sub.uso_confirmado)
    pagos = [asdict(p) for p in PAYMENTS.values() if p.user_id == user_id]
    eventos = [e for e in PAYMENT_EVENTS.values() if e.get("user_id") == user_id]
    casos = [asdict(d) for d in DISCREPANCIES if PAYMENTS.get(d.payment_id, Payment(id="", user_id="")).user_id == user_id]
    account = get_billing_account(user_id)
    period = current_period(user_id)
    if account.account_type == "LIBRE":
        limit = account.max_requests or 0
        used = account.requests_reserved + account.requests_consumed
    elif account.account_type in {"TIER", "GRATUITA"}:
        plan = get_plan(account.plan_code or "gratuita")
        limit = period.cuota_asignada if period else (plan.cuota_mensual if plan else 0)
        used = (period.cuota_reservada + period.cuota_consumida) if period else 0
    else:
        limit = None
        used = 0
    return {
        "usuario": {
            "id": usuario.id,
            "email": usuario.email,
            "plan": usuario.plan,
            "account_type": account.account_type,
        },
        "tipo_cuenta": {
            "account_type": account.account_type,
            "plan_code": account.plan_code,
            "max_requests": account.max_requests,
            "expires_at": account.expires_at.isoformat() if account.expires_at else None,
            "requests_reserved": account.requests_reserved,
            "requests_consumed": account.requests_consumed,
            "limite_efectivo": limit,
            "disponibles": max(0, limit - used) if limit is not None else None,
        },
        "suscripcion": asdict(sub),
        "cuota": {
            "total": sub.cuota,
            "reservado": sub.uso_reservado,
            "confirmado": sub.uso_confirmado,
            "disponible": disponible,
        },
        "saldo_creditos": usuario.saldo_creditos,
        "pagos": pagos,
        "eventos": eventos,
        "discrepancias": casos,
    }


class CambiarPlanBody(BaseModel):
    plan: str
    motivo: str = ""


class CambiarTipoCuentaBody(BaseModel):
    account_type: Literal["CREDITOS", "TIER", "GRATUITA", "LIBRE"]
    plan_code: str | None = Field(default=None, max_length=64)
    max_requests: int | None = Field(default=None, ge=1, le=10_000_000)
    expires_at: datetime | None = None
    motivo: str = ""


class SuspenderBody(BaseModel):
    motivo: str = ""


class CreditoManualBody(BaseModel):
    delta: int
    motivo: str = ""
    referencia: str = ""


class ReprocesarBody(BaseModel):
    evento_id: str
    payload: dict = Field(default_factory=dict)


class ConciliarBody(BaseModel):
    estado_proveedor: str
    importe: int = 0
    moneda: str = "ARS"
    motivo: str = ""


class DiscrepanciaBody(BaseModel):
    payment_id: str
    motivo: str = ""
    dueno: str = ""
    nota: str = ""
    sla: str = ""


@router.get("/billing/accounts/{user_id}")
async def ver_cuenta(user_id: str, authorization: str | None = Header(default=None)) -> dict:
    """Vista de cuenta comercial con cuota, saldo, pagos y discrepancias."""
    require_admin(authorization)
    await _hidratar_usuario_y_cuenta(user_id)
    return {"success": True, "cuenta": _cuenta(user_id)}


async def _hidratar_usuario_y_cuenta(user_id: str) -> None:
    """Hidrata las vistas administrativas desde PostgreSQL cuando está activo."""
    from central_api.admin.users import USERS, _usuario_pg_por_id
    from central_api.db import db_configurado, nueva_sesion

    if user_id not in USERS:
        user = await _usuario_pg_por_id(user_id)
        if user is not None:
            USERS[user_id] = user
    if not db_configurado():
        return
    try:
        uuid.UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        return
    from central_api.billing.entitlements import hydrate_billing_account

    try:
        account = await hydrate_billing_account(user_id)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    user = USERS.get(user_id)
    if user is not None:
        user.account_type = account.account_type
        user.plan_code = account.plan_code
        user.max_requests = account.max_requests
        user.account_expires_at = account.expires_at


async def _persistir_tipo_cuenta_pg(account) -> None:
    """Guarda una asignación de modo en billing_accounts."""
    from central_api.db import db_configurado, nueva_sesion
    from sqlalchemy import select, update

    if not db_configurado():
        return
    try:
        user_uuid = uuid.UUID(account.user_id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=404, detail="usuario no encontrado") from None
    from central_api.models.billing import (
        BillingAccount as BillingAccountRow,
        BillingAccountPeriod as BillingAccountPeriodRow,
    )
    from central_api.models.identity import User

    try:
        async with nueva_sesion() as session:
            if await session.get(User, user_uuid) is None:
                raise HTTPException(status_code=404, detail="usuario no encontrado")
            row = (
                await session.execute(
                    select(BillingAccountRow)
                    .where(BillingAccountRow.user_id == user_uuid)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                row = BillingAccountRow(user_id=user_uuid, account_type=account.account_type)
                session.add(row)
            row.account_type = account.account_type
            row.plan_code = account.plan_code
            row.max_requests = account.max_requests
            row.starts_at = account.starts_at
            row.expires_at = account.expires_at
            row.requests_reserved = 0
            row.requests_consumed = 0
            row.generation_id = uuid.uuid4()
            row.updated_at = utcnow()
            await session.execute(
                update(BillingAccountPeriodRow)
                .where(
                    BillingAccountPeriodRow.user_id == user_uuid,
                    BillingAccountPeriodRow.status == "ABIERTO",
                )
                .values(status="CERRADO", close_reason="asignación de cuenta modificada")
            )
            await session.flush()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None


@router.post("/billing/accounts/{user_id}/account-type")
async def cambiar_tipo_cuenta(
    user_id: str,
    body: CambiarTipoCuentaBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Asigna un modo comercial, persiste el cambio y deja auditoría obligatoria."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    await _hidratar_usuario_y_cuenta(user_id)
    usuario = USERS.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    from central_api.billing.entitlements import (
        BILLING_ACCOUNTS,
        get_billing_account,
        set_billing_account,
    )

    anterior = get_billing_account(user_id)
    anterior_copy = type(anterior)(**asdict(anterior))
    try:
        account = set_billing_account(
            user_id,
            body.account_type,
            plan_code=body.plan_code,
            max_requests=body.max_requests,
            expires_at=body.expires_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        await _persistir_tipo_cuenta_pg(account)
    except Exception:
        BILLING_ACCOUNTS[user_id] = anterior_copy
        raise
    usuario.account_type = account.account_type
    usuario.plan_code = account.plan_code
    usuario.max_requests = account.max_requests
    usuario.account_expires_at = account.expires_at
    usuario.plan = account.plan_code or account.account_type.lower()
    log_event(
        "billing.account_type.changed",
        actor_id=actor,
        target_type="user",
        target_id=user_id,
        request_id=request_id or "",
        reason=motivo,
        metadata={
            "anterior": anterior_copy.account_type,
            "nuevo": account.account_type,
            "plan_code": account.plan_code,
            "max_requests": account.max_requests,
            "expires_at": account.expires_at.isoformat() if account.expires_at else None,
        },
    )
    return {"success": True, "cuenta": _cuenta(user_id)}


@router.post("/billing/accounts/{user_id}/plan")
def cambiar_plan(
    user_id: str,
    body: CambiarPlanBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Cambia el plan vía billing con fecha efectiva y prorrateo delegado."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    if body.plan not in CATALOGO_PLANES:
        raise HTTPException(status_code=400, detail="plan fuera de catálogo")
    usuario = USERS.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    sub = SUBSCRIPTIONS.get(user_id) or Subscription(user_id=user_id)
    anterior = sub.plan
    sub.plan = body.plan
    sub.periodo_inicio = sub.periodo_inicio or utcnow().isoformat()
    SUBSCRIPTIONS[user_id] = sub
    usuario.plan = body.plan
    log_event(
        "billing.plan.changed", actor_id=actor, target_type="subscription",
        target_id=user_id, request_id=request_id or "", reason=motivo,
        metadata={"anterior": anterior, "nuevo": body.plan},
    )
    return {"success": True, "cuenta": _cuenta(user_id)}


@router.post("/billing/accounts/{user_id}/suspend")
def suspender_suscripcion(
    user_id: str,
    body: SuspenderBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Suspende la suscripción sin eliminar pagos ni ledger histórico."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    sub = SUBSCRIPTIONS.get(user_id) or Subscription(user_id=user_id)
    sub.estado = "suspendida"
    SUBSCRIPTIONS[user_id] = sub
    log_event(
        "billing.subscription.suspended", actor_id=actor, target_type="subscription",
        target_id=user_id, request_id=request_id or "", reason=motivo,
    )
    return {"success": True, "cuenta": _cuenta(user_id)}


@router.post("/billing/accounts/{user_id}/credit")
async def credito_manual(
    user_id: str,
    body: CreditoManualBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Aplica un crédito manual como entrada compensatoria con autorización."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    await _hidratar_usuario_y_cuenta(user_id)
    usuario = USERS.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    idempotency_key = f"admin:{actor}:{user_id}:{body.referencia}:{body.delta}"
    from central_api.db import db_configurado

    if db_configurado():
        from central_api.billing.postgres import adjust_credits_in_session, balance_for_user
        from central_api.db import nueva_sesion

        try:
            async with nueva_sesion() as session:
                await adjust_credits_in_session(
                    session,
                    user_id=user_id,
                    amount=body.delta,
                    reason=motivo,
                    idempotency_key=idempotency_key,
                )
            usuario.saldo_creditos = await balance_for_user(user_id)
            saldo_posterior = usuario.saldo_creditos
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except Exception:
            raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    else:
        from central_api.billing.reservation import adjust_credits

        entrada = aplicar_credito(
            user_id, body.delta, f"{motivo} [ref:{body.referencia}]", actor
        )
        adjust_credits(
            user_id,
            body.delta,
            motivo,
            actor=actor,
            referencia=body.referencia,
            idempotency_key=idempotency_key,
        )
        saldo_posterior = entrada.saldo_posterior
    log_event(
        "billing.credit.applied", actor_id=actor, target_type="user",
        target_id=user_id, request_id=request_id or "", reason=motivo,
        metadata={
            "delta": body.delta, "saldo_posterior": saldo_posterior,
            "referencia": body.referencia,
        },
    )
    return {"success": True, "cuenta": _cuenta(user_id)}


class PeriodoForzadoBody(BaseModel):
    plan: str = "free"
    motivo: str = ""


@router.post("/billing/accounts/{user_id}/period/force")
async def forzar_periodo(
    user_id: str,
    body: PeriodoForzadoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Fuerza un período nuevo ya: cierra el abierto y abre otro con cuota.

    El consumo del período desplazado queda intacto (trazable); el nuevo
    arranca con la cuota completa del plan indicado.
    """
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    if body.plan not in CATALOGO_PLANES:
        raise HTTPException(status_code=400, detail="plan fuera de catálogo")
    await _hidratar_usuario_y_cuenta(user_id)
    if USERS.get(user_id) is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    from central_api.db import db_configurado

    if db_configurado():
        from central_api.billing.entitlements import hydrate_billing_account
        from central_api.billing.postgres import force_period_for_user

        try:
            periodo = await force_period_for_user(user_id, body.plan, motivo)
            account = await hydrate_billing_account(user_id)
        except ValueError as exc:
            status_code = 404 if "usuario no encontrado" in str(exc) else 400
            raise HTTPException(status_code=status_code, detail=str(exc)) from None
        except Exception:
            raise HTTPException(status_code=503, detail="Servicio no disponible") from None
        usuario = USERS[user_id]
        usuario.account_type = account.account_type
        usuario.plan_code = account.plan_code
        usuario.plan = account.plan_code or account.account_type.lower()
        periodo_id = periodo["id"]
        periodo_plan = periodo["plan_code"]
    else:
        from central_api.billing.entitlements import force_period

        try:
            periodo = force_period(user_id, body.plan, motivo)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        periodo_id = periodo.id
        periodo_plan = periodo.plan_code
    log_event(
        "billing.period.forced", actor_id=actor, target_type="subscription",
        target_id=user_id, request_id=request_id or "", reason=motivo,
        metadata={"plan": periodo_plan, "periodo_id": periodo_id},
    )
    return {"success": True, "cuenta": _cuenta(user_id)}


@router.post("/billing/periods/close-expired")
async def cerrar_periodos_vencidos(
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Tarea manual de cierre de períodos vencidos (corre periódica en prod).

    Cierra los períodos con ``fin <= now`` sin reabrir ni reasignar: la
    admisión solo acepta ``ABIERTO`` vigente, así que el cierre demorado
    nunca regala consumo.
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


class ConciliacionLoteBody(BaseModel):
    remotos: dict = Field(
        default_factory=dict,
        description="Recursos reconsultados por external_reference",
    )
    motivo: str = ""


@router.post("/billing/reconcile/run")
def ejecutar_conciliacion(
    body: ConciliacionLoteBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Conciliación periódica: compara pagos locales con remotos reconsultados.

    No edita el hecho origen: cada diferencia genera un evento de
    reconciliación para revisión operativa (ver ``RECONCILIATIONS``).
    """
    from central_api.billing.reconciliation import conciliar_lote

    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo, obligatorio=False)
    locales = [
        {"id": p.id, "externo_id": p.externo_id, "estado_local": p.estado_local,
         "importe": p.importe, "moneda": p.moneda}
        for p in PAYMENTS.values()
    ]
    informe = conciliar_lote(locales, dict(body.remotos or {}))
    for item in informe["diferencias"]:
        RECONCILIATIONS.append({
            "payment_id": item["payment_id"],
            "diferencias": item["difs"],
            "en": utcnow().isoformat(),
            "por": actor,
            "motivo": motivo,
            "origen": "conciliacion_periodica",
        })
    log_event(
        "billing.reconciliation.run", actor_id=actor, target_type="billing",
        target_id="payments", request_id=request_id or "", reason=motivo,
        metadata={"informe": informe},
    )
    return {"success": True, "informe": informe}


@router.post("/billing/webhooks/reprocess")
def reprocesar_webhook(
    body: ReprocesarBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Reprocesa un webhook por ID de forma idempotente y con trazabilidad."""
    actor = require_admin(authorization)
    if not body.evento_id:
        raise HTTPException(status_code=400, detail="evento_id obligatorio")
    existente = PAYMENT_EVENTS.get(body.evento_id)
    if existente is not None:
        log_event(
            "billing.webhook.reprocessed", actor_id=actor, target_type="payment_event",
            target_id=body.evento_id, request_id=request_id or "", result="dedup",
        )
        return {"success": True, "dedup": True, "evento": existente}
    evento = {
        "evento_id": body.evento_id,
        "user_id": body.payload.get("user_id", ""),
        "tipo": body.payload.get("tipo", "payment"),
        "procesado_en": utcnow().isoformat(),
    }
    PAYMENT_EVENTS[body.evento_id] = evento
    log_event(
        "billing.webhook.reprocessed", actor_id=actor, target_type="payment_event",
        target_id=body.evento_id, request_id=request_id or "",
    )
    return {"success": True, "dedup": False, "evento": evento}


@router.post("/billing/payments/{payment_id}/reconcile")
def conciliar_pago(
    payment_id: str,
    body: ConciliarBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Concilia un pago generando un evento de reconciliación, sin editar el origen."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    pago = PAYMENTS.get(payment_id)
    if pago is None:
        raise HTTPException(status_code=404, detail="pago no encontrado")
    diferencias = []
    if body.estado_proveedor and body.estado_proveedor != pago.estado_local:
        diferencias.append("estado_distinto")
    if body.importe and body.importe != pago.importe:
        diferencias.append("monto_distinto")
    if body.moneda and body.moneda != pago.moneda:
        diferencias.append("moneda_distinta")
    reconciliacion = {
        "payment_id": payment_id,
        "diferencias": diferencias,
        "en": utcnow().isoformat(),
        "por": actor,
        "motivo": motivo,
    }
    RECONCILIATIONS.append(reconciliacion)
    log_event(
        "billing.payment.reconciled", actor_id=actor, target_type="payment",
        target_id=payment_id, request_id=request_id or "", reason=motivo,
        metadata={"diferencias": diferencias},
    )
    return {"success": True, "pago": asdict(pago), "reconciliacion": reconciliacion}


@router.post("/billing/discrepancies", status_code=201)
def marcar_discrepancia(
    body: DiscrepanciaBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Crea un caso operativo de discrepancia con dueño, nota y SLA."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    if body.payment_id not in PAYMENTS:
        raise HTTPException(status_code=404, detail="pago no encontrado")
    caso = Discrepancy(
        id=f"disc-{len(DISCREPANCIES) + 1}",
        payment_id=body.payment_id,
        motivo=motivo,
        dueno=body.dueno or actor,
        nota=body.nota,
        sla=body.sla or "48h",
        creada_en=utcnow().isoformat(),
    )
    DISCREPANCIES.append(caso)
    log_event(
        "billing.discrepancy.opened", actor_id=actor, target_type="payment",
        target_id=body.payment_id, request_id=request_id or "", reason=motivo,
        metadata={"caso": caso.id, "dueno": caso.dueno},
    )
    return {"success": True, "discrepancia": asdict(caso)}


@router.get("/billing/discrepancies")
def listar_discrepancias(authorization: str | None = Header(default=None)) -> dict:
    """Lista los casos operativos de conciliación abiertos y cerrados."""
    require_admin(authorization)
    return {"success": True, "discrepancias": [asdict(d) for d in DISCREPANCIES]}
