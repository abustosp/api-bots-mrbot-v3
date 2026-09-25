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
import re
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
    return datos


def _firmar_clave(valor: str) -> str:
    secreto = get_settings().api_key_hmac_secret or "sin-secreto-esqueleto"
    return hmac.new(secreto.encode("utf-8"), valor.encode("utf-8"), hashlib.sha256).hexdigest()


def emitir_clave(
    user_id: str, scopes: list[str], expira_en: str, valor_fijo: str = ""
) -> tuple[ApiKeyMeta, str]:
    """Crea una clave, guarda solo verificador+prefijo y devuelve el valor único.

    Con ``valor_fijo`` (p. ej. ``testing`` para depuración) se conserva el
    valor literal en vez de generar uno aleatorio. Debe tener 4+ caracteres
    sin espacios y un verificador HMAC único.
    """
    if valor_fijo:
        valor = valor_fijo.strip()
        if len(valor) < 4 or any(ch.isspace() for ch in valor):
            raise HTTPException(
                status_code=400,
                detail="valor_fijo debe tener 4+ caracteres sin espacios",
            )
    else:
        valor = "mrk_" + secrets.token_urlsafe(32)
    verificador = _firmar_clave(valor)
    if any(k.verificador_hmac == verificador for k in API_KEYS.values()):
        raise HTTPException(status_code=409, detail="valor de clave ya registrado")
    meta = ApiKeyMeta(
        id=_nuevo_id(),
        user_id=user_id,
        prefijo=valor[:8],
        verificador_hmac=verificador,
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
    valor_fijo: str = Field(
        default="",
        description="Valor literal para depuración (p. ej. testing). Vacío = aleatorio.",
    )


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


@router.post("/users", status_code=201)
async def crear_usuario(
    body: CrearUsuarioBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Da de alta un usuario con email o nombre de depuración único."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    email = _normalizar_identidad(body.email)
    if any(u.email == email for u in USERS.values()):
        raise HTTPException(status_code=409, detail="identidad ya registrada")
    usuario = AdminUser(
        id=await _id_pg_por_email(email) or _nuevo_id(),
        email=email, display_name=body.display_name,
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
    meta, valor = emitir_clave(
        user_id, body.scopes, body.expira_en, body.valor_fijo
    )
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


@router.get("/api-keys")
def listar_claves_api(
    authorization: str | None = Header(default=None),
    user_id: str = "",
    estado: str = "",
    q: str = "",
    limit: int = 100,
    offset: int = 0,
) -> dict:
    """Lista metadatos de claves API (nunca valores) con filtros de panel."""
    require_admin(authorization)
    claves = list(API_KEYS.values())
    if user_id:
        claves = [k for k in claves if k.user_id == user_id]
    if estado in ("activa", "revocada"):
        claves = [
            k for k in claves
            if _estado_clave(k) == estado
        ]
    if q.strip():
        termino = q.strip().lower()
        claves = [
            k for k in claves
            if termino in k.prefijo.lower()
            or termino in (USERS.get(k.user_id).email.lower() if USERS.get(k.user_id) else "")
            or any(termino in s.lower() for s in k.scopes)
        ]
    total = len(claves)
    top = max(1, min(limit, 200))
    pagina = claves[max(0, offset): max(0, offset) + top]
    return {
        "success": True,
        "total": total,
        "claves": [_vista_clave_admin(k) for k in pagina],
    }


@router.patch("/api-keys/{key_id}")
def editar_clave_api(
    key_id: str,
    body: EditarClaveBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Edita scopes, expiración o estado de una clave con motivo auditado."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    meta = API_KEYS.get(key_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="clave no encontrada")
    anterior = {
        "scopes": list(meta.scopes),
        "expira_en": meta.expira_en,
        "revocada": meta.revocada,
    }
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
def revocar_clave_api(
    key_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Revoca una clave sin borrar evidencia, con motivo auditado."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    meta = API_KEYS.get(key_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="clave no encontrada")
    meta.revocada = True
    log_event(
        "user.api_key.revoked", actor_id=actor, target_type="api_key",
        target_id=meta.id, request_id=request_id or "", reason=motivo,
        metadata={"user_id": meta.user_id, "prefijo": meta.prefijo},
    )
    return {"success": True, "clave": _vista_clave_admin(meta)}


@router.post("/api-keys/{key_id}/restore")
def restaurar_clave_api(
    key_id: str,
    body: MotivoBody,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    """Restaura una clave revocada con motivo auditado."""
    actor = require_admin(authorization)
    motivo = validar_motivo(body.motivo)
    meta = API_KEYS.get(key_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="clave no encontrada")
    meta.revocada = False
    log_event(
        "user.api_key.restored", actor_id=actor, target_type="api_key",
        target_id=meta.id, request_id=request_id or "", reason=motivo,
        metadata={"user_id": meta.user_id, "prefijo": meta.prefijo},
    )
    return {"success": True, "clave": _vista_clave_admin(meta)}
