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
from datetime import datetime, timezone


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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Catálogo placeholder simbólico (plan 04 §3.2): producto define números.
PLANS: dict[str, PlanTier] = {
    "free": PlanTier(
        code="free", nombre="Gratuito", cuota_mensual=20, concurrencia_max=1,
        bots_habilitados=("consulta_cuit",), prioridad_cola=10,
        precio_centavos=0,
    ),
    "basico": PlanTier(
        code="basico", nombre="Básico", cuota_mensual=200, concurrencia_max=2,
        bots_habilitados=("consulta_cuit", "mis_comprobantes"), prioridad_cola=40,
        precio_centavos=0,
    ),
    "pro": PlanTier(
        code="pro", nombre="Pro", cuota_mensual=2000, concurrencia_max=3,
        bots_habilitados=("consulta_cuit", "mis_comprobantes"), prioridad_cola=70,
        precio_centavos=0,
    ),
    "empresa": PlanTier(
        code="empresa", nombre="Empresa", cuota_mensual=20000, concurrencia_max=5,
        bots_habilitados=("consulta_cuit", "mis_comprobantes"), prioridad_cola=100,
        precio_centavos=0,
    ),
}

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
    "pack-10": {"id": "pack-10", "creditos": 10, "precio_centavos": 0, "moneda": "ARS", "activo": True},
    "pack-100": {"id": "pack-100", "creditos": 100, "precio_centavos": 0, "moneda": "ARS", "activo": True},
}

# Períodos abiertos por usuario (en PG: subscription_periods + índice parcial).
PERIODS: dict[str, SubscriptionPeriod] = {}


def get_plan(code: str) -> PlanTier | None:
    """Devuelve el tier activo por código, o ``None`` si no existe/retirado."""
    plan = PLANS.get(code)
    return plan if plan and plan.activo else None


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
    if existing and existing.estado == "ABIERTO":
        return existing
    plan = get_plan(plan_code) or PLANS["free"]
    now = _utcnow()
    period = SubscriptionPeriod(
        id=f"period-{user_id}-{int(now.timestamp())}",
        user_id=user_id,
        plan_code=plan.code,
        inicio=now,
        fin=now + timedelta(days=30),
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
    for user_id, period in list(PERIODS.items()):
        if period.estado == "ABIERTO" and period.fin <= momento:
            period.estado = "CERRADO"
            cerrados.append(period.id)
            _ = user_id
    return cerrados


__all__ = [
    "PlanTier",
    "SubscriptionPeriod",
    "PLANS",
    "BOT_CREDIT_COST",
    "CREDIT_PRODUCTS",
    "PERIODS",
    "get_plan",
    "bot_enabled_for_plan",
    "credit_cost",
    "open_period",
    "current_period",
    "force_period",
    "close_expired_periods",
]
