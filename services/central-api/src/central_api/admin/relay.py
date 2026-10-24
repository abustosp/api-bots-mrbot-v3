"""Workers por relay frp (detrás de NAT): alta, rotación de token y baja.

``frps`` corre en la red docker de la central con hostname ``RELAY_HOST``; el
``frpc`` del worker expone su puerto 8080 como ``relay:<puerto>``. Para la
central es un nodo más (``relay:<puerto>``). El token se guarda solo como
sha256 (tabla ``relay_workers``; memoria sin base) y se muestra una vez.
"""

from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from central_api.admin.workers import (
    _errores_admin,
    _respuesta_json,
    reactivar_nodo,
    require_admin,
)
from central_api.db import db_configurado, nueva_sesion
from central_api.settings import get_settings
from central_api.store import ADMIN_NODES, RELAY_WORKERS, WORKER_ALIAS_OVERRIDES

router = APIRouter()


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def usuario_frp(node: str) -> str:
    """Usuario frp esperado para el nodo (frp no admite ``:`` en el usuario)."""
    return node.replace(":", "-")


def _rango_puertos() -> range:
    ini, _, fin = get_settings().relay_port_range.partition("-")
    return range(int(ini), int(fin or ini) + 1)


async def _guardar_relay(node: str) -> None:
    if not db_configurado():
        return
    from sqlalchemy import text

    r = RELAY_WORKERS[node]
    async with nueva_sesion() as sesion:
        await sesion.execute(
            text(
                "INSERT INTO relay_workers (node, alias, port, token_hash) "
                "VALUES (:n, :a, :p, :h) ON CONFLICT (node) DO UPDATE SET "
                "alias = :a, port = :p, token_hash = :h, updated_at = CURRENT_TIMESTAMP"
            ),
            {"n": node, "a": r["alias"], "p": r["port"], "h": r["token_hash"]},
        )


async def cargar_relay_persistidos() -> int:
    """Carga ``relay_workers`` en memoria (arranque). Sin base no hace nada."""
    if not db_configurado():
        return 0
    from sqlalchemy import text

    async with nueva_sesion() as sesion:
        filas = (await sesion.execute(
            text("SELECT node, alias, port, token_hash FROM relay_workers")
        )).all()
    RELAY_WORKERS.clear()
    for n, a, p, h in filas:
        RELAY_WORKERS[str(n)] = {"alias": str(a), "port": int(p), "token_hash": str(h)}
        ADMIN_NODES.add(str(n))
        if a:
            WORKER_ALIAS_OVERRIDES[str(n)] = str(a)
    return len(filas)


async def liberar_relay(node: str) -> None:
    """Libera el puerto y descarta el token del nodo relay (no-op si no lo es)."""
    if RELAY_WORKERS.pop(node, None) is None:
        return
    WORKER_ALIAS_OVERRIDES.pop(node, None)
    if db_configurado():
        from sqlalchemy import text

        async with nueva_sesion() as sesion:
            await sesion.execute(text("DELETE FROM relay_workers WHERE node = :n"), {"n": node})


def _entrega(node: str, token: str) -> dict:
    """Respuesta con el único momento en que el token se ve en claro."""
    s = get_settings()
    port = RELAY_WORKERS[node]["port"]
    frpc_toml = (
        f'serverAddr = "{s.relay_public_addr}"\n'
        f"serverPort = {s.relay_public_port}\n"
        'auth.method = "token"\n'
        'auth.token = "{{ .Envs.FRP_AUTH_TOKEN }}"\n'
        f'user = "{usuario_frp(node)}"\n'
        f'metadatas.relay_token = "{token}"\n'
        "transport.tls.enable = true\n"
        "\n"
        "[[proxies]]\n"
        'name = "worker"\n'
        'type = "tcp"\n'
        'localIP = "bot-worker"\n'
        "localPort = 8080\n"
        f"remotePort = {port}\n"
    )
    return {
        "success": True,
        "node": node,
        "port": port,
        "alias": RELAY_WORKERS[node]["alias"],
        "token": token,
        "frpc_toml": frpc_toml,
        "env": f"WORKER_ADVERTISED_URL=http://{s.relay_host}:{port}",
    }


_EJEMPLO = {
    "success": True, "node": "relay:20001", "port": 20001, "alias": "casa-1",
    "token": "token-visible-una-sola-vez",
    "frpc_toml": 'serverAddr = "frp.example.com"\n...',
    "env": "WORKER_ADVERTISED_URL=http://relay:20001",
}


class RelayBody(BaseModel):
    alias: str = Field(
        max_length=64, description="Alias legible del worker relay.", examples=["casa-1"]
    )


@router.post(
    "/workers/relay",
    status_code=201,
    summary="Dar de alta un worker por relay",
    description=(
        "Reserva el primer puerto libre del rango del relay, agrega el nodo "
        "relay:<puerto> al inventario y devuelve el frpc.toml y las variables del "
        "worker. El token se muestra solo en esta respuesta. Requiere autorización "
        "administrativa."
    ),
    responses={
        **_errores_admin(),
        201: _respuesta_json("Worker relay creado.", _EJEMPLO),
        409: {"description": "No quedan puertos libres en RELAY_PORT_RANGE."},
    },
)
async def alta_relay(body: RelayBody, authorization: str | None = Header(default=None)) -> dict:
    """Reserva puerto y token para un nuevo worker relay."""
    require_admin(authorization)
    alias = " ".join(body.alias.split())
    usados = {r["port"] for r in RELAY_WORKERS.values()}
    puerto = next((p for p in _rango_puertos() if p not in usados), None)
    if puerto is None:
        raise HTTPException(status_code=409, detail="sin puertos libres en RELAY_PORT_RANGE")
    node = f"{get_settings().relay_host}:{puerto}"
    token = secrets.token_urlsafe(32)
    RELAY_WORKERS[node] = {"alias": alias, "port": puerto, "token_hash": hash_token(token)}
    ADMIN_NODES.add(node)
    reactivar_nodo(node)
    if alias:
        WORKER_ALIAS_OVERRIDES[node] = alias
    await _guardar_relay(node)
    return _entrega(node, token)


@router.post(
    "/workers/relay/{node}/token",
    summary="Rotar el token de un worker relay",
    description=(
        "Genera un token nuevo; el anterior deja de valer para nuevos logins del "
        "frpc. Devuelve la misma estructura que el alta. Requiere autorización "
        "administrativa."
    ),
    responses={
        **_errores_admin(),
        200: _respuesta_json("Token rotado.", _EJEMPLO),
        404: {"description": "El nodo no es un worker relay."},
    },
)
async def rotar_token_relay(node: str, authorization: str | None = Header(default=None)) -> dict:
    """Rota el token del worker relay."""
    require_admin(authorization)
    if node not in RELAY_WORKERS:
        raise HTTPException(status_code=404, detail="worker relay inexistente")
    token = secrets.token_urlsafe(32)
    RELAY_WORKERS[node]["token_hash"] = hash_token(token)
    await _guardar_relay(node)
    return _entrega(node, token)
