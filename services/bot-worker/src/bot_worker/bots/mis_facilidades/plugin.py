"""Plugin ``mis_facilidades``: planes de facilidades de pago (ola 3).

Porta ``api-bots-mrbot-v2/app/bot/mis_facilidades_bot.py``
(``bot_mis_facilidades``: detalle de planes, cuotas y obligaciones con
exportacion a PDF y Excel) al contrato S7. Cambios obligatorios
respecto de V2:

- Sin ``SessionLocal`` ni escritura de ``ConsultaLog``: la central
  persiste el resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni ``descargas/...``: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya construido
  viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin ``cookies_header`` en el resultado: solo resumen + referencias
  de artefactos; las cookies nunca salen del worker.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.
- ``configure`` aplica la seccion ``service`` del sobre sellado (por
  job) sobre una copia del plugin, nunca sobre el registro compartido.

La sesion de navegador se obtiene de ``runtime.browser_factory`` con la
interfaz minima documentada abajo; el plugin nunca importa Playwright
directo ni desactiva headless/proxy/limpieza.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any, Mapping

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
from bot_worker.bots.mis_facilidades.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "MIS FACILIDADES"
ID_ARTEFACTO_XLSX = "planes_xlsx"
ID_ARTEFACTO_PDF = "plan_pdf"


def nombre_base_archivo(representado_cuit: str, plan: str = "") -> str:
    """Replica el patron de nombres V2 ``'<cuit> - plan - ...'``."""
    digitos = re.sub(r"\D", "", representado_cuit or "")
    sufijo = f" - {re.sub(r'[\\\\/:*?\"<>|]', '_', plan).strip()}" if plan else ""
    return f"{digitos} - MIS FACILIDADES{sufijo}"


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


class MisFacilidadesPlugin:
    """Mis Facilidades de ARCA: planes, cuotas y obligaciones."""

    manifest = BotManifest(
        nombre="mis_facilidades",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="planes.xlsx",
                content_types=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet",
                ),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="plan.pdf",
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
        idempotency_class="CONTINUACION",
        browser_instances_max=1,
        hosts_permitidos=(
            "www.afip.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __init__(self, defectos: dict[str, Any] | None = None) -> None:
        """Valores por defecto que la seccion ``service`` puede presetear."""
        self._defectos: dict[str, Any] = dict(defectos or {})

    def __repr__(self) -> str:
        return "MisFacilidadesPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "MisFacilidadesPlugin":
        """Aplica la sección ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia del
        registro compartido. Solo se aceptan claves conocidas; el resto se
        ignora para no inyectar parametros imprevistos al flujo.
        """
        service = service if isinstance(service, dict) else {}
        for clave in (
            "situacion_excluyente",
            "incluir_pdf",
            "incluir_xlsx",
            "subir_archivos",
        ):
            if clave in service:
                self._defectos[clave] = service[clave]
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT y salidas antes de abrir el navegador."""
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
        """Ejecuta la operacion validada y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotError, BotResult

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
                servicio = await sesion.open_service(SERVICIO_ARCA)
                cuit_objetivo = (
                    entrada.representado_cuit or runtime.credentials.cuit_representante
                )
                await servicio.seleccionar_representado(cuit_objetivo)
                datos, artefactos = await self._consultar(
                    servicio, entrada, runtime, cuit_objetivo
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

    async def _consultar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime, cuit_objetivo: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Lista planes, filtra por situacion y exporta PDF/Excel (port V2)."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Listando planes"
        )
        planes = await servicio.listar_planes()
        excluyentes = [str(s).lower() for s in (entrada.situacion_excluyente or [])]
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": cuit_objetivo,
            "planes": [],
        }
        artefactos: list[dict[str, Any]] = []
        for plan in planes or []:
            situacion = str((plan or {}).get("situacion", "") or "")
            if "plan cancelado" in situacion.lower():
                continue
            if excluyentes and any(e in situacion.lower() for e in excluyentes):
                continue
            await runtime.cancellation.raise_if_cancelled()
            numero = str((plan or {}).get("numero", "") or "s-n")
            base = nombre_base_archivo(cuit_objetivo, numero)
            resumen: dict[str, Any] = {"numero": numero, "situacion": situacion}
            if entrada.incluir_xlsx:
                destino = runtime.artifact_store.resolve(f"{base}.xlsx")
                await servicio.exportar_plan_xlsx(numero=numero, destino=destino)
                resumen["xlsx"] = await self._resumir_y_subir(
                    destino, ID_ARTEFACTO_XLSX, entrada, runtime, "Subiendo plan"
                )
                artefactos.extend(resumen["xlsx"].pop("referencias"))
            if entrada.incluir_pdf:
                destino_pdf = runtime.artifact_store.resolve(f"{base}.pdf")
                await servicio.exportar_plan_pdf(numero=numero, destino=destino_pdf)
                resumen["pdf"] = await self._resumir_y_subir(
                    destino_pdf, ID_ARTEFACTO_PDF, entrada, runtime, "Subiendo PDF"
                )
                artefactos.extend(resumen["pdf"].pop("referencias"))
            datos["planes"].append(resumen)
        datos["planes_procesados"] = len(datos["planes"])
        return datos, artefactos

    async def _resumir_y_subir(
        self,
        destino: Path,
        artifact_id: str,
        entrada: Any,
        runtime: BotRuntime,
        mensaje: str,
    ) -> dict[str, Any]:
        """Resume un archivo de ``work_dir`` y lo sube por slot prefirmado."""
        resumen: dict[str, Any] = {
            "archivo": destino.name,
            "size_bytes": destino.stat().st_size if destino.is_file() else 0,
        }
        referencias: list[dict[str, Any]] = []
        if entrada.subir_archivos:
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message=mensaje
            )
            try:
                referencia = await runtime.artifact_store.upload(
                    artifact_id, destino.name
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            referencias.append(referencia)
            resumen["sha256"] = referencia["sha256"]
            resumen["size_bytes"] = referencia["size_bytes"]
        resumen["referencias"] = referencias
        return resumen
