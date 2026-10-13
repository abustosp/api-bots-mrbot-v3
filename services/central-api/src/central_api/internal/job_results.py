"""Reporte de resultado terminal idempotente (plan 02 §7.8).

``POST /internal/v1/jobs/{job_id}/result`` valida el sobre, persiste
resultado y artefactos en una unidad lógica, confirma o libera la reserva de
billing según la matriz de cobro y marca el job terminal. Idempotente por
``(job_id, assignment_attempt)``: el replay idéntico responde 200 con lo
persistido; un segundo resultado distinto es conflicto auditado.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from central_api.db import db_configurado, nueva_sesion
from central_api.internal.dependencies import require_worker
from central_api.internal.job_access import job_para_worker
from central_api.scheduler import reaper as reaper_mod
from central_api.store import utcnow

router = APIRouter()

TERMINAL_OK = ("OK", "PARCIAL")


class ArtifactBody(BaseModel):
    """Descriptor de salida, nunca acepta URLs firmadas ni campos extra."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str | None = Field(
        default=None, max_length=128,
        description="Identificador del artefacto en la salida de la ejecución.",
        examples=["constancia-01"],
    )
    object_key: str = Field(
        min_length=1, max_length=1024,
        description="Clave del archivo guardado que fue devuelta al solicitar la subida.",
        examples=["jobs/0190c2d4/1/constancia-01/ab12cd34ef56"],
    )
    name: str = Field(
        min_length=1, max_length=255,
        description="Nombre del archivo entregado al usuario.", examples=["constancia.pdf"]
    )
    size_bytes: int = Field(
        ge=0, description="Tamaño del archivo en bytes.", examples=[184320]
    )
    content_type: str = Field(
        min_length=1, max_length=128,
        description="Tipo de contenido del archivo.", examples=["application/pdf"]
    )
    sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-fA-F]{64}$",
        description="Hash SHA-256 hexadecimal del archivo, si está disponible.",
        examples=["0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"],
    )


class ResultBody(BaseModel):
    result: str = Field(
        default="OK", pattern=r"^(OK|PARCIAL|ERROR|CANCELADO)$",
        description="Resultado terminal de la ejecución.", examples=["OK"],
    )
    data: dict = Field(
        default_factory=dict,
        description="Datos devueltos por la operación cuando el resultado es correcto o parcial.",
        examples=[{"estado": "presentada", "periodo": "2026-09"}],
    )
    error: dict | None = Field(
        default=None, description="Información resumida del error, si la ejecución falló.",
        examples=[{"error_code": "consulta_no_disponible"}],
    )
    artifacts: list[ArtifactBody] = Field(
        default_factory=list, max_length=500,
        description="Archivos generados y guardados para esta ejecución.",
        examples=[[{
            "artifact_id": "constancia-01",
            "object_key": "jobs/0190c2d4/1/constancia-01/ab12cd34ef56",
            "name": "constancia.pdf", "size_bytes": 184320,
            "content_type": "application/pdf",
        }]],
    )
    assignment_attempt: int = Field(
        default=1, ge=1, description="Número del intento que produjo el resultado.", examples=[1]
    )
    event_id: str = Field(
        default="", max_length=128,
        description="Identificador de correlación del reporte de resultado.",
        examples=["evt-0190c2d4-result"],
    )
    error_category: str | None = Field(
        default=None, max_length=80,
        description="Categoría pública del error; use INFRASTRUCTURE_ERROR solo para fallos de infraestructura.",
        examples=["INFRASTRUCTURE_ERROR"],
    )


