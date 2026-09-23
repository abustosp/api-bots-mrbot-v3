"""Gestión de usuarios del panel: alta, baja lógica, claves API y créditos.

- ``users.id`` es UUIDv4 (invariante I-1); la auditoría usa UUIDv7.
- La clave API se muestra UNA sola vez al emitirla; en el store solo quedan
  el prefijo y el verificador HMAC, nunca el valor.
- Los ajustes de crédito son entradas compensatorias del ledger con motivo
  obligatorio; nunca una actualización opaca del saldo.
- Toda operación exige motivo de 10 a 500 caracteres y genera auditoría.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import asdict, dataclass, field

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from central_api.admin._common import enmascarar, require_admin, validar_motivo
from central_api.admin.audit import log_event
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
    saldo_creditos: int = 0
    motivo: str = ""


@dataclass
class ApiKeyMeta:
    """Metadatos públicos de una clave API (jamás el valor)."""

    id: str
    user_id: str
    prefijo: str
    verificador_hmac: str
    scopes: list[str] = field(default_factory=list)
    expira_en: str = ""
    revocada: bool = False
    emitida_en: str = ""


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


def _normalizar_email(email: str) -> str:
    return (email or "").strip().lower()


def _vista_usuario(usuario: AdminUser) -> dict:
    return asdict(usuario)


def _vista_clave(meta: ApiKeyMeta) -> dict:
    datos = asdict(meta)
    datos.pop("verificador_hmac", None)
    return datos


def _firmar_clave(valor: str) -> str:
    secreto = get_settings().api_key_hmac_secret or "sin-secreto-esqueleto"
    return hmac.new(secreto.encode("utf-8"), valor.encode("utf-8"), hashlib.sha256).hexdigest()


def emitir_clave(user_id: str, scopes: list[str], expira_en: str) -> tuple[ApiKeyMeta, str]:
    """Crea una clave, guarda solo verificador+prefijo y devuelve el valor único."""
    valor = "mrk_" + secrets.token_urlsafe(32)
    meta = ApiKeyMeta(
        id=_nuevo_id(),
        user_id=user_id,
        prefijo=valor[:8],
        verificador_hmac=_firmar_clave(valor),
        scopes=list(scopes),
        expira_en=expira_en or "",
        emitida_en=utcnow().isoformat(),
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
    previo = usuario.saldo_creditos
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
    motivo: str = Field(default="", description="Justificación de 10 a 500 caracteres")


class MotivoBody(BaseModel):
    motivo: str = ""


class EmitirClaveBody(BaseModel):
    scopes: list[str] = Field(default_factory=list)
    expira_en: str = ""
    motivo: str = ""


class RotarClaveBody(BaseModel):
    key_id: str = ""
    periodo_gracia_horas: int = 0
    motivo: str = ""


class AsignarPlanBody(BaseModel):
    plan: str
    motivo: str = ""


class AjustarCreditoBody(BaseModel):
    delta: int
    motivo: str = ""


@router.post("/users", status_code=201)
def crear_usuario(
    body: CrearUsuarioBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Da de alta un usuario con email normalizado único y plan inicial."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    email = _normalizar_email(body.email)
    if "@" not in email:
        raise HTTPException(status_code=400, detail="email inválido")
    if any(u.email == email for u in USERS.values()):
        raise HTTPException(status_code=409, detail="email ya registrado")
    usuario = AdminUser(
        id=_nuevo_id(), email=email, display_name=body.display_name,
        plan=body.plan, motivo=motivo,
    )
    USERS[usuario.id] = usuario
    log_event(
        "user.created", actor_id=actor, target_type="user", target_id=usuario.id,
        request_id=request_id or "", reason=motivo,
        metadata={"email": email, "plan": body.plan},
    )
    return {"success": True, "usuario": _vista_usuario(usuario)}


@router.get("/users")
def listar_usuarios(
    authorization: str | None = Header(default=None),
    email: str = "",
    estado: str = "",
    plan: str = "",
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Busca usuarios por email, estado y plan con paginación por offset."""
    require_admin(authorization)
    usuarios = list(USERS.values())
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
        "usuarios": [_vista_usuario(u) for u in pagina],
    }


@router.get("/users/{user_id}")
def ver_usuario(user_id: str, authorization: str | None = Header(default=None)) -> dict:
    """Devuelve un usuario con sus claves (metadatos) y ledger reciente."""
    require_admin(authorization)
    usuario = USERS.get(user_id)
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


def _cambiar_estado(
    user_id: str, estado: str, motivo_raw: str, actor: str, request_id: str,
    accion: str,
) -> dict:
    motivo = validar_motivo(motivo_raw)
    usuario = USERS.get(user_id)
    if usuario is None:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
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
def habilitar_usuario(
    user_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Habilita un usuario con motivo y evento auditado."""
    actor = require_admin(authorization)
    return _cambiar_estado(user_id, "habilitado", body.motivo, actor, request_id or "", "user.enabled")


@router.post("/users/{user_id}/disable")
def deshabilitar_usuario(
    user_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Deshabilita un usuario, revoca sus claves y bloquea nuevos jobs."""
    actor = require_admin(authorization)
    return _cambiar_estado(
        user_id, "deshabilitado", body.motivo, actor, request_id or "", "user.disabled"
    )


@router.post("/users/{user_id}/api-keys", status_code=201)
def emitir_clave_api(
    user_id: str,
    body: EmitirClaveBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Emite una clave y la revela una única vez; la auditoría guarda el prefijo."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    if user_id not in USERS:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    meta, valor = emitir_clave(user_id, body.scopes, body.expira_en)
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


@router.post("/users/{user_id}/api-keys/rotate")
def rotar_clave_api(
    user_id: str,
    body: RotarClaveBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Emite una clave nueva y revoca la anterior; audita ambos IDs."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    if user_id not in USERS:
        raise HTTPException(status_code=404, detail="usuario no encontrado")
    anterior = API_KEYS.get(body.key_id)
    if anterior is None or anterior.user_id != user_id:
        raise HTTPException(status_code=404, detail="clave anterior no encontrada")
    anterior.revocada = True
    meta, valor = emitir_clave(user_id, anterior.scopes, anterior.expira_en)
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
    motivo = validar_motivo(body.motivo)
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
    motivo = validar_motivo(body.motivo)
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
