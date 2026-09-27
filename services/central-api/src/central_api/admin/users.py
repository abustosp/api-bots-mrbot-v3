"""Gestión de usuarios del panel: alta, baja lógica, claves API y créditos.

- ``users.id`` es UUIDv4 (invariante I-1); la auditoría usa UUIDv7.
- La clave API se muestra UNA sola vez al emitirla; en el store solo quedan
  el prefijo y el verificador HMAC, nunca el valor.
- Los ajustes de crédito son entradas compensatorias del ledger con motivo
  obligatorio; nunca una actualización opaca del saldo.
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

from fastapi import APIRouter, Header, HTTPException, Response
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


@dataclass
class AdminUser:
    """Identidad de cliente administrada por el panel."""

    id: str
    email: str
    display_name: str = ""
    estado: str = "habilitado"
    plan: str = "free"
    saldo_creditos: int | None = 0
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


@dataclass
class CreditEntry:
    """Entrada compensatoria del ledger de créditos (append-only)."""

    id: str
    user_id: str
    delta: int
    saldo_previo: int
    saldo_posterior: int
    motivo: str
    actor: str
    creada_en: str


USERS: dict[str, AdminUser] = {}
API_KEYS: dict[str, ApiKeyMeta] = {}
CREDIT_LEDGER: list[CreditEntry] = []


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


def aplicar_credito(
    user_id: str, delta: int, motivo: str, actor: str
) -> CreditEntry:
    """Agrega una entrada compensatoria y actualiza el saldo derivado."""
    usuario = USERS.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    # Los usuarios hidratados desde PostgreSQL llegan con saldo desconocido
    # (None): el libro del panel arranca en 0 en vez de fallar con 500.
    previo = usuario.saldo_creditos or 0
    posterior = previo + delta
    if posterior < 0:
        raise HTTPException(status_code=422, detail="saldo de créditos negativo")
    usuario.saldo_creditos = posterior
    entrada = CreditEntry(
        id=_nuevo_id(),
        user_id=user_id,
        delta=delta,
        saldo_previo=previo,
        saldo_posterior=posterior,
        motivo=motivo,
        actor=actor,
        creada_en=utcnow().isoformat(),
    )
    CREDIT_LEDGER.append(entrada)
    return entrada


class CrearUsuarioBody(BaseModel):
    email: str
    display_name: str = ""
    plan: str = "free"
    api_key: str = Field(
        default="",
        description=(
            "API key fija opcional, mínimo 3 caracteres sin espacios. "
            "Vacía = generar automáticamente."
        ),
    )
    valor_fijo: str = Field(
        default="",
        description="Alias de api_key para compatibilidad con el panel de depuración.",
    )
    estado: Literal["habilitado", "deshabilitado"] = "habilitado"
    habilitado: bool | None = Field(
        default=None,
        description="Alias booleano compatible con el formulario V1/V2.",
    )
    enviar_credenciales: bool = False
    send_credentials: bool | None = None
    send_api_key_email: bool | None = Field(
        default=None,
        description="Alias compatible con V1/V2 para solicitar envío por email.",
    )
    motivo: str = Field(default="", description="Justificación de 3 a 500 caracteres")


class MotivoBody(BaseModel):
    motivo: str = ""


class EmitirClaveBody(BaseModel):
    scopes: list[str] = Field(default_factory=list)
    expira_en: str = ""
    motivo: str = ""
    valor_fijo: str = Field(
        default="",
        description="Valor literal con mínimo 3 caracteres y sin espacios. Vacío = aleatorio.",
    )


class RotarClaveBody(BaseModel):
    key_id: str = ""
    periodo_gracia_horas: int = 0
    motivo: str = ""
    valor_fijo: str = Field(
        default="",
        description="Nuevo valor opcional, mínimo 3 caracteres y sin espacios.",
    )


class AsignarPlanBody(BaseModel):
    plan: str
    motivo: str = ""


class AjustarCreditoBody(BaseModel):
    delta: int
    motivo: str = ""


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
    except Exception:  # noqa: BLE001 - error DB sanitizado
        raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    if row is None:
        return None
    return AdminUser(
        id=str(row.id),
        email=str(row.email),
        estado="habilitado" if row.habilitado else "deshabilitado",
        plan="—",
        saldo_creditos=None,
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


async def _persistir_api_key_pg(
    usuario: AdminUser, meta: ApiKeyMeta, valor: str
) -> None:
    """Persiste selector, HMAC y ciphertext, nunca la API key en claro."""
    from sqlalchemy.exc import IntegrityError

    from central_api.db import db_configurado, nueva_sesion
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


@router.post("/users", status_code=201)
async def crear_usuario(
    body: CrearUsuarioBody,
    response: Response,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Da de alta usuario y API key en una sola operación administrativa.

    La forma moderna usa ``api_key``, ``estado`` y ``enviar_credenciales``.
    También se aceptan ``valor_fijo``, ``habilitado`` y ``send_api_key_email``
    para conservar el flujo de alta de V1/V2.
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
    usuario = AdminUser(
        id=persisted_user_id or _nuevo_id(),
        email=email, display_name=body.display_name,
        estado=estado, plan=body.plan, motivo=motivo,
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
            "plan": body.plan,
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


@router.get("/users")
async def listar_usuarios(
    response: Response,
    authorization: str | None = Header(default=None),
    email: str = "",
    estado: str = "",
    plan: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Busca usuarios por email, estado y plan con paginación por offset."""
    require_admin(authorization)
    response.headers["Cache-Control"] = "private, no-store"
    filas_usuarios, filas_claves = await _filas_identidad_pg()
    usuarios_por_id = {user.id: user for user in USERS.values()}
    claves_por_usuario: dict[str, dict[str, dict]] = {}
    ahora = utcnow()
    for user_id, email_pg, habilitado in filas_usuarios:
        uid = str(user_id)
        usuario = usuarios_por_id.get(uid)
        if usuario is None:
            usuario = AdminUser(
                id=uid,
                email=str(email_pg),
                estado="habilitado" if habilitado else "deshabilitado",
                plan="—",
                saldo_creditos=None,
            )
            USERS[uid] = usuario
            usuarios_por_id[uid] = usuario
        else:
            # El estado persistido es canónico tras reinicios o cambios externos.
            usuario.estado = "habilitado" if habilitado else "deshabilitado"
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
                "claves_api": sorted(
                    claves_por_usuario.get(u.id, {}).values(),
                    key=lambda clave: clave["emitida_en"],
                    reverse=True,
                ),
            }
            for u in pagina
        ],
    }


