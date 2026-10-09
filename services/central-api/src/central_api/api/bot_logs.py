"""Historial de ejecuciones por bot: reemplazo V3 de los ``POST .../logs`` de V1.

V1 tenía una tabla ``Consulta*Log`` por bot y 27 endpoints ``/logs``. En V3 no
existen esas tablas: cada ejecución es una fila de ``jobs`` (o una entrada de
``JOBS``/``JOB_META`` en modo memoria). Este módulo expone los mismos 27 paths
bajo ``/api/v3`` y arma cada registro proyectando el job.

Decisiones de diseño:

- Autenticación con ``require_api_principal``; solo se listan jobs propios.
- ``id`` es el ``job_id`` (string). ``status`` conserva el vocabulario V1
  (``success``/``error``) y ``estado_v3`` el estado real del job:
  ``COMPLETO`` -> ``success``; ``FALLIDO``/``CANCELADO`` -> ``error``; jobs
  vivos (``PENDIENTE``/``ASIGNADO``/``CORRIENDO``) -> ``pending``.
- Proyección por whitelist: nunca se leen ``credential_ciphertext``,
  ``credentials`` ni el payload crudo hacia la respuesta. Solo salen campos
  escalares conocidos (CUITs, período, medio de pago, denominación). El mensaje
  de error es el mensaje público de clase, nunca texto del worker.
- Filtros V1: ``Fecha_desde``/``Fecha_hasta`` (``YYYYMMDD``, ``Fecha_hasta``
  inclusiva), ``Cuit_representante``, ``Cuit_representado``, ``Cuit`` (ARBA) y
  ``Medio_pago`` (VEP, VEP-CCMA). Cada path acepta solo los filtros de su
  modelo V1: Siper sin ``Cuit_representado``, AGIP solo ``Cuit_representado``,
  ARBA usa ``Cuit``, SRT filtra ``Cuit_representado`` con ``LIKE`` sobre el
  CSV de CUITs consultados.
- Con PostgreSQL configurado se lee la tabla ``jobs`` (sin columnas secretas);
  sin base se usa la rama en memoria, igual que ``jobs.py``.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, create_model

from central_api.api.dependencies import JOB_META, require_api_principal
from central_api.db import db_configurado, nueva_sesion
from central_api.security.permissions import principal_has
from central_api.security.principals import ApiPrincipal
from central_api.security.secret_redaction import CLASS_MESSAGES, public_error
from central_api.store import JOBS

router = APIRouter()

LIVE_STATUSES = ("PENDIENTE", "ASIGNADO", "CORRIENDO")
_STATUS_V1 = {"COMPLETO": "success", "FALLIDO": "error", "CANCELADO": "error"}
_MAX_ROWS = 5000
_MAX_SCAN_ROWS = 50000
_MAX_TEXT = 256
_CODE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")
_FECHA_RE = re.compile(r"^\d{8}$")

_REPRESENTANTE_KEYS = ("cuit_representante", "cuit_inicio_sesion", "cuit_login")
_REPRESENTADO_KEYS = ("representado_cuit", "cuit_representado")


@dataclass(frozen=True)
class LogSpec:
    """Una ruta ``/logs`` de V1: qué jobs lista, qué filtra y qué devuelve."""

    path: str
    targets: tuple[tuple[str, Optional[tuple[str, ...]]], ...]
    filters: tuple[str, ...]
    fields: tuple[str, ...]
    medio_pago_default: Optional[str] = None


_REP_REPDO = ("Cuit_representante", "Cuit_representado")
_CUITS = ("cuit_representante", "cuit_representado")

# path V3 (sin prefijo /api/v3) -> bots/operaciones. ``None`` = todas las
# operaciones del bot. Los bots y operaciones salen de BOT_ROUTE_ALIASES.
LOG_SPECS: tuple[LogSpec, ...] = (
    LogSpec("/aportes-en-linea/logs", (("aportes_en_linea", ("descargar",)),),
            _REP_REPDO, _CUITS),
    LogSpec("/ccma/logs", (("ccma", ("consultar",)),), _REP_REPDO, _CUITS),
    LogSpec("/certificado-mipyme/logs",
            (("certificado_mipyme", ("descargar",)),), _REP_REPDO,
            (*_CUITS, "opciones_encontradas")),
    LogSpec("/controladores-fiscales/logs",
            (("controladores_fiscales", ("presentar",)),),
            ("Cuit_representante",),
            ("cuit_representante", "cantidad_archivos")),
    LogSpec("/declaracion-en-linea/logs",
            (("declaracion_en_linea", ("consultar",)),), _REP_REPDO,
            (*_CUITS, "representado_nombre", "periodo_desde",
             "periodo_hasta")),
    LogSpec("/facturometro/logs", (("facturometro", ("consultar",)),),
            ("Cuit_representante",), ()),
    LogSpec("/hacienda/logs", (("hacienda", ("consultar",)),), _REP_REPDO,
            _CUITS),
    LogSpec("/libros_iva/logs",
            (("libros_portal_iva", ("descargar_libros", "descargar_ddjj")),),
            _REP_REPDO,
            (*_CUITS, "denominacion", "periodo_desde", "periodo_hasta",
             "periodos_descargados", "periodos_error")),
    LogSpec("/liquidacion_granos/logs",
            (("liquidacion_granos", ("consultar",)),), _REP_REPDO, _CUITS),
    LogSpec("/mis_comprobantes/logs",
            (("mis_comprobantes",
              ("consulta", "consultar", "solicitar")),),
            _REP_REPDO, _CUITS),
    LogSpec("/mis_facilidades/logs", (("mis_facilidades", ("consultar",)),),
            _REP_REPDO, ()),
    LogSpec("/mis_retenciones/logs", (("mis_retenciones", ("consultar",)),),
            _REP_REPDO,
            (*_CUITS, "denominacion", "periodo_desde", "periodo_hasta")),
    LogSpec("/moa/logs", (("moa", ("consultar",)),), _REP_REPDO,
            (*_CUITS, "despachos")),
    LogSpec("/pago_devoluciones/logs",
            (("pago_devoluciones", ("consultar",)),), _REP_REPDO,
            (*_CUITS, "archivo_nombre")),
    LogSpec("/portal_iva/logs", (("portal_iva", ("descargar",)),), _REP_REPDO,
            (*_CUITS, "denominacion", "periodo")),
    LogSpec("/portal_iva/carga/logs",
            (("carga_portal_iva", ("cargar",)),), _REP_REPDO,
            (*_CUITS, "denominacion", "periodo")),
    LogSpec("/rcel/logs", (("rcel", ("descargar",)),), _REP_REPDO, _CUITS),
    LogSpec("/retenciones_percepciones_iibb/agip/logs",
            (("retper_iibb_agip", ("consultar",)),), ("Cuit_representado",),
            ("usuario", "cuit_representado", "denominacion", "periodo_desde",
             "periodo_hasta")),
    LogSpec("/retenciones_percepciones_iibb/arba/logs",
            (("arba", ("descargar",)),), ("Cuit",),
            ("cuit", "denominacion", "periodo")),
    LogSpec("/retenciones_percepciones_iibb/misiones/logs",
            (("retper_iibb_misiones", ("consultar",)),), _REP_REPDO,
            (*_CUITS, "denominacion", "periodo_desde", "periodo_hasta")),
    LogSpec("/sct/logs", (("sct", ("consultar",)),), _REP_REPDO, _CUITS),
    LogSpec("/sct/compensaciones/logs",
            (("compensaciones", ("consultar",)),), _REP_REPDO, _CUITS),
    LogSpec("/sifere/logs", (("sifere", ("consultar",)),), _REP_REPDO,
            (*_CUITS, "periodo")),
    LogSpec("/siper/logs", (("siper", ("consultar",)),),
            ("Cuit_representante",), ("cuit_representante",)),
    LogSpec("/srt/alicuotas/logs", (("srt", ("consultar_alicuotas",)),),
            _REP_REPDO, ("cuit_representante", "cuits_consultados")),
    LogSpec("/vep-ccma/logs", (("vep_ccma", ("generar",)),),
            (*_REP_REPDO, "Medio_pago"), (*_CUITS, "medio_pago")),
    LogSpec("/vep/logs", (("vep_archivo", ("generar",)),),
            ("Cuit_representante", "Medio_pago"),
            ("cuit_representante", "medio_pago"),
            medio_pago_default="internet_banking"),
)

_COMMON_FIELDS = ("job_id", "estado_v3", "bot", "operation", "resultado",
                  "error_code")
_FIELD_TYPES: dict[str, Any] = {
    "cantidad_archivos": Optional[int],
    "opciones_encontradas": Optional[list[str]],
    "despachos": Optional[list[str]],
    "periodos_descargados": Optional[list[str]],
    "periodos_error": Optional[list[str]],
}


# --------------------------------------------------------------------------
# Modelos de request/response generados por path
# --------------------------------------------------------------------------

_FILTER_DESCRIPTIONS = {
    "Fecha_desde": "Fecha de inicio en formato YYYYMMDD.",
    "Fecha_hasta": "Fecha de fin en formato YYYYMMDD (inclusiva).",
    "Cuit_representante": "CUIT del representante (11 dígitos sin guiones).",
    "Cuit_representado": "CUIT del representado (11 dígitos sin guiones).",
    "Cuit": "CUIT consultado (11 dígitos sin guiones).",
    "Medio_pago": "Medio de pago (por ejemplo internet_banking).",
}


def _slug(path: str) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "_", path.strip("/")).strip("_")


def _request_model(spec: LogSpec) -> type[BaseModel]:
    from pydantic import Field

    fields: dict[str, Any] = {}
    for name in ("Fecha_desde", "Fecha_hasta", *spec.filters):
        fields[name] = (
            Optional[str],
            Field(default=None, description=_FILTER_DESCRIPTIONS[name]),
        )
    return create_model(
        f"Logs_request_{_slug(spec.path)}",
        __config__=ConfigDict(extra="ignore"),
        **fields,
    )


def _record_model(spec: LogSpec) -> type[BaseModel]:
    fields: dict[str, Any] = {
        "id": (str, ...),
        "timestamp": (datetime, ...),
        "status": (str, ...),
        "error_message": (Optional[str], None),
    }
    for name in (*spec.fields, *_COMMON_FIELDS):
        if name not in fields:
            fields[name] = (_FIELD_TYPES.get(name, Optional[str]), None)
    return create_model(f"Log_registry_{_slug(spec.path)}", **fields)


def _response_model(spec: LogSpec, record: type[BaseModel]) -> type[BaseModel]:
    return create_model(
        f"Logs_response_{_slug(spec.path)}", registros=(list[record], ...)
    )


# --------------------------------------------------------------------------
# Proyección de jobs a registros
# --------------------------------------------------------------------------


@dataclass
class _Row:
    """Vista mínima y sin secretos de un job (memoria o PostgreSQL)."""

    job_id: str
    bot: str
    operation: str
    status: str
    created_at: datetime
    result: Optional[str] = None
    payload: dict = field(default_factory=dict)
    context: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)
    artifacts: list[dict] = field(default_factory=list)
    error_code: Optional[str] = None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _scalar(value: Any) -> Optional[str]:
    """Texto escalar acotado; dicts, listas y binarios nunca pasan."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (str, int)):
        text = str(value).strip()
        return text[:_MAX_TEXT] if text else None
    return None


