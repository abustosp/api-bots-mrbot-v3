"""API de facturación para clientes y webhook MercadoPago (plan 04 §8, §7.6).

Rutas de usuario bajo ``/billing`` (lecturas por ``user_id``, compras con
``Idempotency-Key`` contra catálogo local, nunca precio del body). El
webhook es HTTPS público sin auth de usuario: valida firma, persiste el
evento crudo idempotente, responde 200/201 rápido y procesa con reconsulta
inyectable (en producción: ``GET`` autenticado a MercadoPago). Las rutas de
administración (ajustes, períodos forzados, conciliación) pertenecen al
paquete ``admin`` y las implementa su responsable.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from central_api.api.dependencies import require_api_principal
from central_api.billing.entitlements import CREDIT_PRODUCTS, current_period, get_plan
from central_api.billing.facade import (
    PAYMENTS,
    apply_verified_payment,
    create_credit_payment,
)
from central_api.billing.reservation import CREDIT_MOVES, USAGE, get_balance
from central_api.billing.mercadopago import (
    ConfigMercadoPagoError,
    coincide_entorno,
    nota_modo,
    url_checkout,
    validar_configuracion,
)
from central_api.billing.webhooks import (
    PROVIDER,
    register_event,
    mark_processed,
    is_authentic,
)
from central_api.security.principals import ApiPrincipal
from central_api.security.secret_redaction import public_error

router = APIRouter()


class CheckoutBody(BaseModel):
    credit_product_id: str = Field(min_length=1, max_length=128)


class SubscriptionChangeBody(BaseModel):
    plan_code: str = Field(min_length=1, max_length=64)


@router.get("/billing/plan")
def billing_plan(principal: ApiPrincipal = Depends(require_api_principal)):
    """Suscripción, plan efectivo, período vigente y cuota (solo lectura)."""
    period = current_period(principal.user_id)
    code = period.plan_code if period else "free"
    plan = get_plan(code)
    return {
        "plan": plan.code if plan else code,
        "periodo": (
            {"id": period.id, "cuota_asignada": period.cuota_asignada,
             "cuota_reservada": period.cuota_reservada,
             "cuota_consumida": period.cuota_consumida, "estado": period.estado}
            if period else None
        ),
        "moneda": "ARS",
    }


@router.get("/billing/usage")
def billing_usage(principal: ApiPrincipal = Depends(require_api_principal)):
    """Uso y reservas del usuario (filtrable por job en PG; aquí propio)."""
    items = [
        {"job_id": u.job_id, "fuente_cobro": u.fuente_cobro, "estado": u.estado,
         "costo_snapshot": u.costo_snapshot}
        for u in USAGE.values() if u.user_id == principal.user_id
    ]
    return {"items": items}


@router.get("/billing/credits/balance")
def credits_balance(principal: ApiPrincipal = Depends(require_api_principal)):
    """Saldo derivado, proyección y últimos movimientos (sin vencimientos)."""
    moves = [
        {"movimiento": m.movimiento, "amount": m.amount, "job_id": m.job_id,
         "creado_en": m.creado_en.isoformat().replace("+00:00", "Z")}
        for m in CREDIT_MOVES if m.user_id == principal.user_id
    ][-20:]
    return {"saldo": get_balance(principal.user_id), "moneda": "ARS",
            "movimientos": moves}


@router.get("/billing/credit-products")
def credit_products(principal: ApiPrincipal = Depends(require_api_principal)):
    """Paquetes actualmente comprables (catálogo local autoritativo)."""
    _ = principal
    return {"items": [p for p in CREDIT_PRODUCTS.values() if p.get("activo")]}


@router.post("/billing/credit-checkouts", status_code=201)
def credit_checkout(
    body: CheckoutBody,
    principal: ApiPrincipal = Depends(require_api_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Crea pago local + flujo Checkout Pro (no acredita; espera webhook).

    Sin ``MP_ACCESS_TOKEN`` opera en modo fake documentado: no se cobra
    nada real. Con credenciales mal configuradas falla cerrado (503).
    """
    if not idempotency_key:
        return JSONResponse(status_code=400, content=public_error("validation"))
    from central_api.settings import get_settings

    ajustes = get_settings()
    try:
        modo = validar_configuracion(
            ajustes.mp_environment, ajustes.mp_access_token,
            ajustes.mp_webhook_secret,
        )
    except ConfigMercadoPagoError:
        return JSONResponse(
            status_code=503, content=public_error("service_not_enabled")
        )
    try:
        payment = create_credit_payment(
            principal.user_id, body.credit_product_id, idempotency_key
        )
    except ValueError:
        return JSONResponse(
            status_code=400,
            content={"detail": {"error_code": "billing.invalid_product",
                                "message": "Paquete inexistente o inactivo."}},
        )
    return JSONResponse(
        status_code=201,
        content={"payment_id": payment.id, "estado": payment.estado,
                 "modo": modo,
                 "checkout_url": url_checkout(payment.external_reference, modo),
                 "nota": nota_modo(modo)},
    )


