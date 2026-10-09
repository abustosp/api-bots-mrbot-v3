"""Reportes administrativos compatibles con las rutas V1.

V3 informa desde las tablas canónicas ``jobs``/``users``. No consulta vistas
legacy ni devuelve request payloads, credenciales, URLs de objetos o secretos.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, time, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from central_api.admin._common import require_admin
from central_api.admin.audit import log_event
from central_api.db import db_configurado, nueva_sesion
from central_api.store import JOBS

router = APIRouter()
REPORT_TYPES = frozenset({"resumen", "desglose_cuit"})
MAX_PREVIEW_ROWS = 200


def _parse_date(value: str, field: str) -> datetime:
    try:
        return datetime.combine(datetime.strptime(value.strip(), "%Y-%m-%d").date(), time.min, timezone.utc)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Formato inválido para {field}. Usa YYYY-MM-DD.") from exc


def _range(desde: str, hasta: str) -> tuple[datetime, datetime]:
    start = _parse_date(desde, "fecha_desde")
    end = _parse_date(hasta, "fecha_hasta").replace(hour=23, minute=59, second=59, microsecond=999999)
    if start > end:
        raise HTTPException(status_code=400, detail="fecha_desde no puede ser mayor a fecha_hasta.")
    return start, end


def _report_type(tipo: str) -> str:
    normalized = (tipo or "resumen").strip()
    if normalized not in REPORT_TYPES:
        raise HTTPException(status_code=400, detail="Tipo de reporte inválido.")
    return normalized


async def _report_rows_db(kind: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Agrupa sólo datos operativos permitidos desde la fuente canónica."""
    from sqlalchemy import text

    cuit_column = (
        ", COALESCE(j.credential_metadata ->> 'cuit_representado', "
        "j.credential_metadata ->> 'cuit', "
        "j.credential_metadata ->> 'cuit_representante') AS cuit_representado"
        if kind == "desglose_cuit" else ""
    )
    cuit_group = ", cuit_representado" if kind == "desglose_cuit" else ""
    statement = text(
        "SELECT j.user_id::text AS user_id, u.email AS user_email, "
        "(j.bot || ':' || j.operation) AS servicio"
        f"{cuit_column}, "
        "SUM(CASE WHEN j.status = 'COMPLETO' AND COALESCE(j.result, 'OK') <> 'ERROR' "
        "THEN 1 ELSE 0 END)::integer AS requests_exitosas, "
        "SUM(CASE WHEN j.status <> 'COMPLETO' OR COALESCE(j.result, 'OK') = 'ERROR' "
        "THEN 1 ELSE 0 END)::integer AS requests_no_exitosas "
        "FROM jobs j JOIN users u ON u.id = j.user_id "
        "WHERE j.created_at >= :start AND j.created_at <= :end "
        "GROUP BY j.user_id, u.email, j.bot, j.operation"
        f"{cuit_group} ORDER BY user_id, servicio{cuit_group}"
    )
    async with nueva_sesion() as session:
        rows = (await session.execute(statement, {"start": start, "end": end})).mappings().all()
    return [{key: value for key, value in dict(row).items()} for row in rows]


