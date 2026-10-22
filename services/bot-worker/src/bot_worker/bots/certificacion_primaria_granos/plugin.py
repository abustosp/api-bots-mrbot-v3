"""Plugin ``certificacion_primaria_granos``.

Descarga los certificados electrónicos de depósito de granos que el
representado tiene como depositante (servicio ARCA "Liquidación Primaria de
Granos" > Certificación Electrónica de Granos > Consulta de Certificados de
Depósito). Resultado: planilla XLSX con la grilla y un PDF por certificado.
Mismas reglas del contrato S7 que ``liquidacion_granos``: sin DB, archivos
bajo ``runtime.work_dir`` y subida con slots prefirmados.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from bot_worker.bots.certificacion_primaria_granos.schema import (
    ENTRADAS,
    esquema_entrada,
)
from bot_worker.bots.errors import (
    ArtifactUploadError,
    CredentialsRejectedError,
    DeadlineExceededError,
    ErrorDeBot,
    InvalidInputError,
)
from bot_worker.bots.liquidacion_granos.plugin import _normalizar_error
from bot_worker.bots.registry import ArtifactSpec, BotManifest
from bot_worker.runtime.context import ruta_para_upload

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "LIQUIDACIÓN PRIMARIA DE GRANOS"
ID_ARTEFACTO_PLANILLA = "planilla_xlsx"
ID_ARTEFACTO_CERTIFICADO = "certificado_pdf"


def nombre_planilla(representado_cuit: str, desde: str, hasta: str) -> str:
    digitos = re.sub(r"\D", "", representado_cuit or "")
    return f"CPG - {digitos} - {desde.replace('/', '')} - {hasta.replace('/', '')}.xlsx"


class CertificacionPrimariaGranosPlugin:
    """Certificados de depósito (como depositante) de ARCA."""

    manifest = BotManifest(
        nombre="certificacion_primaria_granos",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="planilla.xlsx",
                content_types=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet",
                ),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="certificado.pdf",
                content_types=("application/pdf",),
                max_bytes=10_485_760,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=900,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=True,
        costo_creditos_sugerido=2,
        idempotency_class="CONTINUACION",
        browser_instances_max=1,
        hosts_permitidos=("www.afip.gob.ar", "api.capmonster.cloud"),
    )

    def __init__(self, defectos: dict[str, Any] | None = None) -> None:
        self._defectos: dict[str, Any] = dict(defectos or {})

    def __repr__(self) -> str:
        return "CertificacionPrimariaGranosPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "CertificacionPrimariaGranosPlugin":
        """Aplica la sección ``service`` del sobre sellado sobre una copia."""
        service = service if isinstance(service, dict) else {}
        if "subir_archivos" in service:
            self._defectos["subir_archivos"] = service["subir_archivos"]
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "consultar"))
        modelo = ENTRADAS.get(operacion)
        if modelo is None:
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            for clave, valor in self._defectos.items():
                datos.setdefault(clave, valor)
            return (operacion, modelo.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        from bot_worker.runtime.context import BotResult

        _operacion, entrada = payload
        if runtime.credentials is None:
            raise CredentialsRejectedError("el plugin requiere credenciales fiscales")
        secretos = [runtime.credentials.clave]
        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="LOGIN", percent=10, message="Iniciando sesion fiscal"
        )
        if runtime.deadline.remaining_seconds() <= 0:
            raise DeadlineExceededError("deadline agotado antes de navegar")
        try:
            async with runtime.browser_factory.arca_session(
                credentials=runtime.credentials,
                proxy=runtime.proxy,
                deadline=runtime.deadline,
                cancellation=runtime.cancellation,
            ) as sesion:
                await sesion.login()
                await runtime.cancellation.raise_if_cancelled()
                servicio = await sesion.open_service(
                    SERVICIO_ARCA, portal="certificacion_primaria_granos"
                )
                await servicio.seleccionar_representado(
                    entrada.representado_cuit, entrada.representado_nombre
                )
                datos, artefactos = await self._consultar(servicio, entrada, runtime)
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return BotResult(result="OK", data=datos, artifacts=artefactos)

    async def _consultar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Consultando certificados"
        )
        destino = runtime.artifact_store.resolve(
            nombre_planilla(
                entrada.representado_cuit, entrada.fecha_desde, entrada.fecha_hasta
            )
        )
        planilla, pdfs = await servicio.descargar_certificados_depositante(
            destino_xlsx=destino,
            destino_dir=destino.parent,
            desde=entrada.fecha_desde,
            hasta=entrada.fecha_hasta,
        )
        planilla = Path(planilla)
        rutas = [Path(p) for p in pdfs or []]
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": entrada.representado_cuit,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
            "tipo_consulta": "Certificados como Depositante",
            "cantidad_certificados": len(rutas),
            "planilla": planilla.name,
            "certificados": [p.name for p in rutas],
        }
        artefactos: list[dict[str, Any]] = []
        if entrada.subir_archivos:
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=75, message="Subiendo archivos"
            )
            work_dir = getattr(runtime, "work_dir", None)
            for artifact_id, ruta in (
                (ID_ARTEFACTO_PLANILLA, planilla),
                *((ID_ARTEFACTO_CERTIFICADO, p) for p in rutas),
            ):
                try:
                    artefactos.append(
                        await runtime.artifact_store.upload(
                            artifact_id, ruta_para_upload(work_dir, ruta)
                        )
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
        return datos, artefactos