async def _espejar_resultado_db(job_id: str, body: ResultBody) -> tuple[str, bool]:
    """Guarda estado, response y artifacts en una transacción canónica.

    La actualización de ``jobs`` y los INSERT de ``job_results`` y
    ``job_artifacts`` disparan la proyección física por bot dentro de la misma
    transacción. Cualquier error se propaga para que el worker pueda reintentar.
    """
    import uuid

    from central_api.repositories.jobs import JobRepository

    jid = uuid.UUID(str(job_id))
    artifacts = [
        item.model_dump(exclude_none=True)
        for item in body.artifacts
    ]
    summary = {
        "error": dict(body.error or {}),
        "event_id": body.event_id,
        "error_category": body.error_category,
    }
    async with nueva_sesion() as sesion:
        repo = JobRepository(sesion)  # type: ignore[arg-type]
        job, created = await repo.persist_result(
            jid,
            attempt=body.assignment_attempt,
            result=body.result,
            payload=dict(body.data or {}),
            summary=summary,
            artifacts=artifacts,
        )
        if created:
            from central_api.billing.postgres import settle_in_session

            if body.result in TERMINAL_OK or (body.error_category or "") != "INFRASTRUCTURE_ERROR":
                await settle_in_session(sesion, job.id, status="CONFIRMADO")
            else:
                await settle_in_session(
                    sesion,
                    job.id,
                    status="REEMBOLSADO",
                    reason="INFRASTRUCTURE_ERROR",
                )
        return str(job.status), not created