def _first(sources: tuple[dict, ...], keys: tuple[str, ...]) -> Optional[str]:
    for source in sources:
        for key in keys:
            text = _scalar(source.get(key))
            if text:
                return text
    return None


def _digits(value: Optional[str]) -> str:
    return re.sub(r"\D", "", value or "")


def _text_list(value: Any) -> Optional[list[str]]:
    if not isinstance(value, (list, tuple)):
        return None
    items = [t for t in (_scalar(v) for v in value[:200]) if t]
    return items or None


def _row_value(row: _Row, name: str, spec: LogSpec) -> Any:
    sources = (row.data, row.context, row.payload)
    if name == "cuit_representante":
        return _first(sources, _REPRESENTANTE_KEYS)
    if name in ("cuit_representado", "cuit"):
        return _first((row.payload, row.context), _REPRESENTADO_KEYS)
    if name == "usuario":
        return _first(sources, ("usuario", *_REPRESENTANTE_KEYS))
    if name == "denominacion":
        return _first((row.payload,), ("representado_nombre", "denominacion"))
    if name == "representado_nombre":
        return _first((row.payload,), ("representado_nombre", "denominacion"))
    if name == "periodo":
        return _first((row.payload,), ("periodo",))
    if name == "periodo_desde":
        return _first((row.payload,),
                      ("periodo_desde", "fecha_desde", "desde"))
    if name == "periodo_hasta":
        return _first((row.payload,),
                      ("periodo_hasta", "fecha_hasta", "hasta"))
    if name == "medio_pago":
        return (_first((row.payload,), ("medio_pago",))
                or spec.medio_pago_default)
    if name == "cuits_consultados":
        listed = _text_list(row.payload.get("cuits_consulta")
                            or row.payload.get("cuits"))
        return ",".join(_digits(c) or c for c in listed) if listed else None
    if name == "cantidad_archivos":
        value = row.data.get("cantidad_archivos")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        return len(row.artifacts) if row.artifacts else None
    if name in ("opciones_encontradas", "despachos", "periodos_descargados",
                "periodos_error"):
        return _text_list(row.data.get(name))
    if name == "archivo_nombre":
        value = _first((row.data,), ("archivo_nombre", "nombre_archivo", "archivo"))
        if value:
            return value
        for artifact in row.artifacts:
            filename = _scalar(artifact.get("name") or artifact.get("filename"))
            if filename:
                return filename
        return None
    return None


