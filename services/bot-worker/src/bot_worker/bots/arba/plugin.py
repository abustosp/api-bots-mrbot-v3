"""Plugin ``arba``: Ret/Per IIBB con navegador (login directo ARBA).

Porta ``api-bots-mrbot-v2/app/bot/arba_bot.py`` (login SSO ARBA, rol
Contribuyente, consulta por anio/mes con doble intento, descarga zip)
al contrato S7. Cambios obligatorios respecto de V2:

- Sin ``SessionLocal`` ni base de datos (W-1).
- Sin ``os.getcwd()`` ni temporales sueltos: el zip vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: ARBA no usa ARCA, asi
  que la sesion sale de ``browser_factory.new_context()`` (el proxy ya
  construido viaja en la factory); la URL de login se inyecta por
  constructor desde el sobre sellado.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave nunca se interpola.

El plugin nunca importa Playwright directo ni desactiva
headless/proxy/limpieza: el contexto lo abre y cierra la factory.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from bot_worker.bots.arba.schema import esquema_entrada, nombre_archivo_descarga
from bot_worker.bots.arba.schema import ArbaDescargarInput
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

URL_LOGIN = (
    "https://sso.arba.gov.ar/Login/login"
    "?service=https%3A%2F%2Fdfe.arba.gov.ar%3A443%2FDomicilioElectronico"
    "%2FpreDescargarRetenciones.do"
)
URL_CONSULTA = (
    "https://dfe.arba.gov.ar/DomicilioElectronico/preDescargarRetenciones.do"
)
ROL_CONTRIBUYENTE = "Contribuyente/Contribuyente de Ingresos Brutos"
ID_ARTEFACTO_RETPER = "retper_arba.zip"


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


class ArbaPlugin:
    """Retenciones/Percepciones IIBB ARBA: consulta y descarga zip."""

    manifest = BotManifest(
        nombre="arba",
        version="3.0.0",
        operaciones=("descargar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="retper_arba.zip",
                content_types=("application/zip", "application/octet-stream"),
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
        hosts_permitidos=("sso.arba.gov.ar", "dfe.arba.gov.ar"),
    )

    def __init__(
        self,
        login_url: str | None = None,
        consulta_url: str | None = None,
    ) -> None:
        """Inyecta URLs del organismo desde el sobre sellado."""
        self._login_url = login_url or URL_LOGIN
        self._consulta_url = consulta_url or URL_CONSULTA

    def __repr__(self) -> str:
        return "ArbaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "ArbaPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Claves: ``arba_login_url``,
        ``arba_consulta_url``.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("arba_login_url"):
            self._login_url = str(service["arba_login_url"])
        if service.get("arba_consulta_url"):
            self._consulta_url = str(service["arba_consulta_url"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, periodo y denominacion sin navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "descargar"))
        if operacion != "descargar":
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return ("descargar", ArbaDescargarInput.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Ejecuta la consulta ARBA y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotResult

        _, entrada = payload
        if runtime.credentials is None:
            raise CredentialsRejectedError("el plugin requiere credenciales fiscales")
        if not (runtime.credentials.clave or "").strip():
            raise CredentialsRejectedError("la clave fiscal es obligatoria")
        secretos = [runtime.credentials.clave]
        periodo = entrada.periodo
        anio, mes = periodo[:4], periodo[4:6]
        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="LOGIN", percent=10, message="Iniciando sesion ARBA"
        )
        if runtime.deadline.remaining_seconds() <= 0:
            raise DeadlineExceededError("deadline agotado antes de navegar")
        try:
            async with runtime.browser_factory.new_context() as (_, contexto):
                pagina = await contexto.new_page()
                pagina.set_default_timeout(60_000)
                await pagina.goto(self._login_url)
                await pagina.get_by_role(
                    "textbox", name="Ingresá los 11 dígitos sin"
                ).fill(entrada.representado_cuit)
                await pagina.get_by_role("textbox", name="Ingresá tu clave").fill(
                    runtime.credentials.clave
                )
                await pagina.get_by_role("button", name="Ingresar").click()
                await pagina.wait_for_load_state("networkidle")
                rol = pagina.locator("select[name='rol']")
                if await rol.count() > 0:
                    await rol.select_option(ROL_CONTRIBUYENTE)
                    await pagina.get_by_role("button", name="Continuar").click()
                    await pagina.wait_for_load_state("networkidle")
                if "sso.arba.gov.ar/Login/login" in pagina.url:
                    raise CredentialsRejectedError("el organismo rechazo las credenciales")
                await runtime.cancellation.raise_if_cancelled()
                await runtime.event_sink.progress(
                    phase="CONSULTA", percent=45, message="Consultando periodo"
                )
                sin_archivos = False
                for _ in range(2):  # port V2: la primera consulta no siempre lista
                    await pagina.goto(self._consulta_url)
                    await pagina.wait_for_load_state("networkidle")
                    await pagina.locator("input[name=\"anio\"]").fill(anio)
                    await pagina.locator("input[name=\"mes\"]").fill(mes)
                    await pagina.get_by_role("button", name="Consultar").click()
                    await pagina.wait_for_load_state("networkidle")
                    contenido = await pagina.content()
                    if "No Existen archivos para el periodo solicitado" in contenido:
                        sin_archivos = True
                        break
                    try:
                        await pagina.get_by_role(
                            "button", name="Descargar"
                        ).wait_for(state="visible", timeout=5_000)
                        break
                    except Exception:
                        continue
                if sin_archivos:
                    return BotResult(
                        result="OK",
                        data={
                            "operacion": "descargar",
                            "representado_cuit": entrada.representado_cuit,
                            "periodo": periodo,
                            "archivos": [],
                            "mensaje": "No existen archivos para el periodo solicitado",
                        },
                    )
                nombre = nombre_archivo_descarga(
                    entrada.representado_cuit, periodo, entrada.representado_nombre
                )
                destino = runtime.artifact_store.resolve(nombre)
                async with pagina.expect_download() as info_descarga:
                    await pagina.get_by_role("button", name="Descargar").click()
                descarga = await info_descarga.value
                await descarga.save_as(str(destino))
                if not destino.exists():
                    raise TargetUnavailableError(
                        "no se pudo descargar el archivo de Ret/Per IIBB ARBA"
                    )
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        datos: dict[str, Any] = {
            "operacion": "descargar",
            "representado_cuit": entrada.representado_cuit,
            "periodo": periodo,
            "archivo": destino.name,
        }
        artefactos: list[dict[str, Any]] = []
        if entrada.subir:
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message="Subiendo zip"
            )
            try:
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_RETPER, destino.name
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
