"""Gestión de usuarios del panel: alta, baja lógica, claves API y créditos.

- ``users.id`` es UUIDv4 (invariante I-1); la auditoría usa UUIDv7.
- La clave API se muestra UNA sola vez al emitirla; en el store solo quedan
  el prefijo y el verificador HMAC, nunca el valor.
- Los ajustes de crédito son entradas compensatorias del ledger con motivo
  obligatorio.
- Toda operación exige motivo de 3 a 500 caracteres (el mismo mínimo que la
  API key) y genera auditoría.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import string
import uuid
from datetime import datetime, timezone
from typing import Literal
from dataclasses import asdict, dataclass, field
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from central_api.admin._common import (
    MOTIVO_MINIMO_IDENTIDAD,
    enmascarar,
    require_admin,
    validar_motivo,
)
from central_api.admin.audit import log_event
from central_api.admin.notifications import enviar_credenciales_email
from central_api.models.base import new_uuid7
from central_api.settings import get_settings
from central_api.store import utcnow

router = APIRouter()


_RESPUESTAS_ADMIN = {
    401: {
        "description": (
            "Falta la cabecera de autorización Bearer o no tiene el formato esperado. "
            "Envíe Authorization: Bearer <token> con el token administrativo."
        )
    },
    403: {
        "description": (
            "El token administrativo no está configurado o no coincide. "
            "Solicite al operador configurar el acceso y compruebe que usa el token vigente."
        )
    },
}

_RESPUESTA_422 = {
    422: {
        "description": (
            "El cuerpo o algún parámetro no cumple el formato o tipo esperado. "
            "Revise los campos indicados en `detail` y envíe valores con el tipo correcto."
        )
    }
}

_RESPUESTA_503 = {
    503: {
        "description": (
            "No se pudo completar la operación por indisponibilidad del servicio de datos "
            "o de protección de claves. Compruebe la disponibilidad del servicio y vuelva a intentarlo."
        )
    }
}


@dataclass
class AdminUser:
    """Identidad de cliente administrada por el panel."""

    id: str
    email: str
    display_name: str = ""
    estado: str = "habilitado"
    plan: str = "prueba"
    account_type: str = "PRUEBA"
    max_requests: int | None = 100
    motivo: str = ""


@dataclass
class ApiKeyMeta:
    """Metadatos y sobre cifrado de una clave API (nunca el valor en claro)."""

    id: str
    user_id: str
    prefijo: str
    verificador_hmac: str
    scopes: list[str] = field(default_factory=list)
    expira_en: str = ""
    revocada: bool = False
    emitida_en: str = ""
    valor_cifrado: str | None = field(default=None, repr=False)


USERS: dict[str, AdminUser] = {}
API_KEYS: dict[str, ApiKeyMeta] = {}


def _nuevo_id() -> str:
    return str(uuid.uuid4())


def _nueva_clave_alfanumerica(longitud: int = 48) -> str:
    """Genera un secreto al azar solo con letras ASCII y dígitos."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(longitud))


def _normalizar_email(email: str) -> str:
    return (email or "").strip().lower()


_USUARIO_DEBUG_RE = re.compile(r"^[a-z0-9._-]{2,64}$")


def _normalizar_identidad(valor: str) -> str:
    """Acepta email clásico o usuario de depuración sin mail.

    Sin ``@`` se admite un nombre corto de depuración (p. ej. ``abp``),
    normalizado a minúsculas. Con ``@`` se exige un email con dominio.
    """
    texto = (valor or "").strip().lower()
    if not texto:
        raise HTTPException(status_code=400, detail="identidad vacía")
    if "@" in texto:
        local, _, dominio = texto.partition("@")
        if not local or "." not in dominio or " " in texto:
            raise HTTPException(status_code=400, detail="email inválido")
        return texto
    if not _USUARIO_DEBUG_RE.fullmatch(texto):
        raise HTTPException(
            status_code=400,
            detail="usuario de depuración inválido (2-64: a-z 0-9 . _ -)",
        )
    return texto


def _vista_usuario(usuario: AdminUser) -> dict:
    return asdict(usuario)


def _vista_clave(meta: ApiKeyMeta) -> dict:
    datos = asdict(meta)
    datos.pop("verificador_hmac", None)
    datos.pop("valor_cifrado", None)
    datos["revelable"] = bool(meta.valor_cifrado)
    return datos


def _firmar_clave(valor: str) -> str:
    secreto = get_settings().api_key_hmac_secret or "sin-secreto-esqueleto"
    return hmac.new(secreto.encode("utf-8"), valor.encode("utf-8"), hashlib.sha256).hexdigest()


def emitir_clave(
    user_id: str,
    scopes: list[str],
    expira_en: str,
    valor_fijo: str = "",
    reemplaza: str = "",
) -> tuple[ApiKeyMeta, str]:
    """Crea una clave, guarda solo verificador+prefijo y devuelve el valor único.

    Con ``valor_fijo`` se conserva el valor literal en vez de generar uno
    aleatorio. Debe tener al menos 3 caracteres sin espacios y un verificador
    HMAC único entre las claves activas. Una clave revocada no bloquea su
    valor, y ``reemplaza`` (la clave que se rota) tampoco: así un usuario puede
    volver a su valor anterior (p. ej. ``abp``).
    """
    if valor_fijo:
        valor = valor_fijo.strip()
        if len(valor) < 3 or any(ch.isspace() for ch in valor):
            raise HTTPException(
                status_code=400,
                detail="valor_fijo debe tener al menos 3 caracteres sin espacios",
            )
    else:
        valor = "mrk_" + secrets.token_urlsafe(32)
    verificador = _firmar_clave(valor)
    if any(
        k.verificador_hmac == verificador and not k.revocada and k.id != reemplaza
        for k in API_KEYS.values()
    ):
        raise HTTPException(status_code=409, detail="valor de clave ya registrado")
    try:
        from central_api.security.api_key_vault import (
            ApiKeyVaultNotConfigured,
            encrypt_api_key,
        )

        try:
            valor_cifrado = encrypt_api_key(valor)
        except ApiKeyVaultNotConfigured:
            # Los stores puramente efímeros siguen disponibles para desarrollo.
            # Toda escritura PostgreSQL falla cerrada si no puede cifrar.
            valor_cifrado = None
    except Exception:
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    meta = ApiKeyMeta(
        id=str(new_uuid7()),
        user_id=user_id,
        # Una clave manual de hasta 8 caracteres quedaría expuesta completa
        # si se usara como prefijo visible. Para esos valores mostramos un
        # identificador HMAC no reversible, nunca el secreto.
        prefijo=(valor[:8] if len(valor) > 8 else f"manual-{verificador[:8]}"),
        verificador_hmac=verificador,
        scopes=list(scopes),
        expira_en=expira_en or "",
        emitida_en=utcnow().isoformat(),
        valor_cifrado=valor_cifrado,
    )
    API_KEYS[meta.id] = meta
    return meta, valor


class CrearUsuarioBody(BaseModel):
    email: str = Field(
        description=(
            "Email con dominio que contiene un punto, o identificador corto de depuración "
            "de 2–64 caracteres (a-z, 0-9, punto, guion o guion bajo); se normaliza a minúsculas."
        ),
        examples=["ana@ejemplo.com"],
    )
    display_name: str = Field(default="", description="Nombre visible del usuario.", examples=["Ana Pérez"])
    plan: str = Field(
        default="",
        description="Obsoleto: se ignora. Use `account_type` y `max_requests`.",
        examples=["prueba"],
    )
    account_type: Literal["PRUEBA", "TESTING", "NORMAL"] = Field(
        default="PRUEBA",
        description=(
            "Tipo de cuenta inicial: PRUEBA (100 consultas en total, no renovables), "
            "TESTING (ilimitada) o NORMAL (cupo mensual renovable, requiere `max_requests`)."
        ),
        examples=["NORMAL"],
    )
    max_requests: int | None = Field(
        default=None,
        ge=1,
        le=10_000_000,
        description=(
            "Cupo mensual de la cuenta NORMAL (1 a 10000000). Obligatorio para NORMAL; "
            "se ignora para PRUEBA y TESTING."
        ),
        examples=[1000],
    )
    api_key: str = Field(
        default="",
        description=(
            "API key fija opcional, mínimo 3 caracteres sin espacios. "
            "Vacía = generar automáticamente."
        ),
        examples=["clave-demo-123"],
    )
    valor_fijo: str = Field(
        default="",
        description="Alias de api_key para compatibilidad con el panel de depuración.",
        examples=["clave-demo-123"],
    )
    estado: Literal["habilitado", "deshabilitado"] = Field(
        default="habilitado",
        description="Estado inicial de acceso de la cuenta.",
        examples=["habilitado"],
    )
    habilitado: bool | None = Field(
        default=None,
        description="Alias booleano compatible con el formulario V1/V2.",
        examples=[True],
    )
    enviar_credenciales: bool = Field(
        default=False,
        description="Solicita el envío de las credenciales al email indicado.",
        examples=[False],
    )
    send_credentials: bool | None = Field(
        default=None,
        description="Alias compatible con formularios anteriores para solicitar el envío por email.",
        examples=[True],
    )
    send_api_key_email: bool | None = Field(
        default=None,
        description="Alias compatible con V1/V2 para solicitar envío por email.",
        examples=[True],
    )
    motivo: str = Field(
        default="",
        description="Justificación obligatoria de 3 a 500 caracteres para el alta.",
        examples=["Alta solicitada por soporte"],
    )


