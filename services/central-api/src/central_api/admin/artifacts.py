"""Eliminación de artifacts de los buckets desde el panel de administración.

Solo opera sobre una lista cerrada de buckets de artefactos (los de
``buckets.py`` más general, cargas y logger). El bucket de backups nunca es
elegible: se excluye de la lista y se rechaza aunque se envíe a mano.
No toca la base de datos: los jobs conservan sus metadatos de artifacts.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from central_api.admin._common import require_admin, validar_motivo
from central_api.admin.audit import log_event
from central_api.buckets import BUCKET_POR_BOT
from central_api.settings import Settings, get_settings

router = APIRouter(prefix="/artifacts")

#: Bucket de backups: nunca se borra desde esta sección.
BUCKET_BACKUPS = "mrbot-bots-api-bkp-db"
#: Tope de objetos por llamada; el panel repite hasta que ``has_more`` sea false.
MAX_OBJETOS_POR_LLAMADA = 5000
CONFIRMACION = "ELIMINAR"


def buckets_elegibles(settings: Settings) -> list[str]:
    nombres = set(BUCKET_POR_BOT.values())
    nombres |= {
        settings.object_storage_bucket,
        settings.object_storage_uploads_bucket,
        settings.object_storage_logger_bucket,
    }
    prohibidos = {BUCKET_BACKUPS, settings.backup_bucket, ""}
    return sorted(nombres - prohibidos)


class ArtifactsPurgeBody(BaseModel):
    buckets: list[str] = Field(min_length=1, max_length=64)
    todos: bool = False
    desde: date | None = None  # inclusive, UTC
    hasta: date | None = None  # inclusive, UTC
    dry_run: bool = True
    motivo: str | None = None
    confirmation_text: str = ""


def _rango(body: ArtifactsPurgeBody) -> tuple[datetime | None, datetime | None]:
    if body.todos:
        return None, None
    if body.desde is None and body.hasta is None:
        raise HTTPException(status_code=400, detail="indicá un rango de fechas o 'todos'")
    if body.desde and body.hasta and body.desde > body.hasta:
        raise HTTPException(status_code=400, detail="desde no puede ser posterior a hasta")
    ini = datetime.combine(body.desde, time.min, tzinfo=timezone.utc) if body.desde else None
    fin = (
        datetime.combine(body.hasta + timedelta(days=1), time.min, tzinfo=timezone.utc)
        if body.hasta else None
    )
    return ini, fin  # [ini, fin)


def _procesar_bucket(
    settings: Settings, bucket: str, ini: datetime | None, fin: datetime | None,
    dry_run: bool, limite: int,
) -> dict[str, Any]:
    from central_api.admin.backups import _cliente_s3

    cliente = _cliente_s3(settings)
    token: str | None = None
    objetos = tamano = borrados = fallidos = 0
    has_more = False
    lote: list[dict[str, str]] = []

    def vaciar() -> None:
        nonlocal borrados, fallidos, lote
        if not lote:
            return
        resp = cliente.delete_objects(Bucket=bucket, Delete={"Objects": lote, "Quiet": True})
        fallidos += len(resp.get("Errors") or [])
        borrados += len(lote) - len(resp.get("Errors") or [])
        lote = []

    while True:
        params: dict[str, Any] = {"Bucket": bucket, "MaxKeys": 1000}
        if token:
            params["ContinuationToken"] = token
        resp = cliente.list_objects_v2(**params)
        for item in resp.get("Contents") or []:
            modificado = item.get("LastModified")
            if ini and modificado < ini:
                continue
            if fin and modificado >= fin:
                continue
            if objetos >= limite:
                has_more = True
                break
            objetos += 1
            tamano += int(item.get("Size") or 0)
            if not dry_run:
                lote.append({"Key": item["Key"]})
                if len(lote) >= 1000:
                    vaciar()
        if has_more:
            break
        token = resp.get("NextContinuationToken")
        if not resp.get("IsTruncated") or not token:
            break
    vaciar()
    return {
        "bucket": bucket, "objetos": objetos, "bytes": tamano,
        "eliminados": borrados, "fallidos": fallidos, "has_more": has_more,
    }


@router.get("/buckets", summary="Listar buckets de artifacts eliminables")
async def listar_buckets(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    require_admin(authorization)
    return {"success": True, "buckets": buckets_elegibles(get_settings()), "excluidos": [BUCKET_BACKUPS]}


@router.post(
    "/purge",
    summary="Eliminar artifacts de buckets por rango de fechas o todos",
    description=(
        "Con dry_run=true solo cuenta objetos y bytes. Para eliminar, dry_run=false, "
        "motivo (mínimo 10 caracteres) y confirmation_text=ELIMINAR. Filtra por fecha de "
        "última modificación (UTC, desde/hasta inclusivos) o todos=true. Procesa hasta "
        f"{MAX_OBJETOS_POR_LLAMADA} objetos por bucket y llamada; repetir mientras has_more sea true. "
        "El bucket de backups está excluido."
    ),
)
async def purgar_artifacts(
    body: ArtifactsPurgeBody, authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    actor = require_admin(authorization)
    settings = get_settings()
    permitidos = set(buckets_elegibles(settings))
    pedidos = list(dict.fromkeys(body.buckets))
    for nombre in pedidos:
        if nombre in {BUCKET_BACKUPS, settings.backup_bucket}:
            raise HTTPException(status_code=400, detail="el bucket de backups no se puede borrar desde esta sección")
        if nombre not in permitidos:
            raise HTTPException(status_code=400, detail=f"bucket no permitido: {nombre}")
    ini, fin = _rango(body)
    if not body.dry_run:
        motivo = validar_motivo(body.motivo)
        if body.confirmation_text.strip().upper() != CONFIRMACION:
            raise HTTPException(status_code=400, detail=f"Para eliminar debés escribir {CONFIRMACION}.")
    resultados = []
    for nombre in pedidos:
        try:
            resultados.append(await asyncio.to_thread(
                _procesar_bucket, settings, nombre, ini, fin, body.dry_run, 10**9 if body.dry_run else MAX_OBJETOS_POR_LLAMADA,
            ))
        except Exception:
            resultados.append({"bucket": nombre, "error": "almacenamiento no disponible"})
    if not body.dry_run:
        log_event(
            "artifacts.purged", actor_id=actor, target_type="buckets", target_id=",".join(pedidos),
            reason=motivo,
            metadata={
                "todos": body.todos, "desde": str(body.desde or ""), "hasta": str(body.hasta or ""),
                "eliminados": sum(r.get("eliminados", 0) for r in resultados),
                "fallidos": sum(r.get("fallidos", 0) for r in resultados),
            },
        )
    return {
        "success": True, "dry_run": body.dry_run, "resultados": resultados,
        "has_more": any(r.get("has_more") for r in resultados),
    }
