"""Panel de workers: alta de IPs en caliente + monitoreo de la flota.

- ``GET /admin/workers``: inventario (env + panel) con estado derivado por
  nodo: origen, registrado, estado, ejecución/capacidad, último latido.
- ``POST /admin/workers``: agrega una IP:puerto al inventario en caliente
  (sondea al worker best-effort e informa si responde). Mutación protegida
  con ``ADMIN_TOKEN`` (bearer); sin token configurado es 403.
- ``DELETE /admin/workers/{nodo}``: quita el nodo del inventario del panel
  y marca su entrada DRENANDO para que el scheduler deje de asignarle.
- ``GET /admin/workers/panel``: página HTML mínima con la misma información,
  formulario de alta y baja, auto-refresco cada 15 s.

El estado deriva de los umbrales ya configurados
(``worker_degraded_after_seconds`` / ``worker_down_after_seconds``), igual
criterio que el selector: SANO+SATURADO exigen latido fresco.
"""

from __future__ import annotations

import hmac
from datetime import datetime, timedelta

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from central_api.db import db_configurado, nueva_sesion
from central_api.scheduler import dispatcher
from central_api.settings import get_settings
from central_api.store import ADMIN_NODES, WORKERS, WorkerEntry, utcnow
from central_api.worker_nodes import merge_nodes, node_from_url

router = APIRouter()


async def _fila_worker_db(node: str) -> dict | None:
    """Fila del worker en PostgreSQL por endpoint; ``None`` sin base/fila.

    Lee ``models.Worker`` (fuente canónica con base configurada); nunca
    expone secretos, solo inventario y telemetría ya reportada.
    """
    if not db_configurado():
        return None
    try:
        from sqlalchemy import select

        from central_api.models.fleet import Worker
    except Exception:  # noqa: BLE001 - sin modelos, solo memoria
        return None
    try:
        async with nueva_sesion() as sesion:
            fila = (await sesion.execute(
                select(Worker).where(Worker.endpoint == node)
            )).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - sin base, solo memoria
        return None
    if fila is None:
        return None
    return {
        "registrado": True,
        "worker_id": str(fila.id),
        "estado": str(fila.status),
        "en_ejecucion": int(fila.running_jobs or 0),
        "capacidad": int(fila.capacity or 5),
        "ultimo_latido": (
            fila.last_heartbeat_at.isoformat() if fila.last_heartbeat_at else None
        ),
    }


async def vista_worker(node: str) -> dict:
    """Vista del nodo: memoria + fila PG cuando hay base configurada."""
    vista = worker_overview(node)
    fila = await _fila_worker_db(node)
    if fila is not None:
        vista = {**vista, **fila, "origen": vista.get("origen", "env"),
                 "fuente": "postgresql"}
    else:
        vista = {**vista, "fuente": "memoria"}
    return vista


class AddWorkerBody(BaseModel):
    node: str = Field(
        description="Dirección ip:port del worker, p.ej. 10.0.0.13:8080 (con o sin http://)"
    )


def derive_state(entry: WorkerEntry | None) -> str:
    """Estado de flota derivado: SANO, SATURADO, DEGRADADO, CAIDO, REGISTRANDO o DESCONOCIDO."""
    settings = get_settings()
    now: datetime = utcnow()
    if entry is None:
        return "DESCONOCIDO"
    if entry.status == "DRENANDO":
        return "DRENANDO"
    if entry.last_heartbeat_at is None:
        return "REGISTRANDO"
    age = now - entry.last_heartbeat_at
    if age > timedelta(seconds=settings.worker_down_after_seconds):
        return "CAIDO"
    if age > timedelta(seconds=settings.worker_degraded_after_seconds):
        return "DEGRADADO"
    if entry.running_jobs + entry.reserved_slots >= entry.capacity:
        return "SATURADO"
    return "SANO"


def worker_overview(node: str) -> dict:
    settings = get_settings()
    entry = WORKERS.get(node)
    return {
        "node": node,
        "worker_id": entry.worker_id if entry else "",
        "origen": "panel" if node in ADMIN_NODES else "env",
        "registrado": entry is not None,
        "estado": derive_state(entry),
        "en_ejecucion": entry.running_jobs if entry else 0,
        "capacidad": entry.capacity if entry else settings.worker_capacity,
        "sellado": bool(entry and entry.sealed_pubkey_pem),
        "ultimo_latido": entry.last_heartbeat_at.isoformat() if entry and entry.last_heartbeat_at else None,
    }


def require_admin(authorization: str | None) -> None:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(status_code=403, detail="ADMIN_TOKEN no configurado")
    scheme, _, presented = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not presented:
        raise HTTPException(status_code=401, detail="Falta bearer ADMIN_TOKEN")
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=403, detail="ADMIN_TOKEN inválido")