class MotivoBody(BaseModel):
    motivo: str = Field(
        default="",
        description="Justificación de la operación, entre 3 y 500 caracteres.",
        examples=["Solicitud confirmada por soporte"],
    )


class EmitirClaveBody(BaseModel):
    scopes: list[str] = Field(
        default_factory=list,
        description="Permisos asociados a la clave.",
        examples=[["jobs:read", "jobs:write"]],
    )
    expira_en: str = Field(
        default="",
        description="Fecha y hora de expiración ISO 8601; vacío indica que no se fija vencimiento.",
        examples=["2027-12-31T23:59:59Z"],
    )
    motivo: str = Field(
        default="",
        description="Justificación obligatoria de 3 a 500 caracteres.",
        examples=["Emisión solicitada por el cliente"],
    )
    valor_fijo: str = Field(
        default="",
        description="Valor literal con mínimo 3 caracteres y sin espacios. Vacío = aleatorio.",
        examples=["clave-demo-123"],
    )


class RotarClaveBody(BaseModel):
    key_id: str = Field(
        default="",
        description="Identificador de la clave activa que se reemplazará.",
        examples=["0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e6f"],
    )
    periodo_gracia_horas: int = Field(
        default=0,
        description="Campo de compatibilidad registrado en auditoría; la clave anterior se revoca al completar la rotación.",
        examples=[0],
    )
    motivo: str = Field(
        default="",
        description="Justificación obligatoria de 3 a 500 caracteres.",
        examples=["Rotación preventiva de credenciales"],
    )
    valor_fijo: str = Field(
        default="",
        description="Nuevo valor opcional, mínimo 3 caracteres y sin espacios.",
        examples=["clave-nueva-456"],
    )


async def _id_pg_por_email(email: str) -> str | None:
    """Devuelve el UUID de ``users`` para un email, o ``None`` sin base/dato.

    Reutilizar el UUID persistido mantiene estables la FK de jobs, la
    idempotencia y la auditoría aunque la memoria se vacíe con cada restart.
    """
    from central_api.db import db_configurado, nueva_sesion

    if not db_configurado():
        return None
    try:
        from sqlalchemy import text as _texto

        async with nueva_sesion() as sesion:
            fila = (await sesion.execute(
                _texto("SELECT id FROM users WHERE lower(email) = lower(:email) LIMIT 1"),
                {"email": email},
            )).first()
    except Exception:
        return None
    return str(fila[0]) if fila is not None else None


async def _filas_identidad_pg() -> tuple[list[tuple], list[tuple]]:
    """Lee solo datos administrativos no secretos de usuarios y claves."""
    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.identity import ApiKey, User

    if not db_configurado():
        return [], []
    from sqlalchemy import select

    try:
        async with nueva_sesion() as sesion:
            usuarios = (
                await sesion.execute(
                    select(User.id, User.email, User.habilitado).order_by(User.email)
                )
            ).all()
            claves = (
                await sesion.execute(
                    select(
                        ApiKey.id,
                        ApiKey.user_id,
                        ApiKey.key_prefix,
                        ApiKey.scopes,
                        ApiKey.expires_at,
                        ApiKey.revoked_at,
                        ApiKey.created_at,
                        ApiKey.encrypted_value.is_not(None),
                    ).order_by(ApiKey.created_at.desc())
                )
            ).all()
    except Exception:  # noqa: BLE001 - nunca degradar una lista persistente a memoria vacía
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    return list(usuarios), list(claves)


async def _cuentas_billing_pg() -> dict[str, tuple]:
    """Lee el modo comercial junto con sus límites y vencimiento persistidos."""
    from sqlalchemy import select

    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.billing import BillingAccount as BillingAccountRow

    if not db_configurado():
        return {}
    try:
        async with nueva_sesion() as sesion:
            rows = (
                await sesion.execute(
                    select(
                        BillingAccountRow.user_id,
                        BillingAccountRow.account_type,
                        BillingAccountRow.max_requests,
                        BillingAccountRow.starts_at,
                        BillingAccountRow.requests_reserved,
                        BillingAccountRow.requests_consumed,
                    )
                )
            ).all()
    except Exception:
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    return {str(row.user_id): tuple(row[1:]) for row in rows}