@router.post(
    "/jobs/{job_id}/result",
    summary="Informar el resultado final de un job",
    description=(
        "Entrega el resultado terminal, los datos de salida y la metadata de los "
        "archivos generados por el intento asignado al worker autenticado. Un "
        "reenvío idéntico es idempotente; un resultado diferente para el mismo "
        "job e intento se rechaza para evitar reemplazar la salida ya confirmada."
    ),
    responses={
        200: {
            "description": "Resultado persistido o repetición idéntica del reporte.",
            "content": {"application/json": {"examples": {
                "registrado": {"summary": "Resultado guardado", "value": {
                    "success": True, "job_id": "0190c2d4-7b4a-7b5f-9f28-3efc1f7b1b10", "status": "COMPLETO"
                }},
                "repetido": {"summary": "Reenvío idempotente", "value": {
                    "success": True, "job_id": "0190c2d4-7b4a-7b5f-9f28-3efc1f7b1b10", "status": "COMPLETO", "dedup": True
                }},
            }}},
        },
        401: {"description": "Falta la identidad de un worker registrado activo. Incluya `X-Worker-Node` con el nodo del worker registrado.", "content": {"application/json": {"example": {"detail": {"error_code": "authentication", "message": "Credenciales inválidas."}}}}},
        403: {"description": "El token de servicio no es válido o el job pertenece a otra asignación. Renueve el registro/token si corresponde y reporte solo trabajos asignados a este worker.", "content": {"application/json": {"examples": {
            "token": {"summary": "Token no autorizado", "value": {"detail": {"error_code": "forbidden", "message": "Operación no permitida."}}},
            "asignacion": {"summary": "Job de otro worker", "value": {"detail": "asignación de otro worker"}},
        }}}},
        404: {"description": "El job ya no existe o el identificador no es válido para una ejecución registrada. Compruebe el identificador entregado por la central.", "content": {"application/json": {"example": {"detail": "job no encontrado"}}}},
        409: {"description": "Ya existe un resultado distinto para el mismo intento o el intento informado quedó obsoleto. No cambie el contenido al reintentar; use la asignación vigente si el intento fue reemplazado.", "content": {"application/json": {"examples": {
            "duplicado": {"summary": "Resultado distinto ya registrado", "value": {"detail": "resultado duplicado divergente"}},
            "asignacion": {"summary": "Intento obsoleto", "value": {"detail": "stale_assignment"}},
        }}}},
        422: {"description": "El cuerpo, el resultado o la metadata de un archivo no cumple el contrato. Revise tipos, límites, clave del archivo y formato del hash SHA-256 antes de reenviar.", "content": {"application/json": {"examples": {
            "validacion": {"summary": "Campo de entrada inválido", "value": {"detail": [{"loc": ["body", "assignment_attempt"], "msg": "Input should be greater than or equal to 1", "type": "greater_than_equal"}]}},
            "artefacto": {"summary": "Metadata de archivo inválida", "value": {"detail": "metadata de artefacto inválida"}},
            "resultado": {"summary": "Resultado no aceptado", "value": {"detail": "resultado inválido"}},
        }}}},
        503: {"description": "No se pudo verificar la asignación del job o guardar el resultado por una indisponibilidad temporal. Revise que el acceso a datos responda y reintente el mismo reporte después de la espera indicada en `Retry-After`.", "headers": {"Retry-After": {"description": "Segundos sugeridos antes de reintentar.", "schema": {"type": "integer", "example": 3}}}, "content": {"application/json": {"examples": {
            "persistencia": {"summary": "No se pudo confirmar el resultado", "value": {"detail": "no se pudo confirmar el resultado; reintente"}},
            "asignacion": {"summary": "No se pudo verificar la asignación", "value": {"detail": "no se pudo verificar la asignación del job"}},
        }}}},
    },
)
async def report_result(
    job_id: str, body: ResultBody, worker_node: str = Depends(require_worker)
) -> dict:
    """Guarda el resultado terminal de un job asignado a este worker."""
    job = await job_para_worker(job_id, worker_node)
    if job is None:
        raise HTTPException(status_code=404, detail="job no encontrado")
    artifacts = [item.model_dump(exclude_none=True) for item in body.artifacts]
    callback_result = {
        "result": body.result,
        "data": body.data,
        "error": body.error,
        "attempt": body.assignment_attempt,
        "event_id": body.event_id,
        "error_category": body.error_category,
        "artifacts": artifacts,
    }
    dedup = False
    if db_configurado():
        from central_api.repositories.base import NotFoundError, RepositoryError
        from central_api.repositories.jobs import (
            IdempotencyConflict,
            InvalidArtifactError,
            PersistenceError,
            StaleAssignmentError,
        )

        try:
            persisted_status, dedup = await _espejar_resultado_db(job.id, body)
        except IdempotencyConflict as exc:
            raise HTTPException(status_code=409, detail="resultado duplicado divergente") from exc
        except NotFoundError as exc:
            raise HTTPException(status_code=404, detail="job no encontrado") from exc
        except StaleAssignmentError as exc:
            raise HTTPException(status_code=409, detail="stale_assignment") from exc
        except InvalidArtifactError as exc:
            raise HTTPException(status_code=422, detail="metadata de artefacto inválida") from exc
        except PersistenceError as exc:
            raise HTTPException(
                status_code=503,
                detail="no se pudo confirmar el resultado; reintente",
                headers={"Retry-After": "3"},
            ) from exc
        except RepositoryError as exc:
            raise HTTPException(status_code=422, detail="resultado inválido") from exc
        except Exception as exc:  # noqa: BLE001 - fallo de DB debe permitir retry
            raise HTTPException(
                status_code=503,
                detail="no se pudo confirmar el resultado; reintente",
                headers={"Retry-After": "3"},
            ) from exc
        job.status = persisted_status
    elif job.result is not None:
        if job.result == callback_result:
            return {
                "success": True, "job_id": job.id,
                "status": job.status, "dedup": True,
            }
        raise HTTPException(status_code=409, detail="resultado duplicado divergente")
    from central_api.api.dependencies import JOB_META
    from central_api.billing.reservation import confirm_usage, refund_usage

    job.result = callback_result
    terminal_ok = body.result in TERMINAL_OK
    if not db_configurado():
        job.status = (
            "COMPLETO" if terminal_ok
            else "CANCELADO" if body.result == "CANCELADO"
            else "FALLIDO"
        )
    now = utcnow().isoformat().replace("+00:00", "Z")
    meta = JOB_META.get(job.id)
    if meta is not None:
        meta["finished_at"] = now
        if terminal_ok:
            meta["data"] = {"schema_version": 1, **(body.data or {})}
        else:
            meta["error"] = body.error or {"error_code": "unexpected"}
    if not db_configurado() and (terminal_ok or (body.error_category or "") != "INFRASTRUCTURE_ERROR"):
        # COMPLETO o error de negocio: cobro confirmado por defecto.
        confirm_usage(job.id)
    elif not db_configurado():
        # Fallo de infraestructura: reembolso propuesto (libera la reserva).
        refund_usage(job.id, motivo="INFRASTRUCTURE_ERROR")
    reaper_mod.clear_lease(job.id)
    if not dedup:
        entry = reaper_mod.WORKER_LOAD.get(worker_node)
        if entry is not None and entry > 0:
            reaper_mod.WORKER_LOAD[worker_node] = entry - 1
    response = {"success": True, "job_id": job.id, "status": job.status}
    if dedup:
        response["dedup"] = True
    return response
