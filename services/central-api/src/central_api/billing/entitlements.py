"""Tipos de cuenta y reglas de cupo de la API v3.

Ya no existen pagos automáticos, créditos ni planes comerciales. Cada usuario
tiene exactamente uno de tres tipos de cuenta, asignado por un administrador:

* ``PRUEBA``: 100 requests en total, no renovables.
* ``TESTING``: requests ilimitadas (el uso se sigue contando).
* ``NORMAL``: ``max_requests`` por mes, renovable. Los períodos son meses
  calendario en hora de Argentina y el siguiente se abre solo en la primera
  reserva del mes.

PostgreSQL es la fuente de verdad cuando hay ``DATABASE_URL`` (ver
``billing.postgres``). Los diccionarios de este módulo son el respaldo en
memoria para desarrollo y pruebas sin base de datos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import uuid

ACCOUNT_PRUEBA = "PRUEBA"
ACCOUNT_TESTING = "TESTING"
ACCOUNT_NORMAL = "NORMAL"
ACCOUNT_TYPES = (ACCOUNT_PRUEBA, ACCOUNT_TESTING, ACCOUNT_NORMAL)
DEFAULT_ACCOUNT_TYPE = ACCOUNT_PRUEBA

#: Tope fijo y no renovable de una cuenta de prueba.
PRUEBA_MAX_REQUESTS = 100
#: Tope de ``max_requests`` aceptado para una cuenta normal.
MAX_REQUESTS_LIMIT = 10_000_000

# Argentina no tiene horario de verano: un desfase fijo evita depender de tzdata.
_ART = timezone(timedelta(hours=-3), "ART")


@dataclass
class SubscriptionPeriod:
    """Período mensual de una cuenta NORMAL."""

    id: str
    user_id: str
    inicio: datetime
    fin: datetime
    cuota_asignada: int
    cuota_reservada: int = 0
    cuota_consumida: int = 0
    estado: str = "ABIERTO"


@dataclass
class BillingAccount:
    """Tipo de cuenta y contadores acumulados de un usuario."""

    user_id: str
    account_type: str = DEFAULT_ACCOUNT_TYPE
    max_requests: int | None = PRUEBA_MAX_REQUESTS
    starts_at: datetime = field(default_factory=lambda: _utcnow())
    requests_reserved: int = 0
    requests_consumed: int = 0


# Peso informativo por (bot, operación) que se publica en el catálogo como
# ``costo_creditos``. Ya NO se cobra: cada request consume exactamente 1 del
# cupo de la cuenta, sin importar la operación.
BOT_CREDIT_COST: dict[tuple[str, str], int] = {
    ("consulta_cuit", "consulta"): 1,
    ("mis_comprobantes", "consulta"): 1,
    ("apoc", "consultar"): 1,
    ("aportes_en_linea", "descargar"): 2,
    ("arba", "descargar"): 2,
    ("carga_portal_iva", "cargar"): 3,
    ("ccma", "consultar"): 2,
    ("ccma_compensacion", "compensar"): 3,
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
    ("libros_portal_iva", "descargar"): 2,
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

BILLING_ACCOUNTS: dict[str, BillingAccount] = {}
# Un período abierto por usuario (en PG: billing_account_periods).
PERIODS: dict[str, SubscriptionPeriod] = {}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def month_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Inicio y fin (UTC) del mes calendario argentino que contiene ``now``."""
    moment = (now or _utcnow()).astimezone(_ART)
    start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return start.astimezone(timezone.utc), nxt.astimezone(timezone.utc)


def normalize_assignment(
    account_type: str, max_requests: int | None = None
) -> tuple[str, int | None]:
    """Valida una asignación y devuelve ``(tipo, max_requests)`` canónicos."""
    mode = str(account_type or "").strip().upper()
    if mode not in ACCOUNT_TYPES:
        raise ValueError("tipo de cuenta inválido: use PRUEBA, TESTING o NORMAL")
    if mode == ACCOUNT_PRUEBA:
        return mode, PRUEBA_MAX_REQUESTS
    if mode == ACCOUNT_TESTING:
        return mode, None
    if max_requests is None or not 1 <= int(max_requests) <= MAX_REQUESTS_LIMIT:
        raise ValueError(
            f"max_requests es obligatorio para NORMAL (1 a {MAX_REQUESTS_LIMIT})"
        )
    return mode, int(max_requests)