def _coerce_node(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="node vacío")
    if "://" in text:
        return node_from_url(text)
    if ":" not in text:
        raise HTTPException(status_code=400, detail="node debe ser ip:port")
    return text


@router.get("/workers")
async def list_workers() -> dict:
    settings = get_settings()
    nodes = merge_nodes(settings.worker_node_list, ADMIN_NODES)
    return {"success": True, "workers": [await vista_worker(n) for n in nodes]}


@router.post("/workers", status_code=201)
async def add_worker(body: AddWorkerBody, authorization: str | None = Header(default=None)) -> dict:
    require_admin(authorization)
    try:
        node = _coerce_node(body.node)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    reachable: bool | None = None
    detail: str | None = None
    try:
        status = await dispatcher.probe_worker(node)
        reachable = True
        detail = str(status.get("state", status.get("status", "ok")))
    except Exception as exc:  # el nodo puede levantarse después; igual se registra
        reachable = False
        detail = type(exc).__name__
    ADMIN_NODES.add(node)
    return {
        "success": True,
        "node": node,
        "alcanzable": reachable,
        "detalle": detail,
        "vista": worker_overview(node),
    }


@router.delete("/workers/{node}")
def remove_worker(node: str, authorization: str | None = Header(default=None)) -> dict:
    require_admin(authorization)
    try:
        norm = _coerce_node(node)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    ADMIN_NODES.discard(norm)
    entry = WORKERS.get(norm)
    if entry is not None:
        entry.status = "DRENANDO"
    return {"success": True, "node": norm}


@router.get("/workers/panel", response_class=HTMLResponse)
def workers_panel() -> str:
    settings = get_settings()
    nodes = merge_nodes(settings.worker_node_list, ADMIN_NODES)
    rows = "\n".join(
        _panel_row(worker_overview(n)) for n in nodes
    ) or '<tr><td colspan="7">Sin workers inventariados. Agregue uno abajo.</td></tr>'
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="15">
<title>Workers — MrBot central</title>
<style>
body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; margin: 2rem; color: #1a1a1a; max-width: 70rem; }}
table {{ border-collapse: collapse; width: 100%; margin: 1rem 0; }}
th, td {{ border: 1px solid #ccc; padding: 0.4rem 0.6rem; text-align: left; font-size: 0.9rem; }}
th {{ background: #f0f0f0; }}
.estado {{ font-weight: 600; }}
form {{ margin-top: 1.5rem; }}
input, button {{ font-size: 0.9rem; padding: 0.35rem 0.6rem; }}
.muted {{ color: #555; font-size: 0.85rem; }}
</style>
</head>
<body>
<h1>Workers</h1>
<p class="muted">Alta de IPs en caliente y monitoreo. Mutaciones con bearer ADMIN_TOKEN.</p>
<table>
<thead><tr><th>Nodo</th><th>Origen</th><th>Estado</th><th>Ejec/Cap</th><th>Sellado</th><th>Último latido</th><th></th></tr></thead>
<tbody>{rows}</tbody>
</table>
<form id="alta">
<input id="node" name="node" placeholder="10.0.0.13:8080" required>
<button type="submit">Agregar worker</button>
</form>
<p class="muted" id="msg"></p>
<script>
const msg = document.getElementById("msg");
function token() {{
  let t = sessionStorage.getItem("admin_token");
  if (!t) {{ t = prompt("ADMIN_TOKEN:") || ""; sessionStorage.setItem("admin_token", t); }}
  return t;
}}
document.getElementById("alta").addEventListener("submit", async (ev) => {{
  ev.preventDefault();
  const node = document.getElementById("node").value;
  const r = await fetch("/admin/workers", {{
    method: "POST",
    headers: {{"Content-Type": "application/json", "Authorization": "Bearer " + token()}},
    body: JSON.stringify({{node}})
  }});
  msg.textContent = r.ok ? "Worker agregado." : "Error " + r.status + ": " + await r.text();
  if (r.ok) location.reload();
}});
async function baja(node) {{
  const r = await fetch("/admin/workers/" + encodeURIComponent(node), {{
    method: "DELETE", headers: {{"Authorization": "Bearer " + token()}}
  }});
  msg.textContent = r.ok ? "Worker dado de baja." : "Error " + r.status;
  if (r.ok) location.reload();
}}
</script>
</body>
</html>"""


def _panel_row(view: dict) -> str:
    node = view["node"]
    return (
        "<tr>"
        f"<td>{node}</td>"
        f"<td>{view['origen']}</td>"
        f"<td class=\"estado\">{view['estado']}</td>"
        f"<td>{view['en_ejecucion']}/{view['capacidad']}</td>"
        f"<td>{'sí' if view['sellado'] else 'no'}</td>"
        f"<td>{view['ultimo_latido'] or '—'}</td>"
        f"<td><button onclick=\"baja('{node}')\">Dar de baja</button></td>"
        "</tr>"
    )
