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
from central_api.billing.entitlements import account_summary, current_period
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
    nombre: str | None = Field(
        default=None,
        max_length=120,
        description="Nombre visible asociado a la cuenta; es opcional.",
        examples=["Persona"],
    )
    enviar_email: bool = Field(
        default=False,
        description="Indica si también se intenta enviar la clave al correo de la identidad.",
        examples=[False],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"usuario": "persona@example.com", "nombre": "Persona", "enviar_email": False}
            ]
        }
    }


class UsuarioBody(BaseModel):
    usuario: str = Field(
        min_length=2,
        max_length=254,
        description="Correo o alias de la cuenta cuya clave se quiere restablecer.",
        examples=["persona@example.com"],
    )


class EstablecerApiKeyBody(BaseModel):
    usuario: str = Field(
        min_length=2,
        max_length=254,
        description="Correo o alias asociado a la clave actual.",
        examples=["persona@example.com"],
    )
    api_key_actual: str = Field(
        min_length=1,
        description="Clave vigente que se debe comprobar antes de emitir otra.",
        examples=["mbk_0123456789abcdef_abcdef0123456789"],
    )
    api_key_nueva: str = Field(
        min_length=3,
        description="Nueva clave elegida por el usuario; debe tener al menos 3 caracteres y no contener espacios.",
        examples=["mi-clave-nueva"],
    )


class UsuarioCreadoRespuesta(BaseModel):
    usuario: str = Field(description="Identidad normalizada de la cuenta.", examples=["persona@example.com"])
    nombre: str = Field(description="Nombre visible de la cuenta; puede estar vacío.", examples=["Persona"])
    estado: str = Field(description="Estado inicial de la cuenta.", examples=["deshabilitado"])
    api_key: str = Field(
        description="Clave de acceso revelada en esta respuesta. Guárdala de forma segura, ya que no se vuelve a mostrar aquí.",
        examples=["mbk_0123456789abcdef_abcdef0123456789"],
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "usuario": "persona@example.com",
                    "nombre": "Persona",
                    "estado": "deshabilitado",
                    "api_key": "mbk_0123456789abcdef_abcdef0123456789",
                }
            ]
        }
    }


class RestablecimientoSolicitadoRespuesta(BaseModel):
    detail: str = Field(
        description="Confirmación genérica que no revela si la cuenta existe ni si se pudo enviar el mensaje.",
        examples=["Si la cuenta existe, recibirás instrucciones por email."],
    )


class ApiKeyActualizadaRespuesta(BaseModel):
    success: bool = Field(description="Indica que la clave se actualizó correctamente.", examples=[True])
    usuario: str = Field(description="Identidad normalizada de la cuenta actualizada.", examples=["persona@example.com"])
    mensaje: str = Field(description="Confirmación de la actualización.", examples=["API key actualizada"])


class ConsultasUsuarioRespuesta(BaseModel):
    asignadas: int | None = Field(description="Consultas incluidas en el cupo vigente; nulo si es ilimitado.", examples=[100])
    consumidas: int = Field(description="Consultas ya consumidas.", examples=[12])
    reservadas: int = Field(description="Consultas actualmente reservadas.", examples=[1])
    disponibles: int | None = Field(description="Consultas restantes; nulo si el cupo es ilimitado.", examples=[87])


class MiUsuarioRespuesta(BaseModel):
    usuario: str = Field(description="Identidad de la cuenta autenticada.", examples=["persona@example.com"])
    estado: str = Field(description="Estado actual de la cuenta.", examples=["habilitado"])
    tipo_cuenta: str = Field(description="Categoría de cuenta asignada.", examples=["PRUEBA"])
    plan: str = Field(description="Nombre normalizado del tipo de cuenta.", examples=["prueba"])
    max_requests: int | None = Field(description="Máximo de consultas configurado; nulo cuando no hay tope.", examples=[100])
    ilimitado: bool = Field(description="Indica si el cupo no tiene límite.", examples=[False])
    renovable: bool = Field(description="Indica si el cupo se renueva por período.", examples=[False])
    periodo: dict[str, Any] | None = Field(description="Período vigente, si corresponde.", examples=[None])
    consultas: ConsultasUsuarioRespuesta = Field(description="Detalle de asignación y uso del cupo.")


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