def get_billing_account(user_id: str) -> BillingAccount:
    """Cuenta del usuario en memoria; sin asignación previa es PRUEBA."""
    account = BILLING_ACCOUNTS.get(user_id)
    if account is not None:
        return account
    try:
        from central_api.security.principals import ANONYMOUS_USER_ID
        from central_api.settings import get_settings

        # El principal anónimo de desarrollo local (sin secreto HMAC) no debe
        # consumir una cuenta de prueba: opera como TESTING.
        is_development_principal = (
            user_id == ANONYMOUS_USER_ID and not get_settings().api_key_hmac_secret
        )
    except ImportError:
        is_development_principal = False
    try:
        from central_api.admin.users import USERS

        user = USERS.get(user_id)
    except ImportError:
        user = None
    if is_development_principal:
        mode, limit = ACCOUNT_TESTING, None
    else:
        mode = getattr(user, "account_type", DEFAULT_ACCOUNT_TYPE)
        limit = getattr(user, "max_requests", None)
        try:
            mode, limit = normalize_assignment(mode, limit)
        except ValueError:
            mode, limit = DEFAULT_ACCOUNT_TYPE, PRUEBA_MAX_REQUESTS
    account = BillingAccount(user_id=user_id, account_type=mode, max_requests=limit)
    BILLING_ACCOUNTS[user_id] = account
    return account


def set_billing_account(
    user_id: str, account_type: str, *, max_requests: int | None = None
) -> BillingAccount:
    """Asigna el tipo de cuenta en memoria.

    Cambiar de tipo reinicia los contadores. Reasignar el mismo tipo conserva
    el consumo, de modo que una cuenta de prueba no se renueva re-guardándola.
    En NORMAL cambiar el cupo ajusta también el mes en curso.
    """
    mode, limit = normalize_assignment(account_type, max_requests)
    previous = BILLING_ACCOUNTS.get(user_id)
    period = PERIODS.get(user_id)
    if previous is not None and previous.account_type == mode:
        previous.max_requests = limit
        if mode == ACCOUNT_NORMAL and period is not None and period.estado == "ABIERTO":
            period.cuota_asignada = int(limit or 0)
        return previous
    account = BillingAccount(user_id=user_id, account_type=mode, max_requests=limit)
    BILLING_ACCOUNTS[user_id] = account
    if period is not None and period.estado == "ABIERTO":
        period.estado = "CERRADO"
    return account


def current_period(user_id: str) -> SubscriptionPeriod | None:
    """Período vigente (``inicio <= now < fin`` y ``ABIERTO``), o ``None``."""
    period = PERIODS.get(user_id)
    if period is None or period.estado != "ABIERTO":
        return None
    if not (period.inicio <= _utcnow() < period.fin):
        return None
    return period


def open_period(user_id: str) -> SubscriptionPeriod:
    """Abre (o devuelve) el período del mes en curso de una cuenta NORMAL."""
    existing = current_period(user_id)
    if existing is not None:
        return existing
    old = PERIODS.get(user_id)
    if old is not None and old.estado == "ABIERTO":
        old.estado = "CERRADO"
    account = get_billing_account(user_id)
    start, end = month_bounds()
    period = SubscriptionPeriod(
        id=f"period-{user_id}-{int(start.timestamp())}",
        user_id=user_id,
        inicio=start,
        fin=end,
        cuota_asignada=int(account.max_requests or 0),
    )
    PERIODS[user_id] = period
    return period


def close_expired_periods(now: datetime | None = None) -> list[str]:
    """Cierra los períodos vencidos. No reabre ni reasigna cupo."""
    moment = now or _utcnow()
    closed: list[str] = []
    for period in PERIODS.values():
        if period.estado == "ABIERTO" and period.fin <= moment:
            period.estado = "CERRADO"
            closed.append(period.id)
    return closed