async def _periodos_vigentes_pg() -> dict[str, tuple[int, int, int]]:
    """Cuota (incluidas, consumidas, reservadas) del periodo mensual vigente por usuario."""
    from sqlalchemy import select

    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.billing import BillingAccountPeriod as PeriodRow

    if not db_configurado():
        return {}
    ahora = utcnow()
    try:
        async with nueva_sesion() as sesion:
            rows = (
                await sesion.execute(
                    select(
                        PeriodRow.user_id,
                        PeriodRow.included_requests,
                        PeriodRow.requests_consumed,
                        PeriodRow.requests_reserved,
                    ).where(
                        PeriodRow.status == "ABIERTO",
                        PeriodRow.starts_at <= ahora,
                        PeriodRow.ends_at > ahora,
                    )
                )
            ).all()
    except Exception:
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    return {
        str(r.user_id): (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in rows
    }


def _consumo_usuario(
    usuario: AdminUser, cuenta, periodo: tuple[int, int, int] | None
) -> dict:
    """Consultas actuales y restantes del mes corriente (None = ilimitado)."""
    tipo = usuario.account_type
    if tipo == "NORMAL":
        limite, consumidas, reservadas = periodo or (
            int(usuario.max_requests or 0),
            0,
            0,
        )
    else:
        consumidas = int(getattr(cuenta, "requests_consumed", 0) or 0)
        reservadas = int(getattr(cuenta, "requests_reserved", 0) or 0)
        limite = None if tipo == "TESTING" else int(usuario.max_requests or 100)
    return {
        "consultas_limite": limite,
        "consultas_consumidas": consumidas,
        "consultas_reservadas": reservadas,
        "consultas_restantes": (
            None if limite is None else max(0, limite - consumidas - reservadas)
        ),
    }


async def _usuario_pg_por_id(user_id: str) -> AdminUser | None:
    """Hidrata una identidad persistida para habilitar acciones desde el panel."""
    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.identity import User

    if not db_configurado():
        return None
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        return None
    try:
        async with nueva_sesion() as sesion:
            row = await sesion.get(User, user_uuid)
            from central_api.models.billing import BillingAccount as BillingAccountRow

            billing_row = await sesion.get(BillingAccountRow, user_uuid)
    except Exception:  # noqa: BLE001 - error DB sanitizado
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    if row is None:
        return None
    return AdminUser(
        id=str(row.id),
        email=str(row.email),
        estado="habilitado" if row.habilitado else "deshabilitado",
        plan=(billing_row.account_type.lower() if billing_row else "prueba"),
        account_type=billing_row.account_type if billing_row else "PRUEBA",
        max_requests=billing_row.max_requests if billing_row else 100,
    )


def _meta_clave_desde_fila(row) -> ApiKeyMeta:
    """Convierte una fila ``api_keys`` en metadatos administrativos."""
    return ApiKeyMeta(
        id=str(row.id),
        user_id=str(row.user_id),
        prefijo=str(row.key_prefix),
        # El HMAC persistido usa el formato canónico de autenticación, no se
        # utiliza para comparar una clave nueva en claro en el proceso admin.
        verificador_hmac=str(row.verifier_hmac),
        scopes=list(row.scopes or []),
        expira_en=row.expires_at.isoformat() if row.expires_at else "",
        revocada=row.revoked_at is not None,
        emitida_en=row.created_at.isoformat() if row.created_at else "",
        valor_cifrado=row.encrypted_value,
    )


async def _api_key_pg_por_id(user_id: str, key_id: str) -> ApiKeyMeta | None:
    """Hidrata metadatos de una clave persistida sin revelar su valor."""
    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.identity import ApiKey

    if not db_configurado():
        return None
    try:
        user_uuid = uuid.UUID(user_id)
        key_uuid = uuid.UUID(key_id)
    except (ValueError, TypeError, AttributeError):
        return None
    try:
        async with nueva_sesion() as sesion:
            row = await sesion.get(ApiKey, key_uuid)
    except Exception:  # noqa: BLE001 - error DB sanitizado
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    if row is None or row.user_id != user_uuid:
        return None
    return _meta_clave_desde_fila(row)


async def _clave_activa_pg_por_usuario(user_id: str) -> ApiKeyMeta | None:
    """Devuelve la clave activa más reciente del usuario en PostgreSQL.

    Se usa como respaldo cuando el panel envía el ID de una clave que solo
    existe en el cache del proceso (por ejemplo, una lista desactualizada tras
    una edición manual de la base). Sin este respaldo la rotación fallaba con
    404 aunque el usuario sí tuviera una clave persistida válida.
    """
    from sqlalchemy import func, select

    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.identity import ApiKey

    if not db_configurado():
        return None
    try:
        user_uuid = uuid.UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        return None
    try:
        async with nueva_sesion() as sesion:
            row = (
                await sesion.execute(
                    select(ApiKey)
                    .where(
                        ApiKey.user_id == user_uuid,
                        ApiKey.revoked_at.is_(None),
                        (ApiKey.expires_at.is_(None))
                        | (ApiKey.expires_at > func.current_timestamp()),
                    )
                    .order_by(ApiKey.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - error DB sanitizado
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    return _meta_clave_desde_fila(row) if row is not None else None


async def _persistir_api_key_pg(
    usuario: AdminUser, meta: ApiKeyMeta, valor: str
) -> None:
    """Persiste selector, HMAC y ciphertext, nunca la API key en claro."""
    from sqlalchemy.exc import IntegrityError

    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.billing import BillingAccount as BillingAccountRow
    from central_api.models.identity import ApiKey, User

    if not db_configurado():
        return
    settings = get_settings()
    if not settings.api_key_hmac_secret:
        raise HTTPException(status_code=503, detail="Servicio no disponible")
    valor_cifrado = meta.valor_cifrado
    if not valor_cifrado:
        try:
            from central_api.security.api_key_vault import encrypt_api_key

            valor_cifrado = encrypt_api_key(valor)
        except Exception:  # noqa: BLE001 - persistir sin cifrado queda prohibido
            raise HTTPException(status_code=503, detail="Servicio no disponible") from None
        meta.valor_cifrado = valor_cifrado
    try:
        user_uuid = uuid.UUID(usuario.id)
        key_uuid = uuid.UUID(meta.id)
    except (ValueError, TypeError, AttributeError):
        raise HTTPException(status_code=400, detail="Identidad o clave inválida") from None
    try:
        from central_api.api.dependencies import _selector_y_secreto
        from central_api.security.api_keys import fingerprint_secret

        selector, secreto_a_firmar = _selector_y_secreto(valor)
        verifier = fingerprint_secret(settings.api_key_hmac_secret, secreto_a_firmar)
        expires_at = None
        if meta.expira_en:
            try:
                expires_at = datetime.fromisoformat(
                    meta.expira_en.replace("Z", "+00:00")
                )
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(status_code=400, detail="expira_en inválida") from None
        async with nueva_sesion() as sesion:
            cuenta = await sesion.get(User, user_uuid)
            if cuenta is None:
                cuenta = User(
                    id=user_uuid,
                    email=usuario.email,
                    habilitado=usuario.estado == "habilitado",
                )
                sesion.add(cuenta)
                await sesion.flush()
            billing_account = await sesion.get(BillingAccountRow, user_uuid)
            if billing_account is None:
                sesion.add(
                    BillingAccountRow(
                        user_id=user_uuid, account_type="PRUEBA", max_requests=100
                    )
                )
            sesion.add(
                ApiKey(
                    id=key_uuid,
                    user_id=user_uuid,
                    key_prefix=selector,
                    verifier_hmac=verifier,
                    encrypted_value=valor_cifrado,
                    scopes=list(meta.scopes),
                    expires_at=expires_at,
                )
            )
            await sesion.flush()
    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(status_code=409, detail="valor de clave ya registrado") from None
    except Exception:  # noqa: BLE001 - no filtrar DSN ni datos internos
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None


async def _rotar_api_key_pg(
    usuario: AdminUser,
    anterior: ApiKeyMeta,
    nueva: ApiKeyMeta,
    valor: str,
) -> None:
    """Revoca la clave anterior e inserta la nueva en una sola transacción."""
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.identity import ApiKey, User

    if not db_configurado():
        return
    settings = get_settings()
    if not settings.api_key_hmac_secret:
        raise HTTPException(status_code=503, detail="Servicio no disponible")
    try:
        from central_api.api.dependencies import _selector_y_secreto
        from central_api.security.api_keys import fingerprint_secret
        from central_api.security.api_key_vault import encrypt_api_key

        selector, secreto = _selector_y_secreto(valor)
        verifier = fingerprint_secret(settings.api_key_hmac_secret, secreto)
        encrypted_value = encrypt_api_key(valor)
        user_uuid = uuid.UUID(usuario.id)
        old_key_uuid = uuid.UUID(anterior.id)
        new_key_uuid = uuid.UUID(nueva.id)
    except Exception:  # noqa: BLE001 - no filtrar claves ni datos criptográficos
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None

    try:
        async with nueva_sesion() as sesion:
            cuenta = await sesion.get(User, user_uuid)
            if cuenta is None or not cuenta.habilitado:
                raise HTTPException(status_code=409, detail="el usuario no está activo")
            resultado = await sesion.execute(
                select(ApiKey)
                .where(ApiKey.id == old_key_uuid, ApiKey.user_id == user_uuid)
                .with_for_update()
            )
            anterior_pg = resultado.scalar_one_or_none()
            if anterior_pg is None:
                raise HTTPException(status_code=404, detail="clave anterior no encontrada")
            ahora = utcnow()
            if (
                anterior_pg.revoked_at is not None
                or (anterior_pg.expires_at is not None and anterior_pg.expires_at <= ahora)
            ):
                raise HTTPException(status_code=409, detail="la clave anterior no está activa")
            # La fila de PostgreSQL es autoritativa si el cache del proceso quedó
            # desactualizado. La clave nueva conserva los permisos y vencimiento.
            nueva.scopes = list(anterior_pg.scopes or [])
            nueva.expira_en = (
                anterior_pg.expires_at.isoformat() if anterior_pg.expires_at else ""
            )
            expires_at = anterior_pg.expires_at
            anterior_pg.revoked_at = ahora
            # Revocar primero: si el valor nuevo es el mismo que el actual, el
            # índice único parcial (solo activas) lo permite recién ahora.
            await sesion.flush()
            sesion.add(
                ApiKey(
                    id=new_key_uuid,
                    user_id=user_uuid,
                    key_prefix=selector,
                    verifier_hmac=verifier,
                    encrypted_value=encrypted_value,
                    scopes=list(nueva.scopes),
                    expires_at=expires_at,
                    replaces_key_id=old_key_uuid,
                )
            )
            await sesion.flush()
        nueva.valor_cifrado = encrypted_value
    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(status_code=409, detail="valor de clave ya registrado") from None
    except Exception:  # noqa: BLE001 - transacción revertida por nueva_sesion
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None


@router.post(
    "/users",
    status_code=201,
    summary="Crear un usuario y su primera clave API",
    description=(
        "Registra una identidad nueva y emite una clave API en una sola operación. "
        "La identidad puede ser un email o un identificador corto de depuración. "
        "La cuenta inicial es de PRUEBA salvo que se indique `account_type` (TESTING o NORMAL con `max_requests`); `plan` se acepta por compatibilidad, pero se ignora. "
        "La clave fija es opcional y, si se genera o se proporciona, su valor se devuelve únicamente "
        "en esta respuesta. El motivo debe tener entre 3 y 500 caracteres."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        201: {
            "description": "Usuario y clave creados. El valor de la clave solo se muestra en esta respuesta.",
            "content": {"application/json": {"examples": {"creado": {"summary": "Alta correcta", "value": {
                "success": True,
                "usuario": {"id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "email": "ana@ejemplo.com", "display_name": "Ana Pérez", "estado": "habilitado", "plan": "prueba", "account_type": "PRUEBA", "max_requests": 100, "motivo": "Alta solicitada por soporte"},
                "credenciales": {"clave": {"id": "0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e6f", "user_id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "prefijo": "mrk_demo", "scopes": [], "expira_en": "", "revocada": False, "emitida_en": "2026-10-12T16:30:00+00:00", "revelable": True}, "solicitado": False, "enviadas": False, "destino": "", "motivo": "no_solicitado", "valor_unica_vez": "mrk_valor_de_ejemplo"}
            }}}}},
        },
        400: {"description": "Identidad o motivo inválido, los alias `api_key` y `valor_fijo` discrepan, o se solicita email de credenciales sin una dirección con dominio. Corrija el dato señalado y reenvíe la solicitud."},
        409: {"description": "La identidad ya está registrada o el valor de clave elegido ya está activo. Use otra identidad o un valor de clave distinto."},
        **_RESPUESTA_422,
        **_RESPUESTA_503,
    },
)
async def crear_usuario(
    body: CrearUsuarioBody,
    response: Response,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Crea un usuario con su tipo de cuenta y emite su primera clave API.

    Acepta los nombres actuales y los alias de formularios anteriores. El valor
    de la clave solo se devuelve en esta respuesta y no debe registrarse ni
    reutilizarse en listados posteriores.
    """
    actor = require_admin(authorization)
    response.headers["Cache-Control"] = "private, no-store"
    motivo = validar_motivo(body.motivo, minimo=MOTIVO_MINIMO_IDENTIDAD)
    email = _normalizar_identidad(body.email)
    if any(u.email == email for u in USERS.values()):
        raise HTTPException(status_code=409, detail="identidad ya registrada")
    persisted_user_id = await _id_pg_por_email(email)
    api_key = body.api_key.strip()
    valor_fijo = body.valor_fijo.strip()
    if api_key and valor_fijo and api_key != valor_fijo:
        raise HTTPException(status_code=400, detail="api_key y valor_fijo no coinciden")
    api_key = api_key or valor_fijo
    enviar = body.enviar_credenciales
    if body.send_credentials is not None:
        enviar = body.send_credentials
    if body.send_api_key_email is not None:
        enviar = body.send_api_key_email
    estado = body.estado
    if body.habilitado is not None:
        estado = "habilitado" if body.habilitado else "deshabilitado"
    if enviar and ("@" not in email or "." not in email.rsplit("@", 1)[-1]):
        raise HTTPException(
            status_code=400,
            detail="enviar_credenciales requiere un email con dominio",
        )
    from central_api.billing.entitlements import normalize_assignment

    try:
        tipo_cuenta, cupo_cuenta = normalize_assignment(body.account_type, body.max_requests)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    usuario = AdminUser(
        id=persisted_user_id or _nuevo_id(),
        email=email, display_name=body.display_name,
        estado=estado, plan=tipo_cuenta.lower(), motivo=motivo,
    )
    USERS[usuario.id] = usuario
    meta = None
    try:
        meta, valor = emitir_clave(usuario.id, [], "", api_key)
        await _persistir_api_key_pg(usuario, meta, valor)
    except Exception:
        USERS.pop(usuario.id, None)
        if meta is not None:
            API_KEYS.pop(meta.id, None)
        raise

    if tipo_cuenta != "PRUEBA":
        # Todo usuario nace en PRUEBA; si se pidio otro tipo se asigna ahora.
        from central_api.billing.entitlements import assign_account

        try:
            cuenta = await assign_account(usuario.id, tipo_cuenta, max_requests=cupo_cuenta)
            usuario.account_type = cuenta.account_type
            usuario.max_requests = cuenta.max_requests
        except Exception:
            USERS.pop(usuario.id, None)
            if meta is not None:
                API_KEYS.pop(meta.id, None)
            raise

    credenciales = {
        "clave": _vista_clave(meta),
        "solicitado": enviar,
        "enviadas": False,
        "destino": email if enviar else "",
        "motivo": "no_solicitado",
        "valor_unica_vez": valor,
    }
    if enviar:
        entregadas, motivo_envio = await asyncio.to_thread(
            enviar_credenciales_email,
            email,
            valor,
            body.display_name.strip(),
        )
        credenciales.update(
            {
                "enviadas": entregadas,
                "motivo": motivo_envio,
                # El valor solo se devuelve a esta operación administrativa.
                # El panel lo mantiene en memoria temporal para copiarlo
                # aunque también se haya enviado por email. Nunca se incluye
                # en listados ni auditoría.
                "valor_unica_vez": valor,
            }
        )
    log_event(
        "user.created", actor_id=actor, target_type="user", target_id=usuario.id,
        request_id=request_id or "", reason=motivo,
        metadata={
            "email": email,
            "account_type": tipo_cuenta,
            "max_requests": cupo_cuenta,
            "estado": estado,
            "api_key_prefijo": meta.prefijo,
            "credenciales_solicitadas": enviar,
            "credenciales_enviadas": credenciales["enviadas"],
        },
    )
    return {
        "success": True,
        "usuario": _vista_usuario(usuario),
        "credenciales": credenciales,
    }


@router.get(
    "/users",
    summary="Listar y filtrar usuarios",
    description=(
        "Devuelve usuarios con sus metadatos de claves API, sin incluir valores secretos. "
        "Los filtros `email`, `estado` y `plan` se combinan; `limit` se acota a 1–200 y "
        "`offset` permite recorrer los resultados."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Página de usuarios y metadatos no secretos de sus claves.", "content": {"application/json": {"examples": {"lista": {"summary": "Página con resultados", "value": {"success": True, "total": 1, "usuarios": [{"id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "email": "ana@ejemplo.com", "display_name": "Ana Pérez", "estado": "habilitado", "plan": "prueba", "account_type": "PRUEBA", "max_requests": 100, "motivo": "", "claves_api": [{"id": "0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e6f", "prefijo": "mrk_demo", "estado": "activa", "emitida_en": "2026-10-12T16:30:00+00:00", "scopes": [], "expira_en": "", "revelable": True}]}]}}}}}},
        **_RESPUESTA_422,
        **_RESPUESTA_503,
    },
)
async def listar_usuarios(
    response: Response,
    authorization: str | None = Header(default=None),
    email: str = "",
    estado: str = "",
    plan: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Lista usuarios y metadatos de claves aplicando filtros y paginación."""
    require_admin(authorization)
    response.headers["Cache-Control"] = "private, no-store"
    filas_usuarios, filas_claves = await _filas_identidad_pg()
    cuentas_billing = await _cuentas_billing_pg()
    periodos_vigentes = await _periodos_vigentes_pg()
    from central_api.billing.entitlements import BILLING_ACCOUNTS
    usuarios_por_id = {user.id: user for user in USERS.values()}
    usuarios_pg: set[str] = set()
    claves_por_usuario: dict[str, dict[str, dict]] = {}
    ahora = utcnow()
    for user_id, email_pg, habilitado in filas_usuarios:
        uid = str(user_id)
        usuarios_pg.add(uid)
        usuario = usuarios_por_id.get(uid)
        if usuario is None:
            usuario = AdminUser(
                id=uid,
                email=str(email_pg),
                estado="habilitado" if habilitado else "deshabilitado",
                plan="prueba",
            )
            USERS[uid] = usuario
            usuarios_por_id[uid] = usuario
        else:
            # El estado persistido es canónico tras reinicios o cambios externos.
            usuario.estado = "habilitado" if habilitado else "deshabilitado"
        datos_cuenta = cuentas_billing.get(uid)
        if datos_cuenta is not None:
            (account_type, max_requests, starts_at,
             requests_reserved, requests_consumed) = datos_cuenta
            usuario.account_type = str(account_type)
            usuario.max_requests = max_requests
            usuario.plan = str(account_type).lower()
            from central_api.billing.entitlements import BillingAccount

            BILLING_ACCOUNTS[uid] = BillingAccount(
                user_id=uid,
                account_type=str(account_type),
                max_requests=max_requests,
                starts_at=starts_at,
                requests_reserved=int(requests_reserved or 0),
                requests_consumed=int(requests_consumed or 0),
            )
        claves_por_usuario[uid] = {}
    for key_id, user_id, prefix, scopes, expires_at, revoked_at, created_at, revelable in filas_claves:
        uid = str(user_id)
        expirada = expires_at is not None and expires_at <= ahora
        estado_clave = "revocada" if revoked_at is not None else ("expirada" if expirada else "activa")
        claves_por_usuario.setdefault(uid, {})[str(key_id)] = {
            "id": str(key_id),
            "prefijo": str(prefix),
            "estado": estado_clave,
            "emitida_en": created_at.isoformat() if created_at else "",
            "scopes": list(scopes or []),
            "expira_en": expires_at.isoformat() if expires_at else "",
            "revelable": bool(revelable),
        }
    for clave in API_KEYS.values():
        # PostgreSQL es canónico para los usuarios persistidos. Una clave que
        # solo viva en el cache no se puede rotar contra la base y, al figurar
        # como activa, hacía fallar el botón «Guardar» del panel con un 404.
        if clave.user_id in usuarios_pg:
            continue
        # PostgreSQL es la fuente canónica cuando la misma clave existe en
        # ambos stores. No sustituir su selector opaco por metadata de memoria.
        if clave.id in claves_por_usuario.get(clave.user_id, {}):
            continue
        claves_por_usuario.setdefault(clave.user_id, {})[clave.id] = {
            "id": clave.id,
            "prefijo": clave.prefijo,
            "estado": "revocada" if clave.revocada else "activa",
            "emitida_en": clave.emitida_en,
            "scopes": list(clave.scopes),
            "expira_en": clave.expira_en,
            "revelable": bool(clave.valor_cifrado),
        }
    usuarios = list(usuarios_por_id.values())
    if email:
        usuarios = [u for u in usuarios if email.lower() in u.email]
    if estado:
        usuarios = [u for u in usuarios if u.estado == estado]
    if plan:
        usuarios = [u for u in usuarios if u.plan == plan]
    total = len(usuarios)
    top = max(1, min(limit, 200))
    pagina = usuarios[max(0, offset) : max(0, offset) + top]
    return {
        "success": True,
        "total": total,
        "usuarios": [
            {
                **_vista_usuario(u),
                **_consumo_usuario(
                    u, BILLING_ACCOUNTS.get(u.id), periodos_vigentes.get(u.id)
                ),
                "claves_api": sorted(
                    claves_por_usuario.get(u.id, {}).values(),
                    key=lambda clave: clave["emitida_en"],
                    reverse=True,
                ),
            }
            for u in pagina
        ],
    }


@router.get(
    "/users/{user_id}",
    summary="Consultar un usuario",
    description=(
        "Obtiene el estado y los datos administrativos de un usuario junto con los metadatos "
        "de sus claves API. Nunca incluye el valor de una clave."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Datos del usuario y metadatos de sus claves.", "content": {"application/json": {"examples": {"usuario": {"summary": "Usuario encontrado", "value": {"success": True, "usuario": {"id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "email": "ana@ejemplo.com", "display_name": "Ana Pérez", "estado": "habilitado", "plan": "prueba", "account_type": "PRUEBA", "max_requests": 100, "motivo": ""}, "claves": [{"id": "0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e6f", "user_id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "prefijo": "mrk_demo", "scopes": [], "expira_en": "", "revocada": False, "emitida_en": "2026-10-12T16:30:00+00:00", "revelable": True}]}}}}}},
        404: {"description": "No existe un usuario con el identificador indicado. Compruebe el ID y consulte primero el listado."},
        **_RESPUESTA_503,
    },
)
async def ver_usuario(
    user_id: str,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict:
    """Devuelve los datos administrativos del usuario y sus claves no secretas."""
    require_admin(authorization)
    response.headers["Cache-Control"] = "private, no-store"
    usuario = USERS.get(user_id)
    if usuario is None:
        usuario = await _usuario_pg_por_id(user_id)
        if usuario is not None:
            USERS[user_id] = usuario
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    claves = [_vista_clave(k) for k in API_KEYS.values() if k.user_id == user_id]
    return {
        "success": True,
        "usuario": _vista_usuario(usuario),
        "claves": claves,
    }


async def _persistir_estado_pg(user_id: str, estado: str) -> None:
    """Mantiene ``users.habilitado`` y sus claves alineados con el panel."""
    from uuid import UUID

    from sqlalchemy import func, update

    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.identity import ApiKey, User

    if not db_configurado():
        return
    try:
        user_uuid = UUID(user_id)
    except (ValueError, TypeError, AttributeError):
        return
    async with nueva_sesion() as sesion:
        usuario_pg = await sesion.get(User, user_uuid)
        if usuario_pg is None:
            return
        usuario_pg.habilitado = estado == "habilitado"
        if estado == "deshabilitado":
            await sesion.execute(
                update(ApiKey)
                .where(ApiKey.user_id == user_uuid, ApiKey.revoked_at.is_(None))
                .values(revoked_at=func.current_timestamp())
            )


async def _cambiar_estado(
    user_id: str, estado: str, motivo_raw: str, actor: str, request_id: str,
    accion: str,
) -> dict:
    motivo = validar_motivo(motivo_raw, minimo=MOTIVO_MINIMO_IDENTIDAD)
    usuario = USERS.get(user_id)
    if usuario is None:
        usuario = await _usuario_pg_por_id(user_id)
        if usuario is not None:
            USERS[user_id] = usuario
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    try:
        await _persistir_estado_pg(user_id, estado)
    except Exception:  # noqa: BLE001 - no exponer conexión ni detalles internos
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    anterior = usuario.estado
    usuario.estado = estado
    usuario.motivo = motivo
    if estado == "deshabilitado":
        for clave in API_KEYS.values():
            if clave.user_id == user_id and not clave.revocada:
                clave.revocada = True
    log_event(
        accion, actor_id=actor, target_type="user", target_id=user_id,
        request_id=request_id, reason=motivo,
        metadata={"anterior": anterior, "nuevo": estado},
    )
    return {"success": True, "usuario": _vista_usuario(usuario)}


@router.post(
    "/users/{user_id}/enable",
    summary="Habilitar un usuario",
    description="Habilita el acceso de la cuenta indicada y registra la justificación en la auditoría. El motivo debe tener entre 3 y 500 caracteres.",
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Cuenta habilitada.", "content": {"application/json": {"examples": {"habilitado": {"summary": "Cambio aplicado", "value": {"success": True, "usuario": {"id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "email": "ana@ejemplo.com", "display_name": "Ana Pérez", "estado": "habilitado", "plan": "prueba", "account_type": "PRUEBA", "max_requests": 100, "motivo": "Acceso restablecido"}}}}}}},
        400: {"description": "El motivo falta o no tiene entre 3 y 500 caracteres. Envíe una justificación válida."},
        404: {"description": "No se encontró el usuario. Compruebe el identificador antes de reintentar."},
        **_RESPUESTA_422,
        **_RESPUESTA_503,
    },
)
async def habilitar_usuario(
    user_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Habilita la cuenta indicada y registra la operación administrativa."""
    actor = require_admin(authorization)
    return await _cambiar_estado(
        user_id, "habilitado", body.motivo, actor, request_id or "", "user.enabled"
    )


@router.post(
    "/users/{user_id}/disable",
    summary="Deshabilitar un usuario",
    description=(
        "Deshabilita la cuenta, revoca sus claves activas e impide que se inicien nuevos trabajos. "
        "La revocación no se revierte automáticamente al volver a habilitar la cuenta. "
        "El motivo debe tener entre 3 y 500 caracteres."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Cuenta deshabilitada y claves activas revocadas.", "content": {"application/json": {"examples": {"deshabilitado": {"summary": "Cambio aplicado", "value": {"success": True, "usuario": {"id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "email": "ana@ejemplo.com", "display_name": "Ana Pérez", "estado": "deshabilitado", "plan": "prueba", "account_type": "PRUEBA", "max_requests": 100, "motivo": "Baja solicitada por soporte"}}}}}}},
        400: {"description": "El motivo falta o no tiene entre 3 y 500 caracteres. Envíe una justificación válida."},
        404: {"description": "No se encontró el usuario. Compruebe el identificador antes de reintentar."},
        **_RESPUESTA_422,
        **_RESPUESTA_503,
    },
)
async def deshabilitar_usuario(
    user_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Deshabilita la cuenta, revoca sus claves activas y registra el cambio."""
    actor = require_admin(authorization)
    return await _cambiar_estado(
        user_id, "deshabilitado", body.motivo, actor, request_id or "", "user.disabled"
    )


async def _bulk_request_values(request) -> tuple[str, list[str], str]:
    """Lee la forma V1 urlencoded y la forma JSON del panel sin revelar datos."""
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            payload = await request.json()
        except ValueError:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        raw_ids = payload.get("user_ids", [])
        ids = [str(value) for value in raw_ids] if isinstance(raw_ids, list) else []
        return str(payload.get("action", "")), ids, str(payload.get("motivo", ""))
    try:
        form = await request.form()
    except Exception:
        try:
            parsed = parse_qs((await request.body()).decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return "", [], ""
        return (
            parsed.get("action", [""])[0],
            parsed.get("user_ids", []),
            parsed.get("motivo", [""])[0],
        )
    return str(form.get("action", "")), [str(value) for value in form.getlist("user_ids")], str(form.get("motivo", ""))


@router.post(
    "/users/bulk",
    summary="Habilitar o deshabilitar usuarios en lote",
    description=(
        "Aplica la acción `enable` o `disable` a una selección de hasta 200 IDs únicos. "
        "Acepta JSON o formulario. Los IDs inexistentes se devuelven en `missing` y no "
        "detienen el procesamiento de los demás usuarios. Si se omite el motivo, se registra "
        "una justificación predeterminada de la operación masiva."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Resultado agregado de la operación; incluye IDs actualizados y no encontrados.", "content": {"application/json": {"examples": {"resultado": {"summary": "Operación parcial", "value": {"success": True, "action": "disable", "updated": ["e7f291a4-1a3d-4a16-8a65-cc9142a5812b"], "missing": ["no-existe"], "count": 1}}}}}},
        400: {"description": "No se seleccionaron usuarios, la acción no es `enable`/`disable` o hay más de 200 IDs. Envíe una acción admitida y una selección de 1 a 200 usuarios."},
        **_RESPUESTA_503,
    },
)
async def bulk_users(
    request: Request,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Aplica una acción de estado a varios usuarios y devuelve el resultado por ID."""
    actor = require_admin(authorization)
    action, user_ids, motivo_raw = await _bulk_request_values(request)
    user_ids = list(dict.fromkeys(user_id.strip() for user_id in user_ids if user_id.strip()))
    if not user_ids:
        raise HTTPException(status_code=400, detail="No seleccionaste usuarios.")
    if action not in {"enable", "disable"}:
        raise HTTPException(status_code=400, detail="Acción inválida.")
    if len(user_ids) > 200:
        raise HTTPException(status_code=400, detail="Máximo 200 usuarios por operación.")
    motivo = motivo_raw or f"operación masiva V1: {action}"
    estado = "habilitado" if action == "enable" else "deshabilitado"
    updated, missing = [], []
    for user_id in user_ids:
        try:
            result = await _cambiar_estado(
                user_id, estado, motivo, actor, request_id or "", f"user.bulk_{action}"
            )
        except HTTPException as exc:
            if exc.status_code == 404:
                missing.append(user_id)
                continue
            raise
        updated.append(result["usuario"]["id"])
    return {
        "success": True,
        "action": action,
        "updated": updated,
        "missing": missing,
        "count": len(updated),
    }


async def _get_admin_user(user_id: str) -> AdminUser:
    usuario = USERS.get(user_id)
    if usuario is None:
        usuario = await _usuario_pg_por_id(user_id)
        if usuario is not None:
            USERS[user_id] = usuario
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    return usuario


@router.post(
    "/users/{user_id}/monthly-limit",
    summary="Actualizar el límite mensual de consultas",
    description=(
        "Asigna al usuario una cuenta NORMAL con el límite mensual indicado. "
        "El límite admite valores enteros de 1 a 10 000 000. Acepta JSON o formulario; "
        "se admiten los nombres `maximas_consultas_mensuales` y `max_requests`. "
        "Si la cuenta ya era NORMAL, se conserva el consumo del mes en curso."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Cuenta NORMAL actualizada con el nuevo límite.", "content": {"application/json": {"examples": {"actualizado": {"summary": "Límite aplicado", "value": {"success": True, "usuario": {"id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "email": "ana@ejemplo.com", "display_name": "Ana Pérez", "estado": "habilitado", "plan": "normal", "account_type": "NORMAL", "max_requests": 2500, "motivo": ""}, "maximas_consultas_mensuales": 2500}}}}}},
        400: {"description": "El límite no es un entero o está fuera del rango 1–10 000 000. Envíe un valor válido."},
        404: {"description": "No se encontró el usuario indicado. Compruebe su ID."},
        422: {"description": "El tipo de cuenta o el límite no superó la validación de asignación. Envíe un límite entre 1 y 10 000 000 y vuelva a solicitar el cambio a cuenta NORMAL."},
        **_RESPUESTA_503,
    },
)
async def actualizar_limite_mensual(
    user_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Asigna una cuenta NORMAL con el cupo mensual solicitado."""
    actor = require_admin(authorization)
    content_type = request.headers.get("content-type", "")
    try:
        payload = await request.json() if "application/json" in content_type else await request.form()
    except Exception:
        try:
            payload = {
                key: values[0] if len(values) == 1 else values
                for key, values in parse_qs((await request.body()).decode("utf-8")).items()
            }
        except (UnicodeDecodeError, ValueError):
            payload = {}
    raw_limit = payload.get("maximas_consultas_mensuales", payload.get("max_requests", None))
    try:
        limit = int(raw_limit)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="El límite mensual debe ser un entero.") from None
    if not 1 <= limit <= 10_000_000:
        raise HTTPException(status_code=400, detail="El límite mensual debe estar entre 1 y 10000000.")
    usuario = await _get_admin_user(user_id)
    motivo = str(payload.get("motivo", "") or "límite mensual V1")
    motivo = validar_motivo(motivo, minimo=MOTIVO_MINIMO_IDENTIDAD)
    from central_api.billing.entitlements import assign_account

    try:
        account = await assign_account(user_id, "NORMAL", max_requests=limit)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    usuario.account_type = account.account_type
    usuario.max_requests = limit
    usuario.plan = "normal"
    log_event(
        "user.monthly_limit.updated",
        actor_id=actor,
        target_type="user",
        target_id=user_id,
        request_id=request_id or "",
        reason=motivo,
        metadata={"max_requests": limit, "account_type": "NORMAL"},
    )
    return {"success": True, "usuario": _vista_usuario(usuario), "maximas_consultas_mensuales": limit}


@router.post(
    "/users/{user_id}/api-keys",
    status_code=201,
    summary="Emitir una clave API",
    description=(
        "Crea una clave para el usuario indicado y devuelve su valor en claro una sola vez. "
        "El valor no se incluye en listados ni en la auditoría. Se puede proporcionar `valor_fijo` "
        "o dejarlo vacío para generar uno automáticamente."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        201: {"description": "Clave emitida; copie y guarde `valor_unica_vez`, ya que no se mostrará en listados.", "content": {"application/json": {"examples": {"emitida": {"summary": "Clave creada", "value": {"success": True, "clave": {"id": "0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e6f", "user_id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "prefijo": "mrk_demo", "scopes": ["jobs:read"], "expira_en": "", "revocada": False, "emitida_en": "2026-10-12T16:30:00+00:00", "revelable": True}, "valor_unica_vez": "mrk_valor_de_ejemplo", "aviso": "Consérvela ahora: no volverá a mostrarse ni quedará en caché."}}}}}},
        400: {"description": "El motivo no cumple la longitud requerida o el valor fijo tiene menos de 3 caracteres o contiene espacios. Corrija esos campos y reenvíe la solicitud."},
        404: {"description": "No se encontró el usuario propietario. Compruebe su ID."},
        409: {"description": "El valor fijo ya está registrado en otra clave activa. Elija un valor distinto o solicite una clave aleatoria."},
        **_RESPUESTA_422,
        **_RESPUESTA_503,
    },
)
async def emitir_clave_api(
    user_id: str,
    body: EmitirClaveBody,
    response: Response,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Emite una clave para el usuario y revela su valor únicamente en esta respuesta."""
    actor = require_admin(authorization)
    response.headers["Cache-Control"] = "private, no-store"
    motivo = validar_motivo(body.motivo, minimo=MOTIVO_MINIMO_IDENTIDAD)
    usuario = USERS.get(user_id)
    if usuario is None:
        usuario = await _usuario_pg_por_id(user_id)
        if usuario is not None:
            USERS[user_id] = usuario
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    meta, valor = emitir_clave(
        user_id, body.scopes, body.expira_en, body.valor_fijo
    )
    try:
        await _persistir_api_key_pg(usuario, meta, valor)
    except Exception:
        API_KEYS.pop(meta.id, None)
        raise
    log_event(
        "user.api_key.issued", actor_id=actor, target_type="api_key",
        target_id=meta.id, request_id=request_id or "", reason=motivo,
        metadata={"user_id": user_id, "prefijo": meta.prefijo, "scopes": body.scopes},
    )
    return {
        "success": True,
        "clave": _vista_clave(meta),
        "valor_unica_vez": valor,
        "aviso": "Consérvela ahora: no volverá a mostrarse ni quedará en caché.",
    }


@router.post(
    "/users/{user_id}/api-keys/{key_id}/reveal",
    summary="Revelar una clave API recuperable",
    description=(
        "Devuelve el valor de una clave existente solo si pertenece al usuario indicado, "
        "la cuenta está habilitada y la clave no fue revocada ni venció. Las claves emitidas "
        "antes de que pudiera recuperarse su valor deben reemplazarse mediante una nueva emisión."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Valor de la clave recuperado. La respuesta es privada y no se almacena en caché.", "content": {"application/json": {"examples": {"revelada": {"summary": "Clave disponible", "value": {"success": True, "api_key": "mrk_valor_de_ejemplo"}}}}}},
        404: {"description": "No se encontró la clave para el usuario indicado. Compruebe ambos identificadores y que la clave pertenece a esa cuenta."},
        409: {"description": "La cuenta o la clave no está activa, o la clave no conserva un valor recuperable. Habilite la cuenta si procede; si la clave no es recuperable, emita una nueva."},
        **_RESPUESTA_503,
    },
)
async def revelar_clave_api(
    user_id: str,
    key_id: str,
    response: Response,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Recupera el valor de una clave vigente y perteneciente al usuario indicado."""
    actor = require_admin(authorization)
    response.headers["Cache-Control"] = "private, no-store"
    from central_api.db import db_configurado, nueva_sesion

    if db_configurado():
        from sqlalchemy import select

        from central_api.models.identity import ApiKey, User

        try:
            key_uuid = uuid.UUID(key_id)
            user_uuid = uuid.UUID(user_id)
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(status_code=404, detail="clave no encontrada") from None
        try:
            async with nueva_sesion() as sesion:
                fila = (
                    await sesion.execute(
                        select(ApiKey, User.habilitado)
                        .join(User, User.id == ApiKey.user_id)
                        .where(ApiKey.id == key_uuid, ApiKey.user_id == user_uuid)
                    )
                ).first()
        except Exception:  # noqa: BLE001 - error DB sanitizado
            raise HTTPException(status_code=503, detail="Servicio no disponible") from None
        if fila is None:
            raise HTTPException(status_code=404, detail="clave no encontrada")
        row, usuario_habilitado = fila
        if (
            not usuario_habilitado
            or row.revoked_at is not None
            or (row.expires_at is not None and row.expires_at <= utcnow())
        ):
            raise HTTPException(status_code=409, detail="la clave no está activa")
        ciphertext = row.encrypted_value
        verifier = row.verifier_hmac
        persistent = True
    else:
        usuario = USERS.get(user_id)
        meta = API_KEYS.get(key_id)
        if usuario is None or meta is None or meta.user_id != user_id:
            raise HTTPException(status_code=404, detail="clave no encontrada")
        if usuario.estado != "habilitado" or meta.revocada:
            raise HTTPException(status_code=409, detail="la clave no está activa")
        if meta.expira_en:
            try:
                expires_at = datetime.fromisoformat(meta.expira_en.replace("Z", "+00:00"))
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
            except ValueError:
                raise HTTPException(status_code=409, detail="la clave no está activa") from None
            if expires_at <= utcnow():
                raise HTTPException(status_code=409, detail="la clave no está activa")
        ciphertext = meta.valor_cifrado
        verifier = meta.verificador_hmac
        persistent = False

    if not ciphertext:
        raise HTTPException(
            status_code=409,
            detail="esta clave fue emitida antes del cifrado recuperable; emita una nueva",
        )
    try:
        from central_api.security.api_key_vault import decrypt_api_key

        api_key = decrypt_api_key(ciphertext)
        if persistent:
            from central_api.api.dependencies import _selector_y_secreto
            from central_api.security.api_keys import fingerprint_secret

            hmac_secret = get_settings().api_key_hmac_secret
            if not hmac_secret:
                raise ValueError("HMAC de API keys no configurado")
            _, secret = _selector_y_secreto(api_key)
            actual_verifier = fingerprint_secret(hmac_secret, secret)
        else:
            actual_verifier = _firmar_clave(api_key)
    except Exception:  # noqa: BLE001 - no exponer errores ni secretos criptográficos
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    if not hmac.compare_digest(actual_verifier, verifier):
        raise HTTPException(status_code=503, detail="Servicio no disponible")

    log_event(
        "user.api_key.revealed",
        actor_id=actor,
        target_type="api_key",
        target_id=key_id,
        request_id=request_id or "",
        metadata={"user_id": user_id},
    )
    return {"success": True, "api_key": api_key}


@router.post(
    "/users/{user_id}/api-keys/rotate",
    summary="Rotar una clave API",
    description=(
        "Emite una clave nueva con los permisos y vencimiento de la clave anterior y revoca esta última "
        "en la misma operación. El campo `periodo_gracia_horas` se conserva por compatibilidad y "
        "se registra en auditoría, pero no demora la revocación. El nuevo valor solo se devuelve aquí."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Clave nueva emitida y clave anterior revocada.", "content": {"application/json": {"examples": {"rotada": {"summary": "Rotación completada", "value": {"success": True, "clave": {"id": "0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e70", "user_id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "prefijo": "clave-nu", "scopes": ["jobs:read"], "expira_en": "", "revocada": False, "emitida_en": "2026-10-12T16:30:00+00:00", "revelable": True}, "valor_unica_vez": "clave-nueva-de-ejemplo", "revocada": "0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e6f"}}}}}},
        400: {"description": "El motivo no cumple la longitud requerida o el valor fijo nuevo tiene menos de 3 caracteres o contiene espacios. Corrija el campo antes de reintentar."},
        404: {"description": "No se encontró el usuario o la clave anterior no pertenece a esa cuenta. Compruebe los identificadores."},
        409: {"description": "La clave anterior está revocada o el valor de la clave nueva ya está activo. Seleccione una clave anterior activa o use un valor nuevo."},
        **_RESPUESTA_422,
        **_RESPUESTA_503,
    },
)
async def rotar_clave_api(
    user_id: str,
    body: RotarClaveBody,
    response: Response,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Emite la clave sustituta y revoca la clave anterior de forma conjunta."""
    actor = require_admin(authorization)
    response.headers["Cache-Control"] = "private, no-store"
    motivo = validar_motivo(body.motivo, minimo=MOTIVO_MINIMO_IDENTIDAD)
    usuario = USERS.get(user_id)
    if usuario is None:
        usuario = await _usuario_pg_por_id(user_id)
        if usuario is not None:
            USERS[user_id] = usuario
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    anterior = None
    from central_api.db import db_configurado

    if db_configurado():
        # PostgreSQL manda: si el ID enviado por el panel no está persistido
        # pero sí en el cache, se rota la clave activa real del usuario en vez
        # de devolver 404 (clave fantasma de una lista desactualizada).
        anterior = await _api_key_pg_por_id(user_id, body.key_id)
        if anterior is not None:
            API_KEYS[body.key_id] = anterior
        else:
            en_memoria = API_KEYS.get(body.key_id)
            if en_memoria is not None and en_memoria.user_id == user_id:
                anterior = await _clave_activa_pg_por_usuario(user_id)
                if anterior is not None:
                    API_KEYS[anterior.id] = anterior
    else:
        anterior = API_KEYS.get(body.key_id)
    if anterior is None or anterior.user_id != user_id:
        raise HTTPException(status_code=404, detail="clave anterior no encontrada")
    if anterior.revocada:
        raise HTTPException(status_code=409, detail="la clave anterior no está activa")
    # Emitir primero permite validar la clave nueva antes de revocar la actual.
    # La operación queda en un único endpoint para que el panel no deje dos
    # claves activas por un fallo entre requests.
    # El campo vacío es una solicitud de generación, no un valor de clave
    # inválido. El secreto se devuelve únicamente en esta respuesta no-cache.
    valor_fijo = body.valor_fijo.strip() or _nueva_clave_alfanumerica()
    meta, valor = emitir_clave(
        user_id, anterior.scopes, anterior.expira_en, valor_fijo, reemplaza=anterior.id
    )
    try:
        await _rotar_api_key_pg(usuario, anterior, meta, valor)
    except Exception:
        API_KEYS.pop(meta.id, None)
        raise
    anterior.revocada = True
    log_event(
        "user.api_key.rotated", actor_id=actor, target_type="api_key",
        target_id=meta.id, request_id=request_id or "", reason=motivo,
        metadata={
            "user_id": user_id,
            "clave_anterior": enmascarar(anterior.id),
            "clave_nueva": enmascarar(meta.id),
            "periodo_gracia_horas": body.periodo_gracia_horas,
        },
    )
    return {
        "success": True,
        "clave": _vista_clave(meta),
        "valor_unica_vez": valor,
        "revocada": anterior.id,
    }


class EditarClaveBody(BaseModel):
    scopes: list[str] | None = Field(
        default=None,
        description="Nuevos permisos; si se omite, se conservan los actuales.",
        examples=[["jobs:read"]],
    )
    expira_en: str | None = Field(
        default=None,
        description="Nueva fecha ISO 8601 o cadena vacía para quitar el vencimiento; si se omite, no cambia.",
        examples=["2027-12-31T23:59:59Z"],
    )
    revocada: bool | None = Field(
        default=None,
        description="Nuevo estado de revocación; si se omite, no cambia.",
        examples=[False],
    )
    motivo: str = Field(
        default="",
        description="Justificación obligatoria de 3 a 500 caracteres.",
        examples=["Ajuste solicitado por soporte"],
    )


def _estado_clave(meta: ApiKeyMeta) -> str:
    return "revocada" if meta.revocada else "activa"


def _vista_clave_admin(meta: ApiKeyMeta) -> dict:
    vista = _vista_clave(meta)
    usuario = USERS.get(meta.user_id)
    vista["usuario_email"] = usuario.email if usuario else ""
    vista["estado"] = _estado_clave(meta)
    return vista


async def _meta_clave(key_id: str) -> ApiKeyMeta:
    """Clave del cache o, si no está, hidratada desde PostgreSQL; 404 si no existe."""
    meta = API_KEYS.get(key_id)
    if meta is not None:
        return meta
    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.identity import ApiKey

    if db_configurado():
        try:
            key_uuid = uuid.UUID(key_id)
        except (ValueError, TypeError, AttributeError):
            key_uuid = None
        if key_uuid is not None:
            try:
                async with nueva_sesion() as sesion:
                    row = await sesion.get(ApiKey, key_uuid)
            except Exception:  # noqa: BLE001 - error DB sanitizado
                raise HTTPException(status_code=503, detail="Servicio no disponible") from None
            if row is not None:
                meta = await _api_key_pg_por_id(str(row.user_id), key_id)
                if meta is not None:
                    API_KEYS[key_id] = meta
                    if meta.user_id not in USERS:
                        usuario = await _usuario_pg_por_id(meta.user_id)
                        if usuario is not None:
                            USERS[meta.user_id] = usuario
                    return meta
    raise HTTPException(status_code=404, detail="clave no encontrada")


def _parse_expira(valor: str) -> datetime | None:
    if not valor:
        return None
    try:
        fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="expira_en inválida") from None
    return fecha if fecha.tzinfo else fecha.replace(tzinfo=timezone.utc)


async def _actualizar_clave_pg(
    key_id: str,
    *,
    revocada: bool | None = None,
    scopes: list[str] | None = None,
    expira_en: str | None = None,
) -> None:
    """Aplica en PostgreSQL el cambio de la clave; sin base es un no-op.

    La autenticación lee ``revoked_at``, ``expires_at`` y ``scopes`` de la
    fila: si el cambio quedara solo en memoria, una clave revocada seguiría
    autenticando. Restaurar falla con 409 si la clave fue reemplazada, porque
    habría dos claves activas para el mismo reemplazo.
    """
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from central_api.db import db_configurado, nueva_sesion
    from central_api.models.identity import ApiKey

    if not db_configurado():
        return
    try:
        key_uuid = uuid.UUID(key_id)
    except (ValueError, TypeError, AttributeError):
        return
    expires_at = _parse_expira(expira_en) if expira_en else None
    try:
        async with nueva_sesion() as sesion:
            row = (
                await sesion.execute(
                    select(ApiKey).where(ApiKey.id == key_uuid).with_for_update()
                )
            ).scalar_one_or_none()
            if row is None:
                return
            if revocada is True and row.revoked_at is None:
                row.revoked_at = utcnow()
            elif revocada is False and row.revoked_at is not None:
                reemplazo = (
                    await sesion.execute(
                        select(ApiKey.id).where(ApiKey.replaces_key_id == key_uuid)
                    )
                ).first()
                if reemplazo is not None:
                    raise HTTPException(
                        status_code=409,
                        detail="la clave fue reemplazada; no se puede restaurar",
                    )
                row.revoked_at = None
            if scopes is not None:
                row.scopes = list(scopes)
            if expira_en is not None:
                row.expires_at = expires_at
            await sesion.flush()
    except HTTPException:
        raise
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail="el valor de esta clave ya está activo en otra clave",
        ) from None
    except Exception:  # noqa: BLE001 - transacción revertida por nueva_sesion
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None


@router.get(
    "/api-keys",
    summary="Listar y filtrar claves API",
    description=(
        "Devuelve metadatos administrativos de claves, nunca sus valores. Permite filtrar por "
        "usuario, estado y texto coincidente con prefijo, email o permisos; `limit` se acota a 1–200."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Página de claves con metadatos y datos de su usuario propietario.", "content": {"application/json": {"examples": {"lista": {"summary": "Claves encontradas", "value": {"success": True, "total": 1, "claves": [{"id": "0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e6f", "user_id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "prefijo": "mrk_demo", "scopes": ["jobs:read"], "expira_en": "", "revocada": False, "emitida_en": "2026-10-12T16:30:00+00:00", "revelable": True, "usuario_email": "ana@ejemplo.com", "estado": "activa", "owner_enabled": True}]}}}}}},
        **_RESPUESTA_422,
        **_RESPUESTA_503,
    },
)
async def listar_claves_api(
    response: Response,
    authorization: str | None = Header(default=None),
    user_id: str = "",
    estado: str = "",
    q: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Lista metadatos no secretos de claves y aplica filtros con paginación."""
    require_admin(authorization)
    response.headers["Cache-Control"] = "private, no-store"
    from central_api.db import db_configurado

    vistas: list[dict] = []
    if db_configurado():
        filas_usuarios, filas_claves = await _filas_identidad_pg()
        usuarios_pg = {
            str(uid): (str(email), bool(habilitado))
            for uid, email, habilitado in filas_usuarios
        }
        ahora = utcnow()
        for (
            key_id,
            owner_id,
            prefix,
            scopes,
            expires_at,
            revoked_at,
            created_at,
            revelable,
        ) in filas_claves:
            owner_id_str = str(owner_id)
            if user_id and owner_id_str != user_id:
                continue
            expirada = expires_at is not None and expires_at <= ahora
            estado_clave = (
                "revocada" if revoked_at is not None else "expirada" if expirada else "activa"
            )
            if estado and estado != estado_clave:
                continue
            email_owner, owner_enabled = usuarios_pg.get(owner_id_str, ("", False))
            scopes_lista = list(scopes or [])
            if q.strip():
                termino = q.strip().lower()
                if not (
                    termino in str(prefix).lower()
                    or termino in email_owner.lower()
                    or any(termino in scope.lower() for scope in scopes_lista)
                ):
                    continue
            vistas.append(
                {
                    "id": str(key_id),
                    "user_id": owner_id_str,
                    "prefijo": str(prefix),
                    "scopes": scopes_lista,
                    "expira_en": expires_at.isoformat() if expires_at else "",
                    "revocada": revoked_at is not None,
                    "emitida_en": created_at.isoformat() if created_at else "",
                    "revelable": bool(revelable),
                    "usuario_email": email_owner,
                    "estado": estado_clave,
                    "owner_enabled": owner_enabled,
                }
            )
    else:
        claves = list(API_KEYS.values())
        if user_id:
            claves = [k for k in claves if k.user_id == user_id]
        if estado in ("activa", "revocada"):
            claves = [k for k in claves if _estado_clave(k) == estado]
        if q.strip():
            termino = q.strip().lower()
            claves = [
                k for k in claves
                if termino in k.prefijo.lower()
                or termino in (USERS.get(k.user_id).email.lower() if USERS.get(k.user_id) else "")
                or any(termino in s.lower() for s in k.scopes)
            ]
        vistas = []
        for clave in claves:
            vista = _vista_clave_admin(clave)
            usuario = USERS.get(clave.user_id)
            vista["owner_enabled"] = bool(usuario and usuario.estado == "habilitado")
            vistas.append(vista)
    total = len(vistas)
    top = max(1, min(limit, 200))
    pagina = vistas[max(0, offset): max(0, offset) + top]
    return {
        "success": True,
        "total": total,
        "claves": pagina,
    }


@router.patch(
    "/api-keys/{key_id}",
    summary="Editar una clave API",
    description=(
        "Actualiza uno o más de estos datos: permisos, vencimiento y estado de revocación. "
        "Los campos omitidos se conservan. Cada cambio requiere un motivo de 3 a 500 caracteres."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Metadatos actualizados de la clave.", "content": {"application/json": {"examples": {"actualizada": {"summary": "Cambios aplicados", "value": {"success": True, "clave": {"id": "0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e6f", "user_id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "prefijo": "mrk_demo", "scopes": ["jobs:read"], "expira_en": "2027-12-31T23:59:59+00:00", "revocada": False, "emitida_en": "2026-10-12T16:30:00+00:00", "revelable": True, "usuario_email": "ana@ejemplo.com", "estado": "activa"}}}}}}},
        400: {"description": "El motivo es obligatorio y debe tener entre 3 y 500 caracteres, o `expira_en` no es una fecha ISO 8601 válida. Corrija esos valores."},
        404: {"description": "No existe la clave indicada. Compruebe el identificador."},
        409: {"description": "No se puede volver a activar una clave ya reemplazada, o su valor ya está activo en otra clave. Mantenga la clave sustituta vigente o evite duplicar el valor."},
        **_RESPUESTA_422,
        **_RESPUESTA_503,
    },
)
async def editar_clave_api(
    key_id: str,
    body: EditarClaveBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Actualiza permisos, vencimiento o revocación y registra el cambio."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo, minimo=MOTIVO_MINIMO_IDENTIDAD)
    meta = await _meta_clave(key_id)
    anterior = {
        "scopes": list(meta.scopes),
        "expira_en": meta.expira_en,
        "revocada": meta.revocada,
    }
    await _actualizar_clave_pg(
        key_id,
        revocada=body.revocada,
        scopes=body.scopes,
        expira_en=body.expira_en,
    )
    if body.scopes is not None:
        meta.scopes = list(body.scopes)
    if body.expira_en is not None:
        meta.expira_en = body.expira_en
    if body.revocada is not None:
        meta.revocada = bool(body.revocada)
    log_event(
        "user.api_key.updated", actor_id=actor, target_type="api_key",
        target_id=meta.id, request_id=request_id or "", reason=motivo,
        metadata={"anterior": anterior, "nuevo": {
            "scopes": list(meta.scopes),
            "expira_en": meta.expira_en,
            "revocada": meta.revocada,
        }},
    )
    return {"success": True, "clave": _vista_clave_admin(meta)}


@router.post(
    "/api-keys/{key_id}/revoke",
    summary="Revocar una clave API",
    description="Revoca la clave indicada sin eliminar su registro y guarda el motivo en la auditoría. El motivo debe tener entre 3 y 500 caracteres.",
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Clave revocada.", "content": {"application/json": {"examples": {"revocada": {"summary": "Revocación completada", "value": {"success": True, "clave": {"id": "0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e6f", "user_id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "prefijo": "mrk_demo", "scopes": [], "expira_en": "", "revocada": True, "emitida_en": "2026-10-12T16:30:00+00:00", "revelable": True, "usuario_email": "ana@ejemplo.com", "estado": "revocada"}}}}}}},
        400: {"description": "El motivo falta o no tiene entre 3 y 500 caracteres. Envíe una justificación válida."},
        404: {"description": "No existe la clave indicada. Compruebe el identificador."},
        **_RESPUESTA_422,
        **_RESPUESTA_503,
    },
)
async def revocar_clave_api(
    key_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Revoca la clave sin eliminar su registro histórico."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo, minimo=MOTIVO_MINIMO_IDENTIDAD)
    meta = await _meta_clave(key_id)
    await _actualizar_clave_pg(key_id, revocada=True)
    meta.revocada = True
    log_event(
        "user.api_key.revoked", actor_id=actor, target_type="api_key",
        target_id=meta.id, request_id=request_id or "", reason=motivo,
        metadata={"user_id": meta.user_id, "prefijo": meta.prefijo},
    )
    return {"success": True, "clave": _vista_clave_admin(meta)}


@router.post(
    "/api-keys/{key_id}/restore",
    summary="Restaurar una clave API revocada",
    description=(
        "Quita la revocación de la clave y registra la justificación. Solo se puede restaurar "
        "si no fue reemplazada y su valor no está activo en otra clave. El motivo debe tener "
        "entre 3 y 500 caracteres."
    ),
    responses={
        **_RESPUESTAS_ADMIN,
        200: {"description": "Clave restaurada.", "content": {"application/json": {"examples": {"restaurada": {"summary": "Restauración completada", "value": {"success": True, "clave": {"id": "0194f8b2-8c4d-7a12-9b34-1a2b3c4d5e6f", "user_id": "e7f291a4-1a3d-4a16-8a65-cc9142a5812b", "prefijo": "mrk_demo", "scopes": ["jobs:read"], "expira_en": "", "revocada": False, "emitida_en": "2026-10-12T16:30:00+00:00", "revelable": True, "usuario_email": "ana@ejemplo.com", "estado": "activa"}}}}}}},
        400: {"description": "El motivo falta o no tiene entre 3 y 500 caracteres. Envíe una justificación válida."},
        404: {"description": "No existe la clave indicada. Compruebe el identificador."},
        409: {"description": "La clave fue reemplazada o su valor ya está activo en otra clave. No se puede restaurar hasta resolver el conflicto; utilice la clave vigente."},
        **_RESPUESTA_422,
        **_RESPUESTA_503,
    },
)
async def restaurar_clave_api(
    key_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Restaura una clave revocada cuando no existe una clave sustituta activa."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo, minimo=MOTIVO_MINIMO_IDENTIDAD)
    meta = await _meta_clave(key_id)
    if meta.revocada and any(
        k.id != meta.id and not k.revocada and k.verificador_hmac == meta.verificador_hmac
        for k in API_KEYS.values()
    ):
        raise HTTPException(
            status_code=409,
            detail="el valor de esta clave ya está activo en otra clave",
        )
    await _actualizar_clave_pg(key_id, revocada=False)
    meta.revocada = False
    log_event(
        "user.api_key.restored", actor_id=actor, target_type="api_key",
        target_id=meta.id, request_id=request_id or "", reason=motivo,
        metadata={"user_id": meta.user_id, "prefijo": meta.prefijo},
    )
    return {"success": True, "clave": _vista_clave_admin(meta)}
