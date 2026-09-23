"""Plugin ``aportes_en_linea``: archivo historico de AFIP/ARCA con navegador.

Porta ``api-bots-mrbot-v2/app/bot/aportes_en_linea_bot.py`` (operacion
``descargar``: login fiscal, servicio "APORTES EN LÍNEA", boton
INGRESAR, popup "Archivo Histórico", descarga .xls) al contrato S7.
Cambios obligatorios respecto de V2:

- Sin ``SessionLocal`` ni ``ConsultaLog``: la central persiste el
  resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni temporales sueltos: la descarga vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya construido
  viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin base64 obligatorio: el xls puede volver inline solo si se pide
  y entra en el tope; por defecto viaja como artefacto.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory`` con
``arca_session``; el plugin nunca importa Playwright directo ni
desactiva headless/proxy/limpieza.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import datetime
from typing import Any, Mapping

from bot_worker.bots.aportes_en_linea.schema import esquema_entrada
from bot_worker.bots.aportes_en_linea.schema import (
    AportesDescargarInput,
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

SERVICIO_ARCA = "APORTES EN LÍNEA"
ID_ARTEFACTO_HISTORICO = "historico.xls"
TOPE_BASE64_BYTES = 5_242_880


def nombre_archivo_historico(representado_cuit: str, fecha: str | None = None) -> str:
    """Replica el patron V2 ``'AEL - <cuit> - <AAAAMMDD>.xls'``."""
    sello = fecha or datetime.now().strftime("%Y%m%d")
    return f"AEL - {representado_cuit} - {sello}.xls"


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


class AportesEnLineaPlugin:
    """Archivo historico de Aportes en Linea (ARCA)."""

    manifest = BotManifest(
        nombre="aportes_en_linea",
        version="3.0.0",
        operaciones=("descargar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="historico.xls",
                content_types=(
                    "application/vnd.ms-excel",
                    "application/octet-stream",
                ),
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
        hosts_permitidos=("www.afip.gob.ar",),
    )

    def __init__(self, servicio: str | None = None) -> None:
        """Inyecta el nombre del servicio desde el sobre sellado."""
        self._servicio = servicio or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "AportesEnLineaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "AportesEnLineaPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Clave: ``aportes_servicio``.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("aportes_servicio"):
            self._servicio = str(service["aportes_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT y salidas antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "descargar"))
        if operacion != "descargar":
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return ("descargar", AportesDescargarInput.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Descarga el historico y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotResult

        _, entrada = payload
        if runtime.credentials is None:
            raise CredentialsRejectedError("el plugin requiere credenciales fiscales")
        representado = (
            entrada.representado_cuit or runtime.credentials.cuit_representante
        )
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
                pagina = await sesion.open_service(self._servicio)
                await pagina.get_by_role("button", name="INGRESAR").click()
                await runtime.event_sink.progress(
                    phase="CONSULTA",
                    percent=45,
                    message="Descargando archivo historico",
                )
                nombre = nombre_archivo_historico(representado)
                destino = runtime.artifact_store.resolve(nombre)
                async with pagina.expect_download() as info_descarga:
                    async with pagina.expect_popup() as info_popup:
                        await pagina.get_by_role(
                            "button", name="Archivo Histórico"
                        ).click()
                    popup = await info_popup.value
                    await popup.close()
                descarga = await info_descarga.value
                await descarga.save_as(str(destino))
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        datos: dict[str, Any] = {
            "operacion": "descargar",
            "representado_cuit": representado,
            "archivo": destino.name,
        }
        artefactos: list[dict[str, Any]] = []
        if entrada.subir:
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message="Subiendo historico"
            )
            try:
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_HISTORICO, destino.name
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            artefactos.append(referencia)
            datos["sha256"] = referencia["sha256"]
            datos["size_bytes"] = referencia["size_bytes"]
        if entrada.incluir_base64:
            contenido = destino.read_bytes()
            if len(contenido) > TOPE_BASE64_BYTES:
                datos.setdefault("warnings", []).append(
                    "historico excede tope base64; viaja solo como artefacto"
                )
            else:
                datos["archivo_historico_b64"] = base64.b64encode(contenido).decode(
                    "ascii"
                )
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return BotResult(result="OK", data=datos, artifacts=artefactos)
