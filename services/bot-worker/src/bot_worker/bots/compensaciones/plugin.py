"""Plugin ``compensaciones``: consulta de compensaciones (SCT) con navegador.

Porta ``api-bots-mrbot-v2/app/bot/compensaciones_bot.py``
(``bot_compensaciones``: login fiscal, servicio SISTEMA DE CUENTAS,
seleccion de representado, submodulo Consulta Compensaciones y
Afectaciones, filtros desde/hasta + CONSULTAR, exportacion XLS/CSV/PDF)
al contrato S7. Cambios obligatorios respecto de V2:

- Sin ``SessionLocal`` ni base de datos (W-1).
- Sin ``tempfile`` suelto: las descargas viven bajo
  ``runtime.work_dir`` y se validan con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya construido
  viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory`` con
``arca_session``; el plugin nunca importa Playwright directo ni
desactiva headless/proxy/limpieza.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any, Mapping

from bot_worker.bots.compensaciones.schema import esquema_entrada
from bot_worker.bots.compensaciones.schema import (
    CompensacionesConsultarInput,
)
from bot_worker.bots.errors import (
    ArtifactUploadError,
    BrowserCrashedError,
    CaptchaUnsolvableError,
    CredentialsRejectedError,
    DeadlineExceededError,
    ErrorDeBot,
    InvalidInputError,
    TargetUnavailableError,
    sin_secretos,
)
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "SISTEMA DE CUENTAS"
FORMATOS = (
    ("XLS", "excel", "compensaciones.xls"),
    ("CSV", "csv", "compensaciones.csv"),
    ("PDF", "pdf", "compensaciones.pdf"),
)


def nombre_descarga(representado_cuit: str, formato: str, sugerido: str) -> str:
    """Replica el patron V2 ``'sct_compensaciones_<cuit>_<ts>.<ext>'``."""
    limpio = re.sub(r"[\\/:*?\"<>|]+", "_", sugerido or "")
    limpio = re.sub(r"\s+", "_", limpio).strip("_") or f"compensaciones.{formato.lower()}"
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"sct_compensaciones_{representado_cuit}_{sello}_{limpio}"


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnostico seguro."""
    if isinstance(exc, ErrorDeBot):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return TargetUnavailableError("timeout del sitio del organismo")
    texto = sin_secretos(f"{type(exc).__name__}", secretos)
    if "captcha" in type(exc).__name__.lower():
        return CaptchaUnsolvableError(f"desafio no resoluble: {texto}")
    if "browser" in type(exc).__name__.lower() or "playwright" in type(exc).__name__.lower():
        return BrowserCrashedError(f"navegador caido: {texto}")
    return TargetUnavailableError(f"falla del organismo: {texto}")


class CompensacionesPlugin:
    """Consulta de compensaciones y afectaciones: filtros y exportacion."""

    manifest = BotManifest(
        nombre="compensaciones",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="compensaciones.xls",
                content_types=(
                    "application/vnd.ms-excel",
                    "application/octet-stream",
                ),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="compensaciones.csv",
                content_types=("text/csv",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="compensaciones.pdf",
                content_types=("application/pdf",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=900,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=2,
        idempotency_class="CONTINUACION",
        browser_instances_max=1,
        hosts_permitidos=("www.afip.gob.ar", "ctacte.cloud.afip.gob.ar"),
    )

    def __init__(self, servicio: str | None = None) -> None:
        """Inyecta el nombre del servicio desde el sobre sellado."""
        self._servicio = servicio or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "CompensacionesPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "CompensacionesPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Clave: ``compensaciones_servicio``.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("compensaciones_servicio"):
            self._servicio = str(service["compensaciones_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, rango y formatos antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "consultar"))
        if operacion != "consultar":
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return ("consultar", CompensacionesConsultarInput.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Ejecuta la consulta y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotResult

        _, entrada = payload
        if runtime.credentials is None:
            raise CredentialsRejectedError("el plugin requiere credenciales fiscales")
        secretos = [runtime.credentials.clave]
        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="LOGIN", percent=10, message="Iniciando sesion fiscal"
        )
        if runtime.deadline.remaining_seconds() <= 0:
            raise DeadlineExceededError("deadline agotado antes de navegar")
        pedidos = [
            (fmt, flag, slot)
            for fmt, flag, slot in FORMATOS
            if getattr(entrada, flag)
        ]
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
                    self._servicio, portal="compensaciones"
                )
                cuit_representante = re.sub(
                    r"\D", "", runtime.credentials.cuit_representante
                )
                cuit_representado = re.sub(r"\D", "", entrada.representado_cuit)
                if cuit_representado != cuit_representante:
                    await servicio.seleccionar_representado(
                        entrada.representado_cuit
                    )
                await runtime.event_sink.progress(
                    phase="CONSULTA", percent=45, message="Consultando compensaciones"
                )
                estado = await servicio.consultar(
                    entrada.fecha_desde, entrada.fecha_hasta
                )
                datos: dict[str, Any] = {
                    "operacion": "consultar",
                    "representado_cuit": entrada.representado_cuit,
                    "fecha_desde": entrada.fecha_desde,
                    "fecha_hasta": entrada.fecha_hasta,
                }
                artefactos: list[dict[str, Any]] = []
                if estado == "sin_resultados":
                    datos["mensaje"] = "No se encontraron resultados"
                    return BotResult(result="OK", data=datos)
                for formato, _flag, slot in pedidos:
                    await runtime.cancellation.raise_if_cancelled()
                    await runtime.event_sink.progress(
                        phase="PROCESANDO",
                        percent=65,
                        message=f"Exportando {formato}",
                    )
                    nombre = nombre_descarga(
                        entrada.representado_cuit, formato, slot
                    )
                    destino = runtime.artifact_store.resolve(nombre)
                    await servicio.exportar(formato, destino)
                    resumen: dict[str, Any] = {"archivo": nombre}
                    if entrada.subir:
                        try:
                            referencia = await runtime.artifact_store.upload(
                                slot, nombre
                            )
                        except ValueError as exc:
                            raise ArtifactUploadError(str(exc)) from exc
                        artefactos.append(referencia)
                        resumen["sha256"] = referencia["sha256"]
                        resumen["size_bytes"] = referencia["size_bytes"]
                    datos[formato.lower()] = resumen
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return BotResult(result="OK", data=datos, artifacts=artefactos)
