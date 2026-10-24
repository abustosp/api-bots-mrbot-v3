"""Hook de plugin de frps (``/internal/v1/relay/frp-hook``).

frps no envía cabeceras: la autenticación es el ``relay_token`` que el frpc
manda en ``metadatas``. Login exige token válido y usuario = nodo con ``:`` ->
``-``; NewProxy exige tcp en el puerto reservado para ese token. Ping revalida
el token: si se rotó o el worker se dio de baja, frps deja de renovar el latido
y la sesión cae por heartbeat timeout (``transport.heartbeatTimeout`` = 75 s).
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter

from central_api.admin.relay import hash_token, usuario_frp
from central_api.store import RELAY_WORKERS

router = APIRouter()


def _registro_por_token(metas: dict | None) -> tuple[str, dict] | None:
    presentado = hash_token(str((metas or {}).get("relay_token") or ""))
    hallado = None
    for node, reg in RELAY_WORKERS.items():  # sin cortar: tiempo constante por registro
        if hmac.compare_digest(presentado, reg["token_hash"]):
            hallado = (node, reg)
    return hallado


def _rechazo(motivo: str) -> dict:
    return {"reject": True, "reject_reason": motivo}


@router.post(
    "/relay/frp-hook",
    summary="Validar logins y proxies de frps",
    description=(
        "Plugin de servidor de frps. Acepta o rechaza Login, NewProxy y Ping según el "
        "token de relay y el puerto reservado; otras operaciones se aceptan sin cambios."
    ),
    responses={200: {"description": "Decisión: `{\"reject\": false, \"unchange\": true}` o `{\"reject\": true, \"reject_reason\": \"...\"}`."}},
)
def frp_hook(body: dict) -> dict:
    """Decide si frps acepta el login o el proxy."""
    op = body.get("op")
    content = body.get("content") or {}
    if op == "Login":
        hallado = _registro_por_token(content.get("metas"))
        if hallado is None:
            return _rechazo("token de relay inválido")
        if content.get("user") != usuario_frp(hallado[0]):
            return _rechazo("usuario no corresponde al token")
    elif op in ("NewProxy", "Ping"):
        user = content.get("user") or {}
        hallado = _registro_por_token(user.get("metas"))
        if hallado is None:
            return _rechazo("token de relay inválido")
        if user.get("user") != usuario_frp(hallado[0]):
            return _rechazo("usuario no corresponde al token")
        if op == "Ping":
            return {"reject": False, "unchange": True}
        if content.get("proxy_type") != "tcp":
            return _rechazo("solo se permiten proxies tcp")
        if content.get("remote_port") != hallado[1]["port"]:
            return _rechazo("puerto remoto no autorizado")
    return {"reject": False, "unchange": True}
