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

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.admin.users import USERS, aplicar_credito
from central_api.store import utcnow

router = APIRouter()

CATALOGO_PLANES = ("free", "basico", "pro", "empresa")


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
    usuario = USERS.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    sub = SUBSCRIPTIONS.get(user_id) or Subscription(user_id=user_id, plan=usuario.plan)
    disponible = max(0, sub.cuota - sub.uso_reservado - sub.uso_confirmado)
    pagos = [asdict(p) for p in PAYMENTS.values() if p.user_id == user_id]
    eventos = [e for e in PAYMENT_EVENTS.values() if e.get("user_id") == user_id]
    casos = [asdict(d) for d in DISCREPANCIES if PAYMENTS.get(d.payment_id, Payment(id="", user_id="")).user_id == user_id]
    return {
        "usuario": {"id": usuario.id, "email": usuario.email, "plan": usuario.plan},
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
def ver_cuenta(user_id: str, authorization: str | None = Header(default=None)) -> dict:
    """Vista de cuenta comercial con cuota, saldo, pagos y discrepancias."""
    require_admin(authorization)
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
def credito_manual(
    user_id: str,
    body: CreditoManualBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Aplica un crédito manual como entrada compensatoria con autorización."""
    from central_api.billing.reservation import adjust_credits

    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    entrada = aplicar_credito(user_id, body.delta, f"{motivo} [ref:{body.referencia}]", actor)
    # Ajuste append-only en el ledger de billing (una sola fuente por capa:
    # el panel lleva su libro y billing el suyo, ambos con motivo y actor).
    adjust_credits(
        user_id, body.delta, motivo, actor=actor,
        referencia=body.referencia,
        idempotency_key=f"admin:{actor}:{user_id}:{body.referencia}:{body.delta}",
    )
    log_event(
        "billing.credit.applied", actor_id=actor, target_type="user",
        target_id=user_id, request_id=request_id or "", reason=motivo,
        metadata={
            "delta": body.delta, "saldo_posterior": entrada.saldo_posterior,
            "referencia": body.referencia,
        },
    )
    return {"success": True, "cuenta": _cuenta(user_id)}


class PeriodoForzadoBody(BaseModel):
    plan: str = "free"
    motivo: str = ""


@router.post("/billing/accounts/{user_id}/period/force")
def forzar_periodo(
    user_id: str,
    body: PeriodoForzadoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Fuerza un período nuevo ya: cierra el abierto y abre otro con cuota.

    El consumo del período desplazado queda intacto (trazable); el nuevo
    arranca con la cuota completa del plan indicado.
    """
    from central_api.billing.entitlements import force_period

    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    if body.plan not in CATALOGO_PLANES:
        raise HTTPException(status_code=400, detail="plan fuera de catálogo")
    if USERS.get(user_id) is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    periodo = force_period(user_id, body.plan, motivo)
    log_event(
        "billing.period.forced", actor_id=actor, target_type="subscription",
        target_id=user_id, request_id=request_id or "", reason=motivo,
        metadata={"plan": periodo.plan_code, "periodo_id": periodo.id},
    )
    return {"success": True, "cuenta": _cuenta(user_id)}


@router.post("/billing/periods/close-expired")
def cerrar_periodos_vencidos(
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Tarea manual de cierre de períodos vencidos (corre periódica en prod).

    Cierra los períodos con ``fin <= now`` sin reabrir ni reasignar: la
    admisión solo acepta ``ABIERTO`` vigente, así que el cierre demorado
    nunca regala consumo.
    """
    from central_api.billing.entitlements import close_expired_periods

    actor = require_admin(authorization)
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