def _error_message(row: _Row) -> Optional[str]:
    """Mensaje público de clase; nunca texto crudo del worker."""
    if row.status == "CANCELADO":
        return "Job cancelado."
    if row.status != "FALLIDO":
        return None
    code = (row.error_code or "").lower()
    return CLASS_MESSAGES.get(code, CLASS_MESSAGES["unexpected"])


def project_log(row: _Row, spec: LogSpec) -> dict[str, Any]:
    """Proyecta un job a un registro ``/logs`` usando solo la whitelist."""
    record: dict[str, Any] = {
        "id": row.job_id,
        "job_id": row.job_id,
        "timestamp": row.created_at,
        "status": _STATUS_V1.get(row.status, "pending"),
        "estado_v3": row.status,
        "bot": row.bot,
        "operation": row.operation,
        "resultado": row.result if row.result in ("OK", "PARCIAL", "ERROR")
        else None,
        "error_code": row.error_code,
        "error_message": _error_message(row),
    }
    for name in spec.fields:
        record[name] = _row_value(row, name, spec)
    return record


def _safe_code(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and _CODE_RE.match(value) else None


# --------------------------------------------------------------------------
# Filtros V1
# --------------------------------------------------------------------------


class LogFilterError(ValueError):
    """Fecha con formato inválido."""


def _parse_fecha(name: str, value: Optional[str]) -> Optional[datetime]:
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    try:
        if not _FECHA_RE.match(text):
            raise ValueError(text)
        return datetime.strptime(text, "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise LogFilterError(f"{name} debe tener formato YYYYMMDD") from exc


@dataclass(frozen=True)
class LogFilters:
    desde: Optional[datetime] = None
    hasta_exclusivo: Optional[datetime] = None
    representante: str = ""
    representado: str = ""
    cuit: str = ""
    medio_pago: str = ""


def build_filters(spec: LogSpec, body: BaseModel) -> LogFilters:
    desde = _parse_fecha("Fecha_desde", getattr(body, "Fecha_desde", None))
    hasta = _parse_fecha("Fecha_hasta", getattr(body, "Fecha_hasta", None))

    def pick(name: str) -> str:
        if name not in spec.filters:
            return ""
        return (getattr(body, name, None) or "").strip()

    return LogFilters(
        desde=desde,
        hasta_exclusivo=hasta + timedelta(days=1) if hasta else None,
        representante=_digits(pick("Cuit_representante")),
        representado=_digits(pick("Cuit_representado")),
        cuit=_digits(pick("Cuit")),
        medio_pago=pick("Medio_pago"),
    )


def _matches(row: _Row, spec: LogSpec, filters: LogFilters) -> bool:
    created = _aware(row.created_at)
    if filters.desde and created < filters.desde:
        return False
    if filters.hasta_exclusivo and created >= filters.hasta_exclusivo:
        return False
    if filters.representante and _digits(
        _row_value(row, "cuit_representante", spec)
    ) != filters.representante:
        return False
    if filters.cuit and _digits(
        _row_value(row, "cuit", spec)
    ) != filters.cuit:
        return False
    if filters.representado:
        if "cuits_consultados" in spec.fields:
            # SRT: LIKE sobre el CSV de CUITs consultados.
            csv = _row_value(row, "cuits_consultados", spec) or ""
            if filters.representado not in csv:
                return False
        elif _digits(
            _row_value(row, "cuit_representado", spec)
        ) != filters.representado:
            return False
    if filters.medio_pago and (
        _row_value(row, "medio_pago", spec) != filters.medio_pago
    ):
        return False
    return True


def _targets(spec: LogSpec, bot: str, operation: str) -> bool:
    return any(
        bot == target_bot and (ops is None or operation in ops)
        for target_bot, ops in spec.targets
    )


# --------------------------------------------------------------------------
# Fuentes de datos
# --------------------------------------------------------------------------


def _rows_from_memory(
    principal: ApiPrincipal, spec: LogSpec, filters: LogFilters
) -> list[_Row]:
    rows: list[_Row] = []
    for job in list(JOBS.values()):
        if not _targets(spec, job.bot, job.operation):
            continue
        meta = JOB_META.get(job.id) or {}
        if str(meta.get("owner", "")) != str(principal.user_id):
            continue
        result = job.result if isinstance(job.result, dict) else {}
        error = meta.get("error") or result.get("error")
        context = (job.credential_metadata or {}).get("context")
        row = _Row(
            job_id=str(job.id),
            bot=job.bot,
            operation=job.operation,
            status=str(job.status),
            created_at=job.created_at,
            result=_safe_result(result.get("result")),
            payload=dict(job.payload or {}),
            context=dict(context) if isinstance(context, dict) else {},
            data=dict(result.get("data") or {}),
            artifacts=[item for item in result.get("artifacts", []) if isinstance(item, dict)],
            error_code=_safe_code(
                error.get("error_code") if isinstance(error, dict) else None
            ),
        )
        if _matches(row, spec, filters):
            rows.append(row)
    return rows


def _safe_result(value: Any) -> Optional[str]:
    return value if value in ("OK", "PARCIAL", "ERROR") else None


async def _rows_from_db(
    principal: ApiPrincipal, spec: LogSpec, filters: LogFilters
) -> list[_Row]:
    """Lee ``jobs`` del usuario sin columnas secretas.

    Se seleccionan columnas explícitas: ``credential_ciphertext`` y
    ``error_message`` crudo nunca salen de la base.
    """
    import uuid

    from sqlalchemy import and_, or_, select

    from central_api.models.execution import Job, JobArtifact, JobResult

    try:
        user_id = uuid.UUID(str(principal.user_id))
    except (ValueError, AttributeError, TypeError):
        return []
    statement = _db_statement(spec, filters, user_id, Job, and_, or_, select)
    async with nueva_sesion() as session:
        fetched = (await session.execute(statement)).all()
        ids = [item.id for item in fetched]
        result_by_job: dict[object, dict] = {}
        artifacts_by_job: dict[object, list[dict]] = {}
        if ids:
            result_rows = (await session.execute(
                select(JobResult.job_id, JobResult.payload).where(JobResult.job_id.in_(ids))
            )).all()
            result_by_job = {
                item.job_id: dict(item.payload) if isinstance(item.payload, dict) else {}
                for item in result_rows
                if hasattr(item, "payload")
            }
            artifact_rows = (await session.execute(
                select(JobArtifact.job_id, JobArtifact.filename, JobArtifact.object_key)
                .where(JobArtifact.job_id.in_(ids))
                .order_by(JobArtifact.created_at)
            )).all()
            for artifact in artifact_rows:
                if not hasattr(artifact, "filename"):
                    continue
                artifacts_by_job.setdefault(artifact.job_id, []).append({
                    "filename": artifact.filename,
                    "object_key": artifact.object_key,
                })
    rows: list[_Row] = []
    for item in fetched:
        payload = item.request_payload if isinstance(
            item.request_payload, dict) else {}
        metadata = item.credential_metadata if isinstance(
            item.credential_metadata, dict) else {}
        context = metadata.get("context")
        row = _Row(
            job_id=str(item.id),
            bot=item.bot,
            operation=item.operation,
            status=str(item.status),
            created_at=item.created_at,
            result=_safe_result(item.result),
            payload=dict(payload),
            context=dict(context) if isinstance(context, dict) else {},
            data=result_by_job.get(item.id, {}),
            artifacts=artifacts_by_job.get(item.id, []),
            error_code=_safe_code(item.error_code),
        )
        if _matches(row, spec, filters):
            rows.append(row)
    return rows


def _db_statement(spec, filters, user_id, Job, and_, or_, select):  # noqa: N803
    target_clause = or_(*[
        and_(Job.bot == bot, Job.operation.in_(ops)) if ops is not None
        else Job.bot == bot
        for bot, ops in spec.targets
    ])
    statement = (
        select(
            Job.id, Job.bot, Job.operation, Job.status, Job.result,
            Job.request_payload, Job.credential_metadata, Job.created_at,
            Job.error_code,
        )
        .where(Job.user_id == user_id, target_clause)
    )
    if filters.desde:
        statement = statement.where(Job.created_at >= filters.desde)
    if filters.hasta_exclusivo:
        statement = statement.where(Job.created_at < filters.hasta_exclusivo)
    # Los CUIT/medio de pago se filtran en Python sobre la proyección segura,
    # así que con esos filtros el tope SQL es mayor para no perder coincidencias.
    needs_python_filter = any(
        (filters.representante, filters.representado, filters.cuit,
         filters.medio_pago)
    )
    cap = _MAX_SCAN_ROWS if needs_python_filter else _MAX_ROWS
    return statement.order_by(Job.created_at.desc(), Job.id.desc()).limit(cap)


# --------------------------------------------------------------------------
# Rutas
# --------------------------------------------------------------------------


def _validation_error(message: str) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"detail": {"error_code": "validation", "message": message,
                            "success": False}},
    )


