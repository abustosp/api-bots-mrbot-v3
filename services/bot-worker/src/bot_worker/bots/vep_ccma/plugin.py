"""Plugin ``vep_ccma``: genera VEP desde CCMA con volante y QR.

Porta ``api-bots-mrbot-v2/app/bot/vep_ccma_bot.py`` (``bot_vep_ccma``)
al contrato S7. Cambios obligatorios respecto de V2:

- Sin ``SessionLocal`` ni escritura en base: la central persiste el
  resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni temporales sueltos: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya
  construido viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin b64 de PDF/QR en el resultado: viajan como artefactos con
  sha256; el JSON lleva totales, seleccion y metadatos.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

Generar un VEP es efecto fiscal: el manifiesto declara
``idempotency_class="EFECTO"``.

La sesion de navegador se obtiene de ``runtime.browser_factory``
con la interfaz minima documentada abajo; el plugin nunca importa
Playwright directo. El objeto ``servicio`` expone:

- ``seleccionar_representado(cuit)``: elige el representado.
- ``listar_obligaciones()``: retorna
  ``{"impuestos": [...], "intereses": [...]}`` scrapeados.
- ``generar_volante(seleccion, medio_pago)``: genera el VEP y
  retorna ``{"volante": [...], "total": int}``.
- ``descargar_detalle(destino)``: guarda el PDF del volante.
- ``descargar_qr(destino)``: guarda el PNG del QR (o ``None``).
"""

from __future__ import annotations

import asyncio
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
from bot_worker.bots.vep_ccma.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "CCMA"
ID_ARTEFACTO_DETALLE = "vep_ccma_detalle_pdf"
ID_ARTEFACTO_QR = "vep_ccma_qr_png"


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


class VepCcmaPlugin:
    """CCMA de ARCA: volante VEP de monotributo/autonomos con QR."""

    manifest = BotManifest(
        nombre="vep_ccma",
        version="3.0.0",
        operaciones=("generar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="vep_ccma_detalle.pdf",
                content_types=("application/pdf",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="vep_ccma_qr.png",
                content_types=("image/png",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1800,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=3,
        idempotency_class="EFECTO",
        browser_instances_max=1,
        hosts_permitidos=("www.afip.gob.ar",),
    )

    def __init__(self, servicio_nombre: str | None = None) -> None:
        """Inyecta el nombre del servicio ARCA desde el sobre sellado."""
        self._servicio_nombre = servicio_nombre or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "VepCcmaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "VepCcmaPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Sin seccion rige CCMA.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("ccma_servicio"):
            self._servicio_nombre = str(service["ccma_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, medio de pago y flags antes de navegar."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "generar"))
        modelo = ENTRADAS.get(operacion)
        if modelo is None:
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return (operacion, modelo.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Ejecuta la generacion y retorna ``BotResult`` tipado."""
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
                datos, artefactos = await self._generar(
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

    async def _generar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Lista obligaciones, genera el volante y sube PDF+QR."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=30, message="Listando obligaciones CCMA"
        )
        obligaciones = await servicio.listar_obligaciones()
        impuestos = list((obligaciones or {}).get("impuestos", []))
        intereses = list((obligaciones or {}).get("intereses", []))
        seleccion = {
            "impuestos": impuestos if entrada.seleccionar_impuestos else [],
            "intereses": intereses if entrada.seleccionar_intereses else [],
        }
        datos: dict[str, Any] = {
            "operacion": "generar",
            "representado_cuit": entrada.representado_cuit,
            "medio_pago": entrada.medio_pago,
            "total_impuestos": len(impuestos),
            "total_intereses": len(intereses),
            "total_seleccionado": len(seleccion["impuestos"])
            + len(seleccion["intereses"]),
        }
        artefactos: list[dict[str, Any]] = []
        if entrada.generar_volante:
            await runtime.event_sink.progress(
                phase="GENERANDO", percent=55, message="Generando volante VEP"
            )
            volante = await servicio.generar_volante(
                seleccion=seleccion, medio_pago=entrada.medio_pago
            )
            datos["volante"] = list((volante or {}).get("volante", []))
            detalle = runtime.artifact_store.resolve(
                f"{entrada.representado_cuit} - VEP CCMA.pdf"
            )
            await servicio.descargar_detalle(destino=detalle)
            if getattr(entrada, "subir_pdf", True):
                await runtime.event_sink.progress(
                    phase="SUBIENDO", percent=80, message="Subiendo volante"
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        ID_ARTEFACTO_DETALLE, detalle.name
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                datos["sha256"] = referencia["sha256"]
                datos["size_bytes"] = referencia["size_bytes"]
            qr = runtime.artifact_store.resolve(
                f"{entrada.representado_cuit} - VEP CCMA - QR.png"
            )
            qr_generado = await servicio.descargar_qr(destino=qr)
            if qr_generado and getattr(entrada, "subir_pdf", True):
                try:
                    referencia_qr = await runtime.artifact_store.upload(
                        ID_ARTEFACTO_QR, qr.name
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia_qr)
                datos["sha256_qr"] = referencia_qr["sha256"]
        if getattr(entrada, "incluir_json", True):
            datos["impuestos"] = impuestos
            datos["intereses"] = intereses
        return datos, artefactos
