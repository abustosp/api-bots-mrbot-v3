"""Catálogo de tiers y reglas de entitlement (plan 04 §3-§4).

Los tiers viven en tabla ``plans`` en PostgreSQL (fase F4); este módulo es
la contraparte en memoria del esqueleto: mismos códigos, misma precedencia
(**primero cuota del período, luego créditos**) y misma regla de una sola
fuente de cobro por job (``CUOTA | CREDITOS``, sin mezclar). Los importes
son simbólicos (``PENDIENTE_Q1`` de producto decide los reales); precios en
centavos ARS, nunca float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import uuid


@dataclass(frozen=True)
class PlanTier:
    """Instantánea de condiciones comerciales de un tier."""

    code: str
    nombre: str
    cuota_mensual: int
    concurrencia_max: int
    bots_habilitados: tuple[str, ...]
    prioridad_cola: int
    precio_centavos: int
    precio_unitario_centavos: int = 0
    moneda: str = "ARS"
    activo: bool = True


@dataclass
class SubscriptionPeriod:
    """Período explícito de cuota (reemplaza el reset perezoso de V2)."""

    id: str
    user_id: str
    plan_code: str
    inicio: datetime
    fin: datetime
    cuota_asignada: int
    cuota_reservada: int = 0
    cuota_consumida: int = 0
    estado: str = "ABIERTO"
    plan_snapshot: dict = field(default_factory=dict)


@dataclass
class BillingAccount:
    """Modo comercial asignado manualmente a una cuenta."""

    user_id: str
    account_type: str = "CREDITOS"
    plan_code: str | None = None
    max_requests: int | None = None
    starts_at: datetime = field(default_factory=lambda: _utcnow())
    expires_at: datetime | None = None
    requests_reserved: int = 0
    requests_consumed: int = 0


ACCOUNT_TYPES = frozenset({"CREDITOS", "TIER", "GRATUITA", "LIBRE"})
BILLING_ACCOUNTS: dict[str, BillingAccount] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Catálogo mensual con importes en centavos ARS. El precio unitario decrece
# conforme aumenta el volumen, y el tier mayor limita el consumo a 10.000.
_BOTS_BASE = ("consulta_cuit", "mis_comprobantes")
PLANS: dict[str, PlanTier] = {
    "gratuita": PlanTier("gratuita", "Gratuita", 100, 1, ("consulta_cuit",), 10, 0),
    "tier-100": PlanTier("tier-100", "100 requests", 100, 2, _BOTS_BASE, 30, 1_800_000, 18_000),
    "tier-500": PlanTier("tier-500", "500 requests", 500, 2, _BOTS_BASE, 40, 8_000_000, 16_000),
    "tier-1000": PlanTier("tier-1000", "1.000 requests", 1000, 3, _BOTS_BASE, 50, 14_000_000, 14_000),
    "tier-2500": PlanTier("tier-2500", "2.500 requests", 2500, 3, _BOTS_BASE, 60, 30_000_000, 12_000),
    "tier-5000": PlanTier("tier-5000", "5.000 requests", 5000, 4, _BOTS_BASE, 70, 50_000_000, 10_000),
    "tier-10000": PlanTier("tier-10000", "10.000 requests", 10000, 5, _BOTS_BASE, 80, 80_000_000, 8_000),
}
# Compatibilidad con códigos históricos, ya reconciliados con la nueva cuota.
PLANS["free"] = PlanTier("free", "Gratuita", 100, 1, ("consulta_cuit",), 10, 0)
PLANS.update({"basico": PLANS["tier-100"], "pro": PLANS["tier-1000"], "empresa": PLANS["tier-10000"]})
TIER_PLAN_CODES = tuple(code for code in PLANS if code.startswith("tier-"))

# Costo en créditos por (bot, operación); cambiarlo no altera snapshots.
# Replica ``costo_creditos`` de ``OPERATIONS`` (alias históricos con costo
# previo); el default de ``credit_cost`` es 1 si falta una entrada.
BOT_CREDIT_COST: dict[tuple[str, str], int] = {
    ("consulta_cuit", "consulta"): 1,
    ("mis_comprobantes", "consulta"): 1,
    ("apoc", "consultar"): 1,
    ("aportes_en_linea", "descargar"): 2,
    ("arba", "descargar"): 2,
    ("carga_portal_iva", "cargar"): 3,
    ("ccma", "consultar"): 2,
    ("certificado_mipyme", "descargar"): 1,
    ("compensaciones", "consultar"): 2,
    ("comprobantes", "consultar"): 2,
    ("comprobantes", "solicitar"): 2,
    ("comprobantes", "historial"): 2,
    ("consulta_cuit", "consultar"): 1,
    ("consulta_cuit", "consultar_masivo"): 1,
    ("consulta_pagos_vep", "consultar"): 1,
    ("controladores_fiscales", "presentar"): 2,
    ("declaracion_en_linea", "consultar"): 2,
    ("facturometro", "consultar"): 1,
    ("hacienda", "consultar"): 2,
    ("libros_portal_iva", "descargar_libros"): 2,
    ("libros_portal_iva", "descargar_ddjj"): 2,
    ("liquidacion_granos", "consultar"): 2,
    ("mis_comprobantes", "consultar"): 2,
    ("mis_comprobantes", "solicitar"): 2,
    ("mis_comprobantes", "historial"): 2,
    ("mis_facilidades", "consultar"): 2,
    ("mis_retenciones", "consultar"): 2,
    ("mis_retenciones_iva_simple", "consultar"): 2,
    ("moa", "consultar"): 2,
    ("pago_devoluciones", "consultar"): 2,
    ("portal_iva", "descargar"): 3,
    ("portal_iva", "importar"): 3,
    ("portal_iva", "gestionar"): 3,
    ("rcel", "descargar"): 2,
    ("retper_iibb_agip", "consultar"): 2,
    ("retper_iibb_misiones", "consultar"): 2,
    ("sct", "consultar"): 2,
    ("sifere", "consultar"): 2,
    ("siper", "consultar"): 1,
    ("srt", "consultar_alicuotas"): 2,
    ("vep_archivo", "generar"): 3,
    ("vep_ccma", "generar"): 3,
}

# Paquetes de créditos comprables (catálogo local autoritativo, no del body).
CREDIT_PRODUCTS: dict[str, dict] = {
    "pack-10": {"id": "pack-10", "creditos": 10, "precio_centavos": 200_000, "precio_unitario_centavos": 20_000, "moneda": "ARS", "activo": True},
    "pack-100": {"id": "pack-100", "creditos": 100, "precio_centavos": 2_000_000, "precio_unitario_centavos": 20_000, "moneda": "ARS", "activo": True},
}

# Períodos abiertos por usuario (en PG: subscription_periods + índice parcial).
PERIODS: dict[str, SubscriptionPeriod] = {}


def get_plan(code: str) -> PlanTier | None:
    """Devuelve el tier activo por código, o ``None`` si no existe/retirado."""
    plan = PLANS.get(code)
    return plan if plan and plan.activo else None


def get_billing_account(user_id: str, *, expire: bool = True) -> BillingAccount:
    """Devuelve el modo actual, creando CREDITOS como opción segura por defecto."""
    account = BILLING_ACCOUNTS.get(user_id)
    if account is None:
        # The unauthenticated local development principal is a special LIBRE
        # test account, not a GRATUITA account. Its explicit cap preserves the
        # documented no-secret development workflow without giving real users
        # an automatic free allowance.
        try:
            from central_api.security.principals import ANONYMOUS_USER_ID
            from central_api.settings import get_settings

            is_development_principal = (
                user_id == ANONYMOUS_USER_ID
                and not get_settings().api_key_hmac_secret
            )
        except ImportError:
            is_development_principal = False
        try:
            from central_api.admin.users import USERS
            user = USERS.get(user_id)
        except ImportError:
            user = None
        account = BillingAccount(
            user_id=user_id,
            account_type=("LIBRE" if is_development_principal else getattr(user, "account_type", "CREDITOS")),
            plan_code=getattr(user, "plan_code", None),
            max_requests=(10_000 if is_development_principal else getattr(user, "max_requests", None)),
            expires_at=getattr(user, "account_expires_at", None),
        )
        BILLING_ACCOUNTS[user_id] = account
    if (expire and account.account_type == "GRATUITA" and account.expires_at
            and account.expires_at <= _utcnow()):
        from central_api.billing.reservation import _fallback_gratuita

        _fallback_gratuita(user_id, account, "plazo gratuito vencido")
        period = PERIODS.get(user_id)
        if period is not None and period.estado == "ABIERTO":
            period.estado = "CERRADO"
    return account


def set_billing_account(
    user_id: str,
    account_type: str,
    *,
    plan_code: str | None = None,
    max_requests: int | None = None,
    expires_at: datetime | None = None,
) -> BillingAccount:
    """Valida y reemplaza el modo comercial asignado a una cuenta."""
    mode = account_type.upper()
    if mode not in ACCOUNT_TYPES:
        raise ValueError("tipo de cuenta inválido")
    if mode == "TIER":
        if plan_code not in TIER_PLAN_CODES or get_plan(plan_code) is None:
            raise ValueError("plan de tier inválido")
        max_requests = None
        expires_at = None
    elif mode == "GRATUITA":
        plan_code = "gratuita"
        max_requests = 100
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is None or expires_at <= _utcnow():
            raise ValueError("la cuenta gratuita requiere vencimiento futuro")
    elif mode == "LIBRE":
        plan_code = None
        expires_at = None
        if max_requests is None or not 1 <= max_requests <= 10_000_000:
            raise ValueError("max_requests debe estar entre 1 y 10000000")
    else:
        plan_code = None
        max_requests = None
        expires_at = None
    account = BillingAccount(
        user_id=user_id,
        account_type=mode,
        plan_code=plan_code,
        max_requests=max_requests,
        starts_at=_utcnow(),
        expires_at=expires_at,
    )
    BILLING_ACCOUNTS[user_id] = account
    old_period = PERIODS.get(user_id)
    if old_period is not None and old_period.estado == "ABIERTO":
        old_period.estado = "CERRADO"
    return account


async def hydrate_billing_account(user_id: str) -> BillingAccount:
    """Load the admin-assigned mode from PostgreSQL before admitting a request."""
    from central_api.db import db_configurado, nueva_sesion
    from central_api.security.principals import ANONYMOUS_USER_ID
    from central_api.settings import get_settings

    if (
        not db_configurado()
        or (user_id == ANONYMOUS_USER_ID and not get_settings().api_key_hmac_secret)
    ):
        return get_billing_account(user_id)
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        return get_billing_account(user_id)
    from central_api.models.billing import BillingAccount as BillingAccountRow

    try:
        async with nueva_sesion() as session:
            row = await session.get(BillingAccountRow, user_uuid)
            if row is None:
                return get_billing_account(user_id)
            current = BILLING_ACCOUNTS.get(user_id)
            if (
                current is None
                or current.account_type != row.account_type
                or current.plan_code != row.plan_code
                or current.starts_at != row.starts_at
            ):
                BILLING_ACCOUNTS[user_id] = BillingAccount(
                    user_id=user_id,
                    account_type=row.account_type,
                    plan_code=row.plan_code,
                    max_requests=row.max_requests,
                    starts_at=row.starts_at,
                    expires_at=row.expires_at,
                    requests_reserved=row.requests_reserved,
                    requests_consumed=row.requests_consumed,
                )
    except Exception as exc:
        raise RuntimeError("no se pudo cargar el modo de facturación") from exc
    account = get_billing_account(user_id)
    if account.account_type != row.account_type:
        await persist_billing_account(account)
    return account


async def persist_billing_account(account: BillingAccount) -> None:
    """Persist account-mode transitions made by automatic entitlement rules."""
    from central_api.db import db_configurado, nueva_sesion

    if not db_configurado():
        return
    try:
        user_uuid = uuid.UUID(account.user_id)
    except (ValueError, TypeError, AttributeError) as exc:
        raise RuntimeError("no se pudo persistir una cuenta sin UUID válido") from exc

    from central_api.models.billing import BillingAccount as BillingAccountRow
    from central_api.models.identity import User

    try:
        async with nueva_sesion() as session:
            row = await session.get(BillingAccountRow, user_uuid)
            if row is None:
                if await session.get(User, user_uuid) is None:
                    raise RuntimeError("usuario de la cuenta no encontrado")
                row = BillingAccountRow(user_id=user_uuid, account_type=account.account_type)
                session.add(row)
            row.account_type = account.account_type
            row.plan_code = account.plan_code
            row.max_requests = account.max_requests
            row.starts_at = account.starts_at
            row.expires_at = account.expires_at
            row.requests_reserved = account.requests_reserved
            row.requests_consumed = account.requests_consumed
            row.updated_at = _utcnow()
    except Exception as exc:
        raise RuntimeError("no se pudo persistir la cuenta de facturación") from exc


def bot_enabled_for_plan(plan: PlanTier, bot: str) -> bool:
    """Indica si el plan habilita el bot (sin confundir con costo)."""
    return bot in plan.bots_habilitados


def credit_cost(bot: str, operation: str) -> int:
    """Costo en créditos de una operación (default 1, admisible 0 promo)."""
    return BOT_CREDIT_COST.get((bot, operation), 1)


def open_period(user_id: str, plan_code: str) -> SubscriptionPeriod:
    """Abre un período mensual idempotente (un solo ABIERTO por usuario)."""
    from datetime import timedelta

    existing = PERIODS.get(user_id)
    now = _utcnow()
    if existing and existing.estado == "ABIERTO" and existing.inicio <= now < existing.fin:
        return existing
    if existing and existing.estado == "ABIERTO":
        existing.estado = "CERRADO"
    plan = get_plan(plan_code) or PLANS["free"]
    account = get_billing_account(user_id)
    end = account.expires_at if account.account_type == "GRATUITA" else now + timedelta(days=30)
    if end is None or end <= now:
        end = now + timedelta(days=30)
    period = SubscriptionPeriod(
        id=f"period-{user_id}-{int(now.timestamp())}",
        user_id=user_id,
        plan_code=plan.code,
        inicio=now,
        fin=end,
        cuota_asignada=plan.cuota_mensual,
        plan_snapshot={"code": plan.code, "cuota": plan.cuota_mensual,
                       "prioridad": plan.prioridad_cola},
    )
    PERIODS[user_id] = period
    return period


def current_period(user_id: str) -> SubscriptionPeriod | None:
    """Período vigente (``inicio <= now < fin`` y ``ABIERTO``), o ``None``."""
    period = PERIODS.get(user_id)
    if period is None or period.estado != "ABIERTO":
        return None
    now = _utcnow()
    if not (period.inicio <= now < period.fin):
        return None
    return period


def force_period(user_id: str, plan_code: str, motivo: str) -> SubscriptionPeriod:
    """Período forzado por admin: cierra el abierto y abre uno nuevo ya.

    El período desplazado queda ``CERRADO`` con su consumo intacto (trazable);
    el nuevo hereda el plan indicado con cuota completa. Motivo obligatorio.
    """
    if not (motivo or "").strip():
        raise ValueError("motivo obligatorio para forzar el período")
    anterior = PERIODS.get(user_id)
    if anterior is not None and anterior.estado == "ABIERTO":
        anterior.estado = "CERRADO"
    plan = get_plan(plan_code) or PLANS["free"]
    now = _utcnow()
    from datetime import timedelta

    nuevo = SubscriptionPeriod(
        id=f"period-{user_id}-{int(now.timestamp())}-forzado",
        user_id=user_id,
        plan_code=plan.code,
        inicio=now,
        fin=now + timedelta(days=30),
        cuota_asignada=plan.cuota_mensual,
        plan_snapshot={"code": plan.code, "cuota": plan.cuota_mensual,
                       "prioridad": plan.prioridad_cola,
                       "forzado_por": motivo.strip()},
    )
    PERIODS[user_id] = nuevo
    return nuevo


def close_expired_periods(now: datetime | None = None) -> list[str]:
    """Cierra períodos vencidos (``fin <= now``); tarea periódica de billing.

    No reabre ni reasigna: la admisión solo acepta ``ABIERTO`` vigente, así
    que un cierre demorado nunca regala consumo. Devuelve los IDs cerrados.
    """
    momento = now or _utcnow()
    cerrados: list[str] = []
    from central_api.billing.reservation import _fallback_gratuita

    for user_id, account in list(BILLING_ACCOUNTS.items()):
        if (
            account.account_type == "GRATUITA"
            and account.expires_at is not None
            and account.expires_at <= momento
        ):
            _fallback_gratuita(user_id, account, "plazo gratuito vencido")
    for user_id, period in list(PERIODS.items()):
        if period.estado == "ABIERTO" and period.fin <= momento:
            period.estado = "CERRADO"
            cerrados.append(period.id)
            _ = user_id
    return cerrados


__all__ = [
    "ACCOUNT_TYPES",
    "BILLING_ACCOUNTS",
    "BillingAccount",
    "PlanTier",
    "SubscriptionPeriod",
    "PLANS",
    "TIER_PLAN_CODES",
    "BOT_CREDIT_COST",
    "CREDIT_PRODUCTS",
    "PERIODS",
    "get_plan",
    "get_billing_account",
    "set_billing_account",
    "hydrate_billing_account",
    "persist_billing_account",
    "bot_enabled_for_plan",
    "credit_cost",
    "open_period",
    "current_period",
    "force_period",
    "close_expired_periods",
]
