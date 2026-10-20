"""Acciones del panel para actualizar workers manuales a la última imagen.

- ``POST /admin/workers/{node}/update``: actualiza un worker.
- ``POST /admin/fleet/update-all``: actualiza todos los manuales desactualizados.
- ``GET /admin/fleet/updates``: estado de las solicitudes.

La central solo registra la intención; el updater de cada host la ejecuta
(ver ``central_api.worker_updates``). Aplica solo a workers manuales: los
efímeros EC2 los gestiona el autoscaler.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event, nuevo_uuid7
from central_api.admin.fleet import _version_fields
from central_api.admin.workers import _coerce_node
from central_api.settings import get_settings
from central_api.store import ADMIN_NODES, EPHEMERAL_WORKERS, WORKERS
from central_api.worker_nodes import merge_nodes
from central_api.worker_updates import (
    UPDATE_REQUESTS,
    crear_solicitud,
    objetivo,
    solicitud_activa,
)

router = APIRouter()


class UpdateBody(BaseModel):
    motivo: str = Field(
        default="actualización desde el panel",
        description="Motivo de la actualización (10 a 500 caracteres); queda en auditoría.",
    )


def _nodos_manuales() -> list[str]:
    efimeros = {str(i.get("node") or "") for i in EPHEMERAL_WORKERS.values() if i.get("node")}
    nodos = merge_nodes(get_settings().worker_node_list, ADMIN_NODES)
    return [n for n in nodos if n not in efimeros]


def _exigir_objetivo() -> None:
    if not objetivo()[0]:
        raise HTTPException(
            status_code=409,
            detail="WORKER_IMAGE_REF no está configurada: no hay imagen objetivo a desplegar",
        )


@router.post(
    "/workers/{node}/update",
    summary="Actualizar un worker manual a la última imagen",
    description=(
        "Encola la actualización del worker: se pausa, termina lo que tiene en vuelo "
        "y su updater recrea el contenedor con la imagen objetivo (WORKER_IMAGE_REF). "
        "Solo para workers manuales. Requiere autorización administrativa."
    ),
)
def actualizar_worker(
    node: str,
    body: UpdateBody | None = None,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    actor = require_admin(authorization)
    norm = _coerce_node(node)
    motivo = validar_motivo((body or UpdateBody()).motivo)
    if norm not in _nodos_manuales():
        raise HTTPException(status_code=404, detail="worker manual no está en el inventario")
    _exigir_objetivo()
    if solicitud_activa(norm) is not None:
        raise HTTPException(status_code=409, detail="el worker ya tiene una actualización en curso")
    req = crear_solicitud(norm, solicitud_id=nuevo_uuid7(), actor=actor, motivo=motivo)
    log_event(
        "fleet.worker.update_requested", actor_id=actor, target_type="worker",
        target_id=norm, request_id=request_id or "", reason=motivo,
        metadata={"imagen": req.image, "version": req.app_version},
    )
    return {"success": True, "actualizacion": req.public()}


@router.post(
    "/fleet/update-all",
    summary="Actualizar todos los workers manuales desactualizados",
    description=(
        "Encola la actualización de cada worker manual desactualizado que no tenga una "
        "en curso. Se ejecutan de a WORKER_UPDATE_MAX_PARALLEL para conservar capacidad."
    ),
)
def actualizar_todos(
    body: UpdateBody | None = None,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    actor = require_admin(authorization)
    motivo = validar_motivo((body or UpdateBody()).motivo)
    _exigir_objetivo()
    encoladas, omitidas = [], []
    for nodo in _nodos_manuales():
        if solicitud_activa(nodo) is not None:
            omitidas.append({"node": nodo, "motivo": "actualización en curso"})
        elif _version_fields(WORKERS.get(nodo)).get("outdated") is not True:
            omitidas.append({"node": nodo, "motivo": "ya está actualizado"})
        else:
            encoladas.append(
                crear_solicitud(nodo, solicitud_id=nuevo_uuid7(), actor=actor, motivo=motivo).public()
            )
    log_event(
        "fleet.update_all_requested", actor_id=actor, target_type="fleet",
        request_id=request_id or "", reason=motivo,
        metadata={"encoladas": len(encoladas), "omitidas": len(omitidas)},
    )
    return {"success": True, "encoladas": encoladas, "omitidas": omitidas}


@router.get(
    "/fleet/updates",
    summary="Consultar el estado de las actualizaciones de workers",
)
def ver_actualizaciones(authorization: str | None = Header(default=None)) -> dict:
    require_admin(authorization)
    return {
        "success": True,
        "objetivo": dict(zip(("imagen", "version", "digest"), objetivo(), strict=True)),
        "actualizaciones": [r.public() for r in UPDATE_REQUESTS.values()],
    }