def _key_row_values(api_key: str) -> tuple[str, str, str]:
    key_id, secret = _selector_y_secreto(api_key)
    hmac_secret = get_settings().api_key_hmac_secret
    if not hmac_secret:
        raise HTTPException(
            status_code=503,
            detail="La configuración segura de API keys no está disponible",
        )
    try:
        from central_api.security.api_key_vault import encrypt_api_key

        ciphertext = encrypt_api_key(api_key)
    except Exception:  # noqa: BLE001 - no persistir claves sin cifrado RSA
        raise HTTPException(
            status_code=503,
            detail="La custodia cifrada de API keys no está disponible",
        ) from None
    return key_id, fingerprint_secret(hmac_secret, secret), ciphertext


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

    from central_api.models.billing import BillingAccount as BillingAccountRow

    key_prefix, verifier, ciphertext = _key_row_values(api_key)
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
            sesion.add(
                BillingAccountRow(
                    user_id=cuenta.id, account_type="PRUEBA", max_requests=100
                )
            )
            key_row = ApiKey(
                user_id=cuenta.id,
                key_prefix=key_prefix,
                verifier_hmac=verifier,
                encrypted_value=ciphertext,
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

    key_prefix, verifier, ciphertext = _key_row_values(api_key)
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
                encrypted_value=ciphertext,
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

    key_prefix, verifier, ciphertext = _key_row_values(api_key)
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
                    encrypted_value=ciphertext,
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


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Crear una cuenta de usuario",
    description=(
        "Registra una cuenta inicialmente deshabilitada y devuelve una clave de acceso "
        "que solo se revela en esta respuesta. Si se solicita, también intenta enviarla "
        "al correo de la identidad; un fallo de envío no impide completar el alta. "
        "La respuesta no debe almacenarse en caché."
    ),
    responses={
        201: {
            "model": UsuarioCreadoRespuesta,
            "description": "Cuenta creada. Conserva la clave devuelta, porque no se vuelve a mostrar en esta operación.",
        },
        400: {
            "description": "La identidad está vacía o no cumple el formato admitido. Envía un correo válido o un alias de 2 a 64 caracteres permitido.",
            "content": {"application/json": {"example": {"detail": "email inválido"}}},
        },
        409: {
            "description": "Ya existe una cuenta con esa identidad. Usa otra identidad o solicita el restablecimiento de la clave de la cuenta existente.",
            "content": {"application/json": {"example": {"detail": "identidad ya registrada"}}},
        },
        422: {
            "description": "El cuerpo no cumple las restricciones de entrada, por ejemplo, falta usuario o excede la longitud permitida. Corrige los campos indicados y vuelve a enviar la solicitud."
        },
        503: {
            "description": "No se pudo completar el alta o no está disponible la protección necesaria para guardar la clave. No se revela la clave; vuelve a intentar más tarde.",
            "content": {"application/json": {"example": {"detail": "No se pudo completar el alta de usuario"}}},
        },
    },
)
async def crear_usuario(
    body: CrearUsuarioBody,
    response: Response,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """Crea una cuenta deshabilitada y devuelve su clave de acceso una sola vez."""
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


@router.post(
    "/restablecer-api-key",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Solicitar el restablecimiento de una clave",
    description=(
        "Solicita de forma privada una nueva clave para la cuenta indicada. "
        "La respuesta es siempre genérica para no confirmar si la identidad existe; "
        "si la cuenta puede recibir correo y el envío se completa, las instrucciones "
        "llegan a esa dirección."
    ),
    responses={
        202: {
            "model": RestablecimientoSolicitadoRespuesta,
            "description": "Solicitud aceptada. La confirmación no informa si la cuenta existe ni garantiza que se haya enviado un correo.",
        },
        400: {
            "description": "La identidad está vacía o tiene un formato no admitido. Corrige el correo o alias e inténtalo de nuevo.",
            "content": {"application/json": {"example": {"detail": "email inválido"}}},
        },
        422: {
            "description": "El cuerpo no cumple las restricciones del campo usuario. Envía una identidad de entre 2 y 254 caracteres."
        },
    },
)
async def restablecer_api_key(
    body: UsuarioBody,
    response: Response,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, str]:
    """Solicita el envío privado de instrucciones para cambiar la clave."""
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


@router.post(
    "/establecer-api-key",
    summary="Cambiar la clave de acceso",
    description=(
        "Comprueba la identidad y la clave vigente antes de sustituirla por la nueva. "
        "Al completarse, las claves anteriores dejan de ser válidas. La respuesta "
        "confirma el cambio, pero no devuelve la nueva clave: consérvala en el cliente "
        "que la envió."
    ),
    responses={
        200: {
            "model": ApiKeyActualizadaRespuesta,
            "description": "La clave se actualizó y las claves anteriores quedaron revocadas.",
        },
        400: {
            "description": "La identidad no es válida, la nueva clave tiene menos de 3 caracteres, contiene espacios o coincide con la actual. Corrige la identidad o elige una clave distinta sin espacios.",
            "content": {"application/json": {"example": {"detail": "La nueva API key debe ser distinta"}}},
        },
        401: {
            "description": "La clave actual no es válida para esa identidad, está revocada o la cuenta no está habilitada. Verifica ambos datos o solicita el restablecimiento de la clave.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        409: {
            "description": "No se pudo emitir la nueva clave. Reintenta más tarde; la operación no confirma el cambio.",
            "content": {"application/json": {"example": {"detail": "No se pudo emitir la nueva API key"}}},
        },
        422: {
            "description": "El cuerpo no cumple las restricciones de entrada, como los límites de longitud o la ausencia de un campo obligatorio. Corrige los campos indicados y vuelve a enviar la solicitud."
        },
        503: {
            "description": "El servicio de verificación o actualización no está disponible temporalmente. Espera y vuelve a intentarlo.",
            "content": {"application/json": {"example": {"detail": {"error_code": "service_not_enabled", "message": "Servicio temporalmente no disponible."}}}},
        },
    },
)
async def establecer_api_key(
    body: EstablecerApiKeyBody,
    response: Response,
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """Verifica la clave actual y sustituye las claves anteriores por la nueva."""
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


@router.get(
    "/me",
    summary="Consultar mi cuenta de usuario",
    description=(
        "Devuelve la identidad autenticada, el estado de la cuenta y el detalle "
        "del cupo asignado y utilizado. Requiere una credencial válida cuando la "
        "autenticación de clientes está habilitada."
    ),
    responses={
        200: {
            "model": MiUsuarioRespuesta,
            "description": "Identidad, estado y resumen de consultas de la cuenta autenticada.",
        },
        401: {
            "description": "Falta una credencial válida o la cuenta no puede autenticarse. Envía una clave activa asociada a una cuenta habilitada.",
            "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}},
        },
        503: {
            "description": "No se puede verificar la cuenta temporalmente. Reintenta cuando el servicio vuelva a estar disponible.",
            "content": {"application/json": {"example": {"detail": {"error_code": "service_not_enabled", "message": "Servicio temporalmente no disponible."}}}},
        },
        422: {
            "description": "La solicitud contiene datos o encabezados que no se pudieron interpretar. Revisa el formato de la petición y vuelve a enviarla.",
        },
        500: {
            "description": "No fue posible cargar la información actual de la cuenta. Reintenta la consulta y, si el problema persiste, contacta al soporte de la cuenta.",
            "content": {"text/plain": {"example": "Internal Server Error"}},
        },
    },
)
async def mi_usuario(
    principal: ApiPrincipal = Depends(require_api_principal),
) -> dict[str, Any]:
    """Devuelve la identidad, el estado y el cupo de consultas de la cuenta autenticada."""
    if principal.key_id == "dev":
        raise _no_autorizado()
    from central_api.admin.users import USERS
    from central_api.billing.entitlements import hydrate_billing_account

    memoria = USERS.get(principal.user_id)
    identidad = memoria.email if memoria is not None else principal.user_id
    estado = memoria.estado if memoria is not None else "habilitado"
    if db_configurado():
        async with nueva_sesion() as sesion:
            usuario = await sesion.get(User, uuid.UUID(principal.user_id))
        if usuario is not None:
            identidad = usuario.email
            estado = "habilitado" if usuario.habilitado else "deshabilitado"

    cuenta = await hydrate_billing_account(principal.user_id)
    resumen = account_summary(cuenta, current_period(principal.user_id))
    return {
        "usuario": identidad,
        "estado": estado,
        "tipo_cuenta": resumen["tipo_cuenta"],
        "plan": resumen["tipo_cuenta"].lower(),
        "max_requests": cuenta.max_requests,
        "ilimitado": resumen["ilimitado"],
        "renovable": resumen["renovable"],
        "periodo": resumen["periodo"],
        "consultas": {
            "asignadas": resumen["limite"],
            "consumidas": resumen["consumidas"],
            "reservadas": resumen["reservadas"],
            "disponibles": resumen["disponibles"],
        },
    }


__all__ = ["router"]