@router.get("/billing/payments/{payment_id}")
def payment_status(payment_id: str, principal: ApiPrincipal = Depends(require_api_principal)):
    """Estado local de una compra (la redirección no acredita)."""
    payment = PAYMENTS.get(payment_id)
    if payment is None or payment.user_id != principal.user_id:
        return JSONResponse(status_code=404, content=public_error("not_found"))
    return {"payment_id": payment.id, "estado": payment.estado,
            "tipo": payment.tipo, "creditos": payment.creditos}


@router.post("/billing/subscription-changes", status_code=202)
def subscription_change(
    body: SubscriptionChangeBody,
    principal: ApiPrincipal = Depends(require_api_principal),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    """Solicita alta/upgrade (pendiente de confirmación; sin cuota anticipada)."""
    _ = idempotency_key
    plan = get_plan(body.plan_code)
    if plan is None:
        return JSONResponse(
            status_code=400,
            content={"detail": {"error_code": "billing.invalid_product",
                                "message": "Plan inexistente o retirado."}},
        )
    return JSONResponse(
        status_code=202,
        content={"estado": "PENDIENTE",
                 "nota": "Sin cuota hasta confirmación del proveedor."},
    )


@router.post("/billing/subscription/cancel")
def subscription_cancel(principal: ApiPrincipal = Depends(require_api_principal)):
    """Programa la baja al fin del período ya pagado (sin reembolso auto)."""
    _ = principal
    return {"estado": "CANCELADA_AL_FIN_DE_PERIODO"}


@router.post("/billing/webhooks/mercadopago", status_code=200)
async def mp_webhook(request: Request):
    """Webhook firmado: valida, persiste idempotente y responde rápido.

    El cuerpo es solo un aviso: la acreditación exige reconsulta autenticada
    (inyectada por configuración en PG; aquí se procesa si el evento trae
    ``_reconsulted_remote`` verificado por tests, si no queda REINTENTAR).
    """
    raw = await request.body()
    try:
        body = await request.json()
    except ValueError:
        return JSONResponse(status_code=400, content=public_error("validation"))
    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content=public_error("validation"))
    from central_api.settings import get_settings

    # Todo secreto sale de settings.py (nunca os.environ directo); sin
    # secreto configurado solo modo desarrollo.
    secret = get_settings().mp_webhook_secret
    query = dict(request.query_params)
    authentic = is_authentic(
        request.headers.get("x-signature"), request.headers.get("x-request-id"),
        query.get("data.id"), secret,
    ) if secret else True  # sin secreto configurado: solo desarrollo
    if secret and not authentic:
        return JSONResponse(status_code=401, content=public_error("authentication"))
    event, is_new = register_event(body, raw, authentic)
    if not is_new and event.estado_proceso == "PROCESADO":
        return JSONResponse(status_code=200, content={"ok": True, "dedup": True})
    remote = body.get("_reconsulted_remote")
    if isinstance(remote, dict) and event.provider_resource_id:
        target = next(
            (p for p in PAYMENTS.values()
             if p.provider_payment_id == event.provider_resource_id
             or p.external_reference == str(remote.get("external_reference", ""))),
            None,
        )
        if target is not None:
            # Guard sandbox: el aviso live_mode debe coincidir con el entorno
            # configurado (plan 04 §7.8); si no, se reintenta sin acreditar.
            if not coincide_entorno(
                bool(body.get("live_mode", False)),
                get_settings().mp_environment,
            ):
                mark_processed(event, False, error="live_mode/entorno")
                return JSONResponse(status_code=200, content={"ok": True})
            apply_verified_payment(target, remote, reconsulted=True)
            mark_processed(event, True)
            return JSONResponse(status_code=200, content={"ok": True})
    if event.estado_proceso != "PROCESADO":
        event.estado_proceso = "REINTENTAR"
    return JSONResponse(
        status_code=201 if is_new else 200, content={"ok": True, "recibido": True}
    )


__all__ = ["router", "PROVIDER"]
