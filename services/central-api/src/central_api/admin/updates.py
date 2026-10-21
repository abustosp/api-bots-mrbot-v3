"""Acciones del panel para actualizar workers manuales a la última imagen.

- ``POST /admin/workers/{node}/update``: actualiza un worker (a la última versión del
  registry o a la ``version`` elegida en el desplegable del panel).
- ``GET /admin/fleet/versions``: versiones publicadas en el registry y cuál es la última.
- ``POST /admin/fleet/update-all``: actualiza todos los manuales desactualizados.
- ``GET /admin/fleet/updates``: estado de las solicitudes.

La central solo registra la intención; el updater de cada host la ejecuta
(ver ``central_api.worker_updates``). Aplica solo a workers manuales: los
efímeros EC2 los gestiona el autoscaler.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from central_api import registry
from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event, nuevo_uuid7
from central_api.admin.fleet import _latest_origen, _version_fields
from central_api.admin.workers import _coerce_node
from central_api.settings import get_settings
from central_api.store import ADMIN_NODES, EPHEMERAL_WORKERS, WORKERS
from central_api.worker_nodes import merge_nodes
from central_api.worker_updates import (
    UPDATE_REQUESTS,
    VersionNoDisponible,
    cancelar_solicitud,
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
    version: str | None = Field(
        default=None,
        description=(
            "Versión puntual a aplicar (tag AAAAMMDD_HHMMSS publicado en el registry). "
            "Sin valor se aplica la última. Permite volver a una versión anterior."
        ),
        examples=["20261021_133600"],
    )


def _nodos_manuales() -> list[str]:
    efimeros = {str(i.get("node") or "") for i in EPHEMERAL_WORKERS.values() if i.get("node")}
    nodos = merge_nodes(get_settings().worker_node_list, ADMIN_NODES)
    return [n for n in nodos if n not in efimeros]


def _exigir_objetivo(version: str | None = None) -> None:
    """409 si no hay imagen objetivo o la versión pedida no es aplicable."""
    registry.refrescar()
    try:
        imagen = objetivo(version)[0]
    except VersionNoDisponible as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not imagen:
        raise HTTPException(
            status_code=409,
            detail=(
                "no hay imagen objetivo a desplegar: configure WORKER_IMAGE_REPO "
                "(automático desde el registry) o WORKER_IMAGE_REF"
            ),
        )


@router.post(
    "/workers/{node}/update",
    summary="Actualizar un worker manual a la última versión o a una elegida",
    description=(
        "Encola la actualización del worker: se pausa, termina lo que tiene en vuelo "
        "y su updater recrea el contenedor con la imagen objetivo: la última versión "
        "del registry (WORKER_IMAGE_REPO) o la `version` indicada, que debe estar publicada. "
        "Sin WORKER_IMAGE_REPO se usa WORKER_IMAGE_REF. Solo para workers manuales. "
        "Requiere autorización administrativa."
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
    version = (body or UpdateBody()).version
    _exigir_objetivo(version)
    if solicitud_activa(norm) is not None:
        raise HTTPException(status_code=409, detail="el worker ya tiene una actualización en curso")
    req = crear_solicitud(norm, solicitud_id=nuevo_uuid7(), actor=actor, motivo=motivo, version=version)
    log_event(
        "fleet.worker.update_requested", actor_id=actor, target_type="worker",
        target_id=norm, request_id=request_id or "", reason=motivo,
        metadata={"imagen": req.image, "version": req.app_version, "version_elegida": bool(version)},
    )
    return {"success": True, "actualizacion": req.public()}


@router.delete(
    "/workers/{node}/update",
    summary="Cancelar la actualización pendiente de un worker",
    description=(
        "Cancela una solicitud en cola o en drenaje (se reanuda el worker si lo pausó la "
        "solicitud). No interrumpe una actualización que el updater ya está ejecutando. "
        "Requiere autorización administrativa."
    ),
)
def cancelar_actualizacion(
    node: str,
    authorization: str | None = Header(default=None),
    request_id: str | None = Header(default=None, alias="X-Request-ID"),
) -> dict:
    actor = require_admin(authorization)
    norm = _coerce_node(node)
    req = cancelar_solicitud(norm, actor)
    if req is None:
        raise HTTPException(
            status_code=409,
            detail="no hay una actualización cancelable (no existe o ya se está ejecutando)",
        )
    log_event(
        "fleet.worker.update_cancelled", actor_id=actor, target_type="worker",
        target_id=norm, request_id=request_id or "", reason="cancelada desde el panel",
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


@router.get(
    "/fleet/versions",
    summary="Listar las versiones del worker publicadas en el registry",
    description=(
        "Consulta (con caché) los tags de release del repositorio del worker y marca cuál es "
        "la última. Alimenta el desplegable de versión del panel. Si el registry no responde "
        "se devuelve la última lista conocida y el error. Requiere autorización administrativa."
    ),
)
def ver_versiones(authorization: str | None = Header(default=None), refrescar: bool = False) -> dict:
    require_admin(authorization)
    estado = registry.refrescar(force=refrescar)
    tags = registry.versiones()
    ultima = tags[0] if tags else None
    en_uso: dict[str, list[str]] = {}
    for nodo in _nodos_manuales():
        entrada = WORKERS.get(nodo)
        version = str(getattr(entrada, "app_version", "") or "").strip()
        if version:
            en_uso.setdefault(version, []).append(nodo)
    return {
        "success": True,
        "habilitado": registry.habilitado(),
        "repo": registry.repo() or None,
        "latest": ultima,
        "origen": _latest_origen(),
        "consultado": estado.consultado.isoformat() if estado.consultado else None,
        "error": estado.error or None,
        "versiones": [
            {"version": t, "es_latest": t == ultima, "workers": sorted(en_uso.get(t, []))}
            for t in tags[:30]
        ],
    }