@router.get("/users/{user_id}")
async def ver_usuario(
    user_id: str,
    response: Response,
    authorization: str | None = Header(default=None),
) -> dict:
    """Devuelve un usuario con sus claves (metadatos) y ledger reciente."""
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
    ledger = [asdict(e) for e in CREDIT_LEDGER if e.user_id == user_id][-20:]
    return {
        "success": True,
        "usuario": _vista_usuario(usuario),
        "claves": claves,
        "ledger_reciente": ledger,
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


@router.post("/users/{user_id}/enable")
async def habilitar_usuario(
    user_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Habilita un usuario con motivo y evento auditado."""
    actor = require_admin(authorization)
    return await _cambiar_estado(
        user_id, "habilitado", body.motivo, actor, request_id or "", "user.enabled"
    )


@router.post("/users/{user_id}/disable")
async def deshabilitar_usuario(
    user_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Deshabilita un usuario, revoca sus claves y bloquea nuevos jobs."""
    actor = require_admin(authorization)
    return await _cambiar_estado(
        user_id, "deshabilitado", body.motivo, actor, request_id or "", "user.disabled"
    )


@router.post("/users/{user_id}/api-keys", status_code=201)
async def emitir_clave_api(
    user_id: str,
    body: EmitirClaveBody,
    response: Response,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Emite una clave y la revela una única vez; la auditoría guarda el prefijo."""
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


@router.post("/users/{user_id}/api-keys/{key_id}/reveal")
async def revelar_clave_api(
    user_id: str,
    key_id: str,
    response: Response,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Revela una clave activa al administrador para permitir copiarla.

    El ciphertext nunca se entrega al navegador. Solo se devuelve el secreto
    tras autorización, validación de vigencia/HMAC y registro de auditoría.
    """
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


@router.post("/users/{user_id}/api-keys/rotate")
async def rotar_clave_api(
    user_id: str,
    body: RotarClaveBody,
    response: Response,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Emite una clave nueva y revoca la anterior; audita ambos IDs."""
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
    anterior = API_KEYS.get(body.key_id)
    if anterior is None:
        anterior = await _api_key_pg_por_id(user_id, body.key_id)
        if anterior is not None:
            API_KEYS[body.key_id] = anterior
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


@router.post("/users/{user_id}/plan")
def asignar_plan(
    user_id: str,
    body: AsignarPlanBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Asigna un plan dejando rastro del anterior y el nuevo con fecha efectiva."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo, minimo=MOTIVO_MINIMO_IDENTIDAD)
    usuario = USERS.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    anterior = usuario.plan
    usuario.plan = body.plan
    log_event(
        "user.plan.assigned", actor_id=actor, target_type="user", target_id=user_id,
        request_id=request_id or "", reason=motivo,
        metadata={"anterior": anterior, "nuevo": body.plan},
    )
    return {"success": True, "usuario": _vista_usuario(usuario)}


@router.post("/users/{user_id}/credits")
def ajustar_creditos(
    user_id: str,
    body: AjustarCreditoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Crea una entrada compensatoria de créditos con saldos previo/posterior."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo, minimo=MOTIVO_MINIMO_IDENTIDAD)
    entrada = aplicar_credito(user_id, body.delta, motivo, actor)
    log_event(
        "user.credit.adjusted", actor_id=actor, target_type="user", target_id=user_id,
        request_id=request_id or "", reason=motivo,
        metadata={
            "delta": body.delta,
            "saldo_previo": entrada.saldo_previo,
            "saldo_posterior": entrada.saldo_posterior,
        },
    )
    return {"success": True, "entrada": asdict(entrada)}


class EditarClaveBody(BaseModel):
    scopes: list[str] | None = None
    expira_en: str | None = None
    revocada: bool | None = None
    motivo: str = ""


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


@router.get("/api-keys")
async def listar_claves_api(
    response: Response,
    authorization: str | None = Header(default=None),
    user_id: str = "",
    estado: str = "",
    q: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Lista metadatos de claves API (nunca valores) con filtros de panel."""
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


@router.patch("/api-keys/{key_id}")
async def editar_clave_api(
    key_id: str,
    body: EditarClaveBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Edita scopes, expiración o estado de una clave con motivo auditado."""
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


@router.post("/api-keys/{key_id}/revoke")
async def revocar_clave_api(
    key_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Revoca una clave sin borrar evidencia, con motivo auditado."""
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


@router.post("/api-keys/{key_id}/restore")
async def restaurar_clave_api(
    key_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Restaura una clave revocada con motivo auditado."""
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
