"""Alta y autogestión de usuarios y API keys de cliente."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from pydantic import BaseModel, Field

from central_api.admin.audit import log_event
from central_api.admin.notifications import enviar_credenciales_email
from central_api.api.dependencies import (
    _no_autorizado,
    _selector_y_secreto,
    authenticate_api_key,
    require_api_principal,
)
from central_api.billing.entitlements import current_period, get_plan
from central_api.billing.reservation import get_balance
from central_api.db import db_configurado, nueva_sesion
from central_api.models.identity import ApiKey, User
from central_api.security.api_keys import fingerprint_secret, new_key_id, new_secret
from central_api.security.principals import ApiPrincipal
from central_api.settings import get_settings

router = APIRouter(prefix="/usuarios", tags=["usuarios"])


class CrearUsuarioBody(BaseModel):
    usuario: str = Field(
        min_length=2,
        max_length=254,
        description="Email o alias de depuración único, normalizado sin distinguir mayúsculas.",
        examples=["persona@example.com"],
    )
    nombre: str | None = Field(default=None, max_length=120)
    enviar_email: bool = False

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"usuario": "persona@example.com", "nombre": "Persona", "enviar_email": False}
            ]
        }
    }


class UsuarioBody(BaseModel):
    usuario: str = Field(min_length=2, max_length=254, examples=["persona@example.com"])


class EstablecerApiKeyBody(BaseModel):
    usuario: str = Field(min_length=2, max_length=254, examples=["persona@example.com"])
    api_key_actual: str = Field(min_length=1, examples=["mbk_0123456789abcdef_abcdef0123456789"])
    api_key_nueva: str = Field(min_length=3, examples=["mi-clave-nueva"])


def _normalizar_usuario(raw: str) -> str:
    try:
        from central_api.admin.users import _normalizar_identidad

        return _normalizar_identidad(raw)
    except HTTPException:
        raise


def _validar_api_key(raw: str) -> str:
    valor = (raw or "").strip()
    if len(valor) < 3 or any(character.isspace() for character in valor):
        raise HTTPException(
            status_code=400,
            detail="api_key_nueva debe tener al menos 3 caracteres sin espacios",
        )
    return valor


def _nueva_api_key() -> str:
    return f"mbk_{new_key_id()}_{new_secret()}"


def _key_row_values(api_key: str) -> tuple[str, str]:
    key_id, secret = _selector_y_secreto(api_key)
    hmac_secret = get_settings().api_key_hmac_secret
    if not hmac_secret:
        raise HTTPException(
            status_code=503,
            detail="La configuración segura de API keys no está disponible",
        )
    return key_id, fingerprint_secret(hmac_secret, secret)


def _registrar_en_memoria(
    *, usuario_id: str, identidad: str, nombre: str, api_key: str, estado: str
) -> None:
    from central_api.admin.users import AdminUser, USERS, emitir_clave

    existente = USERS.get(usuario_id)
    if existente is None:
        USERS[usuario_id] = AdminUser(
            id=usuario_id,
            email=identidad,
            display_name=nombre,
            estado=estado,
        )
    else:
        existente.email = identidad
        existente.display_name = nombre or existente.display_name
        existente.estado = estado
    emitir_clave(usuario_id, [], "", api_key)


async def _crear_usuario_pg(identidad: str, api_key: str) -> str:
    from sqlalchemy import func, select
    from sqlalchemy.exc import IntegrityError

    key_prefix, verifier = _key_row_values(api_key)
    try:
        async with nueva_sesion() as sesion:
            existente = (
                await sesion.execute(
                    select(User.id).where(func.lower(User.email) == identidad.casefold()).limit(1)
                )
            ).scalar_one_or_none()
            if existente is not None:
                raise HTTPException(status_code=409, detail="identidad ya registrada")
            cuenta = User(email=identidad, habilitado=False)
            sesion.add(cuenta)
            await sesion.flush()
            key_row = ApiKey(
                user_id=cuenta.id,
                key_prefix=key_prefix,
                verifier_hmac=verifier,
                scopes=[],
            )
            sesion.add(key_row)
            await sesion.flush()
            return str(cuenta.id)
    except IntegrityError:
        raise HTTPException(status_code=409, detail="identidad ya registrada") from None


def _buscar_usuario_memoria(identidad: str):
    from central_api.admin.users import USERS

    return next(
        (user for user in USERS.values() if user.email.strip().casefold() == identidad.casefold()),
        None,
    )


async def _buscar_usuario_pg(identidad: str) -> tuple[str, str, bool] | None:
    from sqlalchemy import func, select

    async with nueva_sesion() as sesion:
        fila = (
            await sesion.execute(
                select(User.id, User.email, User.habilitado)
                .where(func.lower(User.email) == identidad.casefold())
                .limit(1)
            )
        ).first()
    return (str(fila[0]), str(fila[1]), bool(fila[2])) if fila else None


async def _emitir_y_revocar_pg(
    *, user_id: str, api_key: str
) -> None:
    from sqlalchemy import func, update

    key_prefix, verifier = _key_row_values(api_key)
    async with nueva_sesion() as sesion:
        await sesion.execute(
            update(ApiKey)
            .where(ApiKey.user_id == uuid.UUID(user_id), ApiKey.revoked_at.is_(None))
            .values(revoked_at=func.current_timestamp())
        )
        sesion.add(
            ApiKey(
                user_id=uuid.UUID(user_id),
                key_prefix=key_prefix,
                verifier_hmac=verifier,
                scopes=[],
            )
        )
        await sesion.flush()


class _EmailNoEnviado(Exception):
    """Señal interna para revertir la rotación cuando no llega el email."""


async def _rotar_pg_y_enviar_email(
    *, user_id: str, identidad: str, api_key: str, nombre: str
) -> bool:
    """Rota en una transacción y revierte si el proveedor no acepta el email."""
    from sqlalchemy import func, update

    key_prefix, verifier = _key_row_values(api_key)
    try:
        async with nueva_sesion() as sesion:
            await sesion.execute(
                update(ApiKey)
                .where(ApiKey.user_id == uuid.UUID(user_id), ApiKey.revoked_at.is_(None))
                .values(revoked_at=func.current_timestamp())
            )
            sesion.add(
                ApiKey(
                    user_id=uuid.UUID(user_id),
                    key_prefix=key_prefix,
                    verifier_hmac=verifier,
                    scopes=[],
                )
            )
            await sesion.flush()
            enviado, _ = await asyncio.to_thread(
                enviar_credenciales_email, identidad, api_key, nombre
            )
            if not enviado:
                raise _EmailNoEnviado
        return True
    except _EmailNoEnviado:
        return False


async def _rotar_clave_memoria(usuario_id: str, api_key: str) -> None:
    from central_api.admin.users import API_KEYS, emitir_clave

    for key in API_KEYS.values():
        if key.user_id == usuario_id and not key.revocada:
            key.revocada = True
    emitir_clave(usuario_id, [], "", api_key)


@router.post("", status_code=status.HTTP_201_CREATED, summary="Crear usuario")
async def crear_usuario(
    body: CrearUsuarioBody,
    response: Response,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """Crea una cuenta deshabilitada y revela su clave una sola vez."""
    identidad = _normalizar_usuario(body.usuario)
    if _buscar_usuario_memoria(identidad) is not None:
        raise HTTPException(status_code=409, detail="identidad ya registrada")

    api_key = _nueva_api_key() if db_configurado() else "mrk_" + new_secret()
    usuario_id = str(uuid.uuid4())
    if db_configurado():
        try:
            usuario_id = await _crear_usuario_pg(identidad, api_key)
        except HTTPException:
            raise
        except Exception:  # noqa: BLE001 - fallo de DB sin detalle interno
            raise HTTPException(
                status_code=503,
                detail="No se pudo completar el alta de usuario",
            ) from None

    try:
        _registrar_en_memoria(
            usuario_id=usuario_id,
            identidad=identidad,
            nombre=(body.nombre or "").strip(),
            api_key=api_key,
            estado="deshabilitado",
        )
    except Exception:
        # La cuenta creada en PostgreSQL sigue deshabilitada y sin divulgar su
        # clave si falla el espejo local.
        raise HTTPException(status_code=503, detail="No se pudo completar el alta de usuario") from None

    enviada = False
    if body.enviar_email and "@" in identidad:
        enviada, _ = await asyncio.to_thread(
            enviar_credenciales_email,
            identidad,
            api_key,
            (body.nombre or "").strip(),
        )
    log_event(
        "user.public_created",
        actor_type="user",
        actor_id=usuario_id,
        target_type="user",
        target_id=usuario_id,
        request_id=request_id or "",
        metadata={"usuario": identidad, "email_enviado": enviada},
    )
    response.headers["Cache-Control"] = "no-store"
    return {
        "usuario": identidad,
        "nombre": (body.nombre or "").strip(),
        "estado": "deshabilitado",
        "api_key": api_key,
    }


@router.post("/restablecer-api-key", status_code=status.HTTP_202_ACCEPTED, summary="Restablecer API key")
async def restablecer_api_key(
    body: UsuarioBody,
    response: Response,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, str]:
    """Solicita una rotación enviada exclusivamente por email, sin oráculo."""
    identidad = _normalizar_usuario(body.usuario)
    settings = get_settings()
    usuario_pg = None
    usuario_mem = None
    user_id = ""
    enviado = False
    if "@" in identidad and settings.smtp_server and settings.smtp_user and settings.smtp_password and settings.smtp_starttls:
        try:
            usuario_pg = await _buscar_usuario_pg(identidad) if db_configurado() else None
        except Exception:  # noqa: BLE001 - respuesta deliberadamente genérica
            usuario_pg = None
        usuario_mem = _buscar_usuario_memoria(identidad)
        if usuario_pg is not None or usuario_mem is not None:
            api_key = _nueva_api_key() if usuario_pg is not None else "mrk_" + new_secret()
            user_id = usuario_pg[0] if usuario_pg is not None else usuario_mem.id
            if usuario_pg is not None:
                try:
                    enviado = await _rotar_pg_y_enviar_email(
                        user_id=user_id,
                        identidad=identidad,
                        api_key=api_key,
                        nombre=usuario_mem.display_name if usuario_mem else "",
                    )
                except Exception:  # noqa: BLE001 - no se filtra existencia ni motivo
                    enviado = False
                if enviado and usuario_mem is not None:
                    await _rotar_clave_memoria(user_id, api_key)
            else:
                try:
                    enviado, _ = await asyncio.to_thread(
                        enviar_credenciales_email,
                        identidad,
                        api_key,
                        usuario_mem.display_name,
                    )
                except Exception:  # noqa: BLE001 - no se filtra existencia ni motivo
                    enviado = False
                if enviado:
                    await _rotar_clave_memoria(user_id, api_key)
    log_event(
        "user.public_api_key_reset",
        actor_type="user",
        actor_id=user_id or "user:unknown",
        target_type="user",
        target_id=user_id,
        request_id=request_id or "",
        result="success" if enviado else "accepted",
        metadata={"email_enviado": enviado},
    )
    response.headers["Cache-Control"] = "no-store"
    return {"detail": "Si la cuenta existe, recibirás instrucciones por email."}


@router.post("/establecer-api-key", summary="Establecer API key")
async def establecer_api_key(
    body: EstablecerApiKeyBody,
    response: Response,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """Verifica la clave actual y revoca las claves anteriores al emitir otra."""
    identidad = _normalizar_usuario(body.usuario)
    api_key_actual = body.api_key_actual.strip()
    api_key_nueva = _validar_api_key(body.api_key_nueva)
    if api_key_actual == api_key_nueva:
        raise HTTPException(status_code=400, detail="La nueva API key debe ser distinta")
    principal = await authenticate_api_key(identidad, api_key_actual)
    if principal is None:
        raise _no_autorizado()

    usuario_pg = None
    if db_configurado():
        try:
            usuario_pg = await _buscar_usuario_pg(identidad)
        except Exception:  # noqa: BLE001 - sin detalle interno
            raise HTTPException(status_code=503, detail="Servicio no disponible") from None
    usuario_mem = _buscar_usuario_memoria(identidad)
    if usuario_pg is not None:
        try:
            await _emitir_y_revocar_pg(user_id=usuario_pg[0], api_key=api_key_nueva)
        except Exception:  # noqa: BLE001 - transacción revierte los cambios
            raise HTTPException(status_code=409, detail="No se pudo emitir la nueva API key") from None
    if usuario_mem is not None:
        await _rotar_clave_memoria(usuario_mem.id, api_key_nueva)
    elif usuario_pg is None:
        raise _no_autorizado()

    log_event(
        "user.public_api_key_changed",
        actor_type="user",
        actor_id=principal.user_id,
        target_type="user",
        target_id=principal.user_id,
        request_id=request_id or "",
        metadata={"api_key_prefijo": hashlib.sha256(api_key_nueva.encode()).hexdigest()[:8]},
    )
    response.headers["Cache-Control"] = "no-store"
    return {"success": True, "usuario": identidad, "mensaje": "API key actualizada"}


@router.get("/me", summary="Consultar mi usuario")
async def mi_usuario(
    principal: ApiPrincipal = Depends(require_api_principal),
) -> dict[str, Any]:
    """Devuelve identidad, estado y datos de plan, saldo y consultas."""
    if principal.key_id == "dev":
        raise _no_autorizado()
    from central_api.admin.users import USERS

    memoria = USERS.get(principal.user_id)
    identidad = memoria.email if memoria is not None else principal.user_id
    estado = memoria.estado if memoria is not None else "habilitado"
    plan_memoria = memoria.plan if memoria is not None else "free"
    if db_configurado():
        async with nueva_sesion() as sesion:
            usuario = await sesion.get(User, uuid.UUID(principal.user_id))
        if usuario is not None:
            identidad = usuario.email
            estado = "habilitado" if usuario.habilitado else "deshabilitado"

    period = current_period(principal.user_id)
    plan_code = period.plan_code if period is not None else plan_memoria
    plan = get_plan(plan_code) or get_plan("free")
    asignadas = period.cuota_asignada if period is not None else (plan.cuota_mensual if plan else 0)
    consumidas = period.cuota_consumida if period is not None else 0
    reservadas = period.cuota_reservada if period is not None else 0
    return {
        "usuario": identidad,
        "estado": estado,
        "plan": plan.code if plan else plan_code,
        "saldo_creditos": get_balance(principal.user_id),
        "consultas": {
            "asignadas": asignadas,
            "consumidas": consumidas,
            "reservadas": reservadas,
            "disponibles": max(0, asignadas - consumidas - reservadas),
        },
    }


__all__ = ["router"]
