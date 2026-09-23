"""Plugin ``sct``: sistema de cuentas de ARCA (vencimientos/deudas/DDJJ).

Porta ``api-bots-mrbot-v2/app/bot/sct_bot.py`` (``bot_sct``) al
contrato S7. Cambios obligatorios respecto de V2:

- Sin ``SessionLocal`` ni escritura en base: la central persiste el
  resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni temporales sueltos: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya
  construido viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin b64 de reportes en el resultado: los archivos viajan como
  artefactos con sha256; el JSON lleva solo resumen e indices.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory``
con la interfaz minima documentada abajo; el plugin nunca importa
Playwright directo. El objeto ``servicio`` expone:

- ``seleccionar_representado(cuit)``: elige el representado.
- ``descargar_reporte(seccion, formato, destino)``: guarda el
  reporte (``vencimientos`` | ``deudas`` | ``ddjj_pendientes`` en
  ``xlsx`` | ``csv`` | ``pdf``) en ``destino``.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Mapping

from bot_worker.bots.errors import (
    ArtifactUploadError,
    BrowserCrashedError,
    CredentialsRejectedError,
    DeadlineExceededError,
    ErrorDeBot,
    InvalidInputError,
    TargetUnavailableError,
    sin_secretos,
)
from bot_worker.bots.sct.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "SISTEMA DE CUENTAS"
ID_ARTEFACTO_BASE = "sct_reporte"
EXTENSIONES = {"xlsx": "xlsx", "csv": "csv", "pdf": "pdf"}


def nombre_reporte_sct(cuit: str, seccion: str, formato: str) -> str:
    """Replica el patron de nombres V2 ``'{fin} - SCT - ...'``."""
    digitos = re.sub(r"\D", "", cuit or "")
    fin = digitos[-1:] if digitos else "X"
    return f"{fin} - SCT - {seccion} - {digitos}.{EXTENSIONES[formato]}"


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnostico seguro."""
    if isinstance(exc, ErrorDeBot):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return TargetUnavailableError("timeout del sitio del organismo")
    texto = sin_secretos(f"{type(exc).__name__}", secretos)
    nombre = type(exc).__name__.lower()
    if "browser" in nombre or "playwright" in nombre:
        return BrowserCrashedError(f"navegador caido: {texto}")
    return TargetUnavailableError(f"falla del organismo: {texto}")


class SctPlugin:
    """Sistema de Cuentas ARCA: vencimientos, deudas y DDJJ pendientes."""

    manifest = BotManifest(
        nombre="sct",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="sct_reporte",
                content_types=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "text/csv",
                    "application/pdf",
                ),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1800,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=2,
        idempotency_class="CONTINUACION",
        browser_instances_max=1,
        hosts_permitidos=("www.afip.gob.ar",),
    )

    def __init__(self, servicio_nombre: str | None = None) -> None:
        """Inyecta el nombre del servicio ARCA desde el sobre sellado."""
        self._servicio_nombre = servicio_nombre or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "SctPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "SctPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Sin seccion rige Sistema de Cuentas.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("sct_servicio"):
            self._servicio_nombre = str(service["sct_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, secciones y formatos antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "consultar"))
        modelo = ENTRADAS.get(operacion)
        if modelo is None:
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return (operacion, modelo.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Ejecuta la consulta y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotResult

        operacion, entrada = payload
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
                servicio = await sesion.open_service(self._servicio_nombre)
                await servicio.seleccionar_representado(entrada.representado_cuit)
                datos, artefactos = await self._descargar(
                    servicio, entrada, runtime
                )
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return BotResult(result="OK", data=datos, artifacts=artefactos)

    async def _descargar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Descarga seccion x formato a ``work_dir`` y sube por slot."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Descargando reportes SCT"
        )
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": entrada.representado_cuit,
            "reportes": [],
        }
        artefactos: list[dict[str, Any]] = []
        for seccion in entrada.secciones:
            for formato in entrada.formatos:
                await runtime.cancellation.raise_if_cancelled()
                nombre = nombre_reporte_sct(
                    entrada.representado_cuit, seccion, formato
                )
                destino = runtime.artifact_store.resolve(nombre)
                await servicio.descargar_reporte(
                    seccion=seccion, formato=formato, destino=destino
                )
                resumen: dict[str, Any] = {
                    "seccion": seccion,
                    "formato": formato,
                    "archivo": destino.name,
                }
                if getattr(entrada, "subir", True):
                    await runtime.event_sink.progress(
                        phase="SUBIENDO", percent=80,
                        message=f"Subiendo {seccion}.{formato}",
                    )
                    try:
                        referencia = await runtime.artifact_store.upload(
                            f"{ID_ARTEFACTO_BASE}_{seccion}_{formato}", destino.name
                        )
                    except ValueError as exc:
                        raise ArtifactUploadError(str(exc)) from exc
                    artefactos.append(referencia)
                    resumen["sha256"] = referencia["sha256"]
                    resumen["size_bytes"] = referencia["size_bytes"]
                if getattr(entrada, "incluir_json", True):
                    datos["reportes"].append(resumen)
        if not getattr(entrada, "incluir_json", True):
            datos.pop("reportes", None)
        return datos, artefactos
