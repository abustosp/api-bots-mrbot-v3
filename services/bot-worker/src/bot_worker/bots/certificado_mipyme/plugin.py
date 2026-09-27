"""Plugin ``certificado_mipyme``: descarga del certificado MiPyME (LUFE).

Porta ``api-bots-mrbot-v2/app/bot/certificado_mipyme_bot.py``
(``descargar_certificado_mipyme``: sesion ARCA, servicio LUFE,
combobox de representado, boton Seleccionar, link "Descargar
Certificado MiPyME") al contrato S7. Cambios obligatorios respecto
de V2:

- Sin ``SessionLocal`` ni base de datos (W-1).
- Sin ``os.getcwd()``/``descargas/...``: el PDF vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya construido
  viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre
  (``certificado.pdf``, obligatorio como en el manifiesto F2).
- Sin ``local_path`` ni ``opciones_encontradas`` en crudo: el
  resultado lleva nombre de archivo y referencia de subida.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory`` con
``arca_session``; el plugin nunca importa Playwright directo ni
desactiva headless/proxy/limpieza.

NOTA: ``registry.py`` conserva el stub F2 ``CertificadoMipymePlugin``
(lo cablea la central); este modulo es el port real y no toca el
registro.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Mapping

from bot_worker.bots.certificado_mipyme.schema import esquema_entrada
from bot_worker.bots.certificado_mipyme.schema import (
    CertificadoMipymeDescargarInput,
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

SERVICIO_ARCA = "LUFE"
ID_ARTEFACTO_CERTIFICADO = "certificado.pdf"


def nombre_certificado(representado_cuit: str) -> str:
    """Replica el patron V2 ``'sepyme_<cuit>_<ts>.pdf'``."""
    sello = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"sepyme_{representado_cuit}_{sello}.pdf"


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


class CertificadoMipymePlugin:
    """Certificado MiPyME: seleccion de representado y descarga PDF."""

    manifest = BotManifest(
        nombre="certificado_mipyme",
        version="3.0.0",
        operaciones=("descargar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="certificado.pdf",
                content_types=("application/pdf",),
                max_bytes=10_485_760,
                obligatorio=True,
            ),
        ),
        timeout_por_defecto_seconds=600,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=1,
        idempotency_class="LECTURA",
        browser_instances_max=1,
        hosts_permitidos=("www.afip.gob.ar",),
    )

    def __init__(self, servicio: str | None = None) -> None:
        """Inyecta el nombre del servicio desde el sobre sellado."""
        self._servicio = servicio or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "CertificadoMipymePlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "CertificadoMipymePlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Clave: ``mipyme_servicio``.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("mipyme_servicio"):
            self._servicio = str(service["mipyme_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion y CUIT representado antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "descargar"))
        if operacion != "descargar":
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return (
                "descargar",
                CertificadoMipymeDescargarInput.model_validate(datos),
            )
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Descarga el certificado y retorna ``BotResult`` tipado."""
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
                    self._servicio, portal="certificado_mipyme"
                )
                await servicio.seleccionar_representado(entrada.representado_cuit)
                await runtime.event_sink.progress(
                    phase="CONSULTA",
                    percent=45,
                    message="Descargando certificado",
                )
                nombre = nombre_certificado(entrada.representado_cuit)
                destino = runtime.artifact_store.resolve(nombre)
                await servicio.descargar_certificado(destino)
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        datos: dict[str, Any] = {
            "operacion": "descargar",
            "representado_cuit": entrada.representado_cuit,
            "archivo": destino.name,
        }
        artefactos: list[dict[str, Any]] = []
        if entrada.subir:
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message="Subiendo certificado"
            )
            try:
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_CERTIFICADO, destino.name
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            artefactos.append(referencia)
            datos["sha256"] = referencia["sha256"]
            datos["size_bytes"] = referencia["size_bytes"]
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return BotResult(result="OK", data=datos, artifacts=artefactos)