def _report_rows_memory(kind: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Mismo contrato para el modo de desarrollo sin PostgreSQL."""
    from central_api.admin.users import USERS
    from central_api.api.dependencies import JOB_META

    groups: dict[tuple[str, str, str | None], dict[str, Any]] = {}
    for job in JOBS.values():
        created = getattr(job, "created_at", None)
        if created is None:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if not start <= created <= end:
            continue
        meta = JOB_META.get(job.id, {})
        user_id = str(meta.get("owner", ""))
        cuit = None
        if kind == "desglose_cuit":
            data = meta.get("data") or {}
            if isinstance(data, dict):
                cuit = data.get("cuit_representado") or data.get("cuit")
        key = (user_id, f"{job.bot}:{job.operation}", str(cuit) if cuit else None)
        row = groups.setdefault(
            key,
            {
                "user_id": user_id,
                "user_email": getattr(USERS.get(user_id), "email", ""),
                "servicio": key[1],
                "requests_exitosas": 0,
                "requests_no_exitosas": 0,
            },
        )
        if kind == "desglose_cuit":
            row["cuit_representado"] = key[2]
        if job.status == "COMPLETO" and (not isinstance(job.result, dict) or job.result.get("result") != "ERROR"):
            row["requests_exitosas"] += 1
        else:
            row["requests_no_exitosas"] += 1
    return sorted(groups.values(), key=lambda row: (row["user_id"], row["servicio"], row.get("cuit_representado") or ""))


async def _rows(kind: str, start: datetime, end: datetime) -> list[dict[str, Any]]:
    if not db_configurado():
        return _report_rows_memory(kind, start, end)
    try:
        return await _report_rows_db(kind, start, end)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Servicio no disponible") from exc


def _csv_response(kind: str, desde: str, hasta: str, rows: list[dict[str, Any]]) -> StreamingResponse:
    columns = ["user_id", "user_email", "servicio"]
    if kind == "desglose_cuit":
        columns.append("cuit_representado")
    columns.extend(["requests_exitosas", "requests_no_exitosas"])
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    filename = f"reporte_{kind}_{desde}_{hasta}.csv".replace("/", "-")
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "private, no-store"},
    )


@router.get("/reporting")
async def reporting(
    desde: str | None = Query(default=None),
    hasta: str | None = Query(default=None),
    tipo: str = Query(default="resumen"),
    fecha_desde: str | None = Query(default=None),
    fecha_hasta: str | None = Query(default=None),
    tipo_reporte: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Preview V1-compatible limitado a 200 filas, sin payloads sensibles."""
    actor = require_admin(authorization)
    raw_desde, raw_hasta = (desde or fecha_desde or "").strip(), (hasta or fecha_hasta or "").strip()
    kind = _report_type(tipo_reporte or tipo)
    rows: list[dict[str, Any]] = []
    if bool(raw_desde or raw_hasta):
        if not raw_desde or not raw_hasta:
            raise HTTPException(status_code=400, detail="Debes indicar fecha de inicio y fin.")
        start, end = _range(raw_desde, raw_hasta)
        rows = await _rows(kind, start, end)
        log_event("reporting.previewed", actor_id=actor, target_type="report", target_id=kind, metadata={"desde": raw_desde, "hasta": raw_hasta, "rows": len(rows)})
    columns = list(rows[0]) if rows else (["user_id", "user_email", "servicio"] + (["cuit_representado"] if kind == "desglose_cuit" else []) + ["requests_exitosas", "requests_no_exitosas"])
    return {
        "success": True,
        "report_type": kind,
        "columns": columns,
        "rows": rows[:MAX_PREVIEW_ROWS],
        "total_rows": len(rows),
        "preview_truncated": len(rows) > MAX_PREVIEW_ROWS,
        "totals": {
            "requests_exitosas": sum(int(row["requests_exitosas"]) for row in rows),
            "requests_no_exitosas": sum(int(row["requests_no_exitosas"]) for row in rows),
        },
        "download_url": f"/admin/reporting/download?desde={raw_desde}&hasta={raw_hasta}&tipo={kind}" if raw_desde and raw_hasta else "",
    }


@router.get("/reporting/download")
async def download_reporting(
    desde: str | None = Query(default=None),
    hasta: str | None = Query(default=None),
    tipo: str = Query(default="resumen"),
    fecha_desde: str | None = Query(default=None),
    fecha_hasta: str | None = Query(default=None),
    tipo_reporte: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    actor = require_admin(authorization)
    raw_desde, raw_hasta = (desde or fecha_desde or "").strip(), (hasta or fecha_hasta or "").strip()
    kind = _report_type(tipo_reporte or tipo)
    start, end = _range(raw_desde, raw_hasta)
    rows = await _rows(kind, start, end)
    log_event("reporting.downloaded", actor_id=actor, target_type="report", target_id=kind, metadata={"desde": raw_desde, "hasta": raw_hasta, "rows": len(rows)})
    return _csv_response(kind, raw_desde, raw_hasta, rows)


@router.get("/reports/download")
async def download_reporting_legacy(
    fecha_desde: str,
    fecha_hasta: str,
    tipo_reporte: str = "resumen",
    authorization: str | None = Header(default=None),
) -> StreamingResponse:
    """Alias legacy exacto de la descarga CSV V1."""
    return await download_reporting(
        desde=fecha_desde,
        hasta=fecha_hasta,
        tipo=tipo_reporte,
        fecha_desde=None,
        fecha_hasta=None,
        tipo_reporte=None,
        authorization=authorization,
    )