def _register(spec: LogSpec) -> None:
    request_model = _request_model(spec)
    record_model = _record_model(spec)
    response_model = _response_model(spec, record_model)
    primary_bot = spec.targets[0][0]

    async def handler(
        principal: ApiPrincipal = Depends(require_api_principal),
        body: request_model = Body(default_factory=request_model),  # type: ignore[valid-type]
        limit: int = Query(
            default=_MAX_ROWS, ge=1, le=_MAX_ROWS,
            description="Máximo de registros devueltos (más recientes primero).",
        ),
    ):
        if not (principal_has(principal, "jobs:read_own")
                or principal_has(principal, "jobs:read_any")):
            return JSONResponse(
                status_code=403, content=public_error("forbidden"))
        try:
            filters = build_filters(spec, body)
        except LogFilterError as exc:
            return _validation_error(str(exc))
        if db_configurado():
            try:
                rows = await _rows_from_db(principal, spec, filters)
            except Exception:  # noqa: BLE001 - no degradar a memoria con DB
                return JSONResponse(
                    status_code=503,
                    content=public_error("service_not_enabled"),
                    headers={"Retry-After": "3"},
                )
        else:
            rows = _rows_from_memory(principal, spec, filters)
        rows.sort(key=lambda r: (_aware(r.created_at), r.job_id), reverse=True)
        return {"registros": [project_log(r, spec) for r in rows[:limit]]}

    handler.__name__ = f"logs_{_slug(spec.path)}"
    router.add_api_route(
        spec.path,
        handler,
        methods=["POST"],
        response_model=response_model,
        tags=[primary_bot],
        summary=f"Historial de ejecuciones de {spec.path.rsplit('/', 1)[0]}",
        response_description=(
            "Registros del historial del usuario, más recientes primero."
        ),
    )


for _spec in LOG_SPECS:
    _register(_spec)

__all__ = ["LOG_SPECS", "LogSpec", "project_log", "router"]
