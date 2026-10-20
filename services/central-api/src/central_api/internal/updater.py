"""Rutas internas del updater de host (``services/worker-updater``).

El updater consulta si hay una actualización pendiente para su nodo y
reporta el resultado. Se autentica con ``UPDATER_TOKEN`` (Bearer); sin token
configurado en la central las rutas responden 403. No usa el token de
servicio del worker: el updater tiene acceso a Docker y no debe compartir
credenciales con él.
"""

from __future__ import annotations

import hmac

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from central_api.admin.audit import log_event
from central_api.settings import get_settings
from central_api.worker_updates import ESTADOS_REPORTABLES, comando_para, registrar_resultado

router = APIRouter(prefix="/updater")


def _exigir_updater(authorization: str | None) -> None:
    token = get_settings().updater_token
    if not token:
        raise HTTPException(status_code=403, detail="UPDATER_TOKEN no configurado")
    scheme, _, presentado = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not presentado:
        raise HTTPException(status_code=401, detail="Falta bearer UPDATER_TOKEN")
    if not hmac.compare_digest(presentado, token):
        raise HTTPException(status_code=403, detail="UPDATER_TOKEN inválido")


class ReportBody(BaseModel):
    node: str = Field(min_length=3, max_length=255)
    request_id: str = Field(min_length=1, max_length=64)
    estado: str = Field(description="HECHO, FALLIDO o REVERTIDO.")
    error: str = Field(default="", max_length=500)


@router.get("/command", summary="Comando pendiente para el nodo del updater")
def obtener_comando(
    node: str = Query(min_length=3, max_length=255),
    authorization: str | None = Header(default=None),
) -> dict:
    _exigir_updater(authorization)
    return comando_para(node)


@router.post("/report", summary="Resultado de una actualización")
def reportar(body: ReportBody, authorization: str | None = Header(default=None)) -> dict:
    _exigir_updater(authorization)
    if body.estado not in ESTADOS_REPORTABLES:
        raise HTTPException(status_code=422, detail=f"estado debe ser uno de {ESTADOS_REPORTABLES}")
    req = registrar_resultado(body.node, body.request_id, body.estado, body.error)
    if req is None:
        raise HTTPException(status_code=409, detail="no hay una actualización activa con ese id")
    log_event(
        f"fleet.worker.update_{body.estado.lower()}", actor_id=f"updater:{body.node}",
        actor_type="service", target_type="worker", target_id=body.node,
        result="success" if body.estado == "HECHO" else "failure",
        reason=body.error, metadata={"request_id": body.request_id, "version": req.app_version},
    )
    return {"success": True, "estado": req.status}