def account_summary(
    account: BillingAccount, period: SubscriptionPeriod | None = None
) -> dict:
    """Resumen público uniforme de cupo (``None`` en tope = ilimitado)."""
    mode = account.account_type
    if mode == ACCOUNT_NORMAL:
        limit = int(account.max_requests or 0)
        if period is not None:
            limit = period.cuota_asignada
            consumed, reserved = period.cuota_consumida, period.cuota_reservada
        else:
            consumed = reserved = 0
    elif mode == ACCOUNT_TESTING:
        limit = None
        consumed, reserved = account.requests_consumed, account.requests_reserved
    else:
        limit = PRUEBA_MAX_REQUESTS
        consumed, reserved = account.requests_consumed, account.requests_reserved
    return {
        "tipo_cuenta": mode,
        "limite": limit,
        "ilimitado": limit is None,
        "renovable": mode == ACCOUNT_NORMAL,
        "consumidas": consumed,
        "reservadas": reserved,
        "disponibles": (
            None if limit is None else max(0, limit - consumed - reserved)
        ),
        "periodo": (
            {
                "id": period.id,
                "inicio": period.inicio.isoformat().replace("+00:00", "Z"),
                "fin": period.fin.isoformat().replace("+00:00", "Z"),
                "estado": period.estado,
            }
            if mode == ACCOUNT_NORMAL and period is not None
            else None
        ),
    }


async def hydrate_billing_account(user_id: str) -> BillingAccount:
    """Refresca cuenta y período vigente desde PostgreSQL (si está activo)."""
    from central_api.db import db_configurado, nueva_sesion
    from central_api.security.principals import ANONYMOUS_USER_ID
    from central_api.settings import get_settings

    if not db_configurado() or (
        user_id == ANONYMOUS_USER_ID and not get_settings().api_key_hmac_secret
    ):
        return get_billing_account(user_id)
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        return get_billing_account(user_id)

    from sqlalchemy import select

    from central_api.billing.postgres import _lock_account
    from central_api.models.billing import BillingAccountPeriod as PeriodRow

    try:
        async with nueva_sesion() as session:
            row = await _lock_account(session, user_uuid)
            now = _utcnow()
            active = (
                await session.execute(
                    select(PeriodRow)
                    .where(
                        PeriodRow.user_id == user_uuid,
                        PeriodRow.status == "ABIERTO",
                        PeriodRow.starts_at <= now,
                        PeriodRow.ends_at > now,
                    )
                    .order_by(PeriodRow.starts_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            account = BillingAccount(
                user_id=user_id,
                account_type=row.account_type,
                max_requests=row.max_requests,
                starts_at=row.starts_at,
                requests_reserved=row.requests_reserved,
                requests_consumed=row.requests_consumed,
            )
            BILLING_ACCOUNTS[user_id] = account
            if active is None:
                PERIODS.pop(user_id, None)
            else:
                PERIODS[user_id] = SubscriptionPeriod(
                    id=str(active.id),
                    user_id=user_id,
                    inicio=active.starts_at,
                    fin=active.ends_at,
                    cuota_asignada=active.included_requests,
                    cuota_reservada=active.requests_reserved,
                    cuota_consumida=active.requests_consumed,
                    estado=active.status,
                )
    except Exception as exc:
        raise RuntimeError("no se pudo cargar el tipo de cuenta") from exc
    return account


async def assign_account(
    user_id: str, account_type: str, *, max_requests: int | None = None
) -> BillingAccount:
    """Asigna el tipo de cuenta de forma durable (PG) o en memoria.

    Levanta ``ValueError`` si la asignación es inválida y ``RuntimeError`` si
    la base no está disponible o el usuario no existe.
    """
    from central_api.db import db_configurado

    mode, limit = normalize_assignment(account_type, max_requests)
    if not db_configurado():
        return set_billing_account(user_id, mode, max_requests=limit)
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        return set_billing_account(user_id, mode, max_requests=limit)
    from central_api.billing.postgres import assign_account_in_db

    await assign_account_in_db(user_uuid, mode, limit)
    BILLING_ACCOUNTS.pop(user_id, None)
    PERIODS.pop(user_id, None)
    return await hydrate_billing_account(user_id)


__all__ = [
    "ACCOUNT_NORMAL",
    "ACCOUNT_PRUEBA",
    "ACCOUNT_TESTING",
    "ACCOUNT_TYPES",
    "BILLING_ACCOUNTS",
    "BOT_CREDIT_COST",
    "BillingAccount",
    "DEFAULT_ACCOUNT_TYPE",
    "MAX_REQUESTS_LIMIT",
    "PERIODS",
    "PRUEBA_MAX_REQUESTS",
    "SubscriptionPeriod",
    "account_summary",
    "assign_account",
    "close_expired_periods",
    "current_period",
    "get_billing_account",
    "hydrate_billing_account",
    "month_bounds",
    "normalize_assignment",
    "open_period",
    "set_billing_account",
]
