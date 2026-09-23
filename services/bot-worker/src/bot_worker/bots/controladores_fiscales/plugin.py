"""Plugin ``controladores_fiscales``: presentacion fiscal con navegador.

Porta ``api-bots-mrbot-v2/app/bot/controladores_fiscales_bot.py``
(``controladores_fiscales_bot``) al contrato worker V3. Cambios
obligatorios respecto de V2:

- Sin ``archivos_dir``/``descargas_dir`` del host: los archivos a
  presentar llegan como adjuntos del sobre bajo ``runtime.work_dir`` y
  se resuelven con ``artifact_store.resolve``; las constancias se
  descargan a ``work_dir``.
- Sin ``os.environ`` ni ``get_proxy_config()``: proxy y credenciales
  llegan por ``runtime`` desde el sobre sellado.
- Sin subida a MinIO con claves: cada constancia PDF se sube con
  ``artifact_store.upload`` (slot ``constancia_pdf``).
- Sin credenciales en logs: categoria + diagnostico redactado.

La presentacion tiene efecto fiscal: el manifiesto declara
``idempotency_class="EFECTO"`` para que la central la trate como
operacion no reintentable a ciegas. Los reintentos ante estados
transitorios del sitio los gobierna el supervisor con el flag
``retryable`` de cada categoria.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Mapping

from bot_worker.bots.controladores_fiscales.schema import (
    ENTRADAS,
    esquema_entrada,
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

SERVICIO_ARCA = "PRESENTACIÓN DDJJ Y PAGOS -"
ID_ARTEFACTO_CONSTANCIA = "constancia_pdf"


def nombre_constancia(archivo: str) -> str:
    """Deriva el nombre de la constancia desde el archivo presentado."""
    tallo = Path(archivo).stem
    seguro = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in tallo)
    return f"constancia_{seguro[:100]}.pdf"


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


class ControladoresFiscalesPlugin:
    """Presenta archivos fiscales y descarga sus constancias."""

    manifest = BotManifest(
        nombre="controladores_fiscales",
        version="3.0.0",
        operaciones=("presentar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="constancia.pdf",
                content_types=("application/pdf",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1800,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=True,
        costo_creditos_sugerido=2,
        idempotency_class="EFECTO",
        browser_instances_max=1,
        hosts_permitidos=(
            "www.afip.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __init__(self, servicio: str | None = None) -> None:
        """Valor por defecto; la seccion sellada lo ajusta en ``configure``."""
        self._servicio = servicio or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "ControladoresFiscalesPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "ControladoresFiscalesPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Clave conocida: ``servicio`` (nombre visible del servicio ARCA).
        Sin seccion rige el valor por defecto V2.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("servicio"):
            self._servicio = str(service["servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion y nombres de archivo antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "presentar"))
        modelo = ENTRADAS.get(operacion)
        if modelo is None:
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return (operacion, modelo.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Ejecuta la presentacion y retorna ``BotResult`` tipado."""
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
                servicio = await sesion.open_service(self._servicio)
                datos, artefactos = await self._presentar(
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

    async def _presentar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Presenta cada archivo y descarga su constancia a ``work_dir``."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Presentando archivos"
        )
        presentados: list[dict[str, Any]] = []
        artefactos: list[dict[str, Any]] = []
        total = len(entrada.archivos)
        for indice, nombre in enumerate(entrada.archivos):
            await runtime.cancellation.raise_if_cancelled()
            try:
                origen = runtime.artifact_store.resolve(nombre)
            except ValueError as exc:
                raise InvalidInputError(f"archivo fuera de work_dir: {nombre}") from exc
            if not origen.is_file():
                raise InvalidInputError(f"archivo no encontrado en work_dir: {nombre}")
            destino = runtime.artifact_store.resolve(nombre_constancia(nombre))
            estado = await servicio.presentar_archivo(
                archivo=str(origen),
                destino=destino,
            )
            registro: dict[str, Any] = {
                "archivo": nombre,
                "estado": str(estado),
                "constancia": destino.name,
            }
            if getattr(entrada, "subir_constancia", True):
                await runtime.event_sink.progress(
                    phase="SUBIENDO",
                    percent=60 + int(20 * (indice + 1) / max(total, 1)),
                    message=f"Subiendo constancia {indice + 1}/{total}",
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        ID_ARTEFACTO_CONSTANCIA, destino.name
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                registro["sha256"] = referencia["sha256"]
                registro["size_bytes"] = referencia["size_bytes"]
            presentados.append(registro)
        datos: dict[str, Any] = {
            "operacion": "presentar",
            "archivos_presentados": len(presentados),
        }
        if getattr(entrada, "incluir_json", True):
            datos["detalle"] = presentados
        return datos, artefactos
