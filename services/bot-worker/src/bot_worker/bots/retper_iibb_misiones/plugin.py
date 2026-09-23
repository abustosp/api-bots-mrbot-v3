"""Plugin ``retper_iibb_misiones``: retenciones/percepciones ATM Misiones.

Porta ``api-bots-mrbot-v2/app/bot/retper_iibb_misiones_bot.py``
(``bot_retper_iibb_misiones``) al contrato S7. Cambios obligatorios
respecto de V2:

- Sin ``SessionLocal`` ni escritura en base: la central persiste el
  resultado tras el callback idempotente.
- Sin ``tempfile`` fuera del workspace: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya
  construido viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin CAPTCHA resuelto con clave del entorno: el desafio se delega
  al perfil captcha del runtime (proveedor inyectado por la central).
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory``
con la interfaz minima documentada abajo; el plugin nunca importa
Playwright directo. El objeto ``servicio`` expone:

- ``ingresar()``: login en la extranet ATM con las credenciales
  fiscales del runtime (resuelve CAPTCHA via perfil inyectado).
- ``consultar_retper(desde_site, hasta_site, destino_xlsx)``:
  aplica el rango ``AAAA/MM`` y guarda el Excel en ``destino_xlsx``.
- ``generar_pdf(destino_pdf)``: genera el PDF del reporte.
"""

from __future__ import annotations

import asyncio
import re
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
from bot_worker.bots.retper_iibb_misiones.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

ATM_BASE_URL_DEFAULT = "https://extranet.atmisiones.gob.ar"
ID_ARTEFACTO_XLSX = "retper_misiones_xlsx"
ID_ARTEFACTO_PDF = "retper_misiones_pdf"

INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')


def _sanitize_filename_component(value: str) -> str:
    """Sanitiza un tramo de nombre de archivo (port de V2)."""
    sanitized = INVALID_FILENAME_RE.sub("_", value or "")
    return re.sub(r"\s+", " ", sanitized).strip()


def _period_to_site_format(periodo: str) -> str:
    """Convierte ``AAAAMM`` al formato del sitio ``AAAA/MM``."""
    periodo = (periodo or "").strip()
    if len(periodo) == 6 and periodo.isdigit():
        return f"{periodo[:4]}/{periodo[4:6]}"
    return periodo


def _build_filename_base(cuit: str, desde: str, hasta: str, denominacion: str) -> str:
    """Replica el patron de nombres V2."""
    cuit_digits = re.sub(r"\D", "", cuit or "")
    fin_cuit = cuit_digits[-1:] if cuit_digits else ""
    parts = [
        fin_cuit,
        cuit_digits,
        desde,
        hasta,
        "RETPER IIBB MISIONES",
        _sanitize_filename_component(denominacion),
    ]
    return " - ".join([part for part in parts if part])


def _looks_like_pdf(data: bytes) -> bool:
    """Verifica la firma minima de un PDF descargado (port de V2)."""
    if not data or len(data) < 100:
        return False
    return data[:4] == b"%PDF"


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnostico seguro."""
    if isinstance(exc, ErrorDeBot):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return TargetUnavailableError("timeout del sitio del organismo")
    texto = sin_secretos(f"{type(exc).__name__}", secretos)
    nombre = type(exc).__name__.lower()
    if "captcha" in nombre or "recaptcha" in nombre:
        return CaptchaUnsolvableError(f"desafio no resoluble: {texto}")
    if "browser" in nombre or "playwright" in nombre:
        return BrowserCrashedError(f"navegador caido: {texto}")
    return TargetUnavailableError(f"falla del organismo: {texto}")


class RetperIibbMisionesPlugin:
    """Retenciones y percepciones IIBB de ATM Misiones."""

    manifest = BotManifest(
        nombre="retper_iibb_misiones",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="retper_misiones.xlsx",
                content_types=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="retper_misiones.pdf",
                content_types=("application/pdf",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1200,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=True,
        costo_creditos_sugerido=2,
        idempotency_class="CONTINUACION",
        browser_instances_max=1,
        hosts_permitidos=(
            "extranet.atmisiones.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __init__(self, base_url: str | None = None) -> None:
        """Inyecta la base de la extranet ATM desde el sobre sellado."""
        self._base_url = base_url or ATM_BASE_URL_DEFAULT

    def __repr__(self) -> str:
        return "RetperIibbMisionesPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "RetperIibbMisionesPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Sin seccion rige la extranet ATM.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("atm_base_url"):
            self._base_url = str(service["atm_base_url"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, denominacion y rango antes del navegador."""
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
            phase="LOGIN", percent=10, message="Iniciando sesion ATM"
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
                await sesion.login(url=f"{self._base_url}/Extranet/index.php")
                await runtime.cancellation.raise_if_cancelled()
                await sesion.ingresar()
                datos, artefactos = await self._consultar(sesion, entrada, runtime)
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
        self, sesion: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Consulta el rango, genera PDF y sube por slots prefirmados."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Consultando retenciones Misiones"
        )
        desde = _period_to_site_format(entrada.periodo_desde)
        hasta = _period_to_site_format(entrada.periodo_hasta)
        base = _build_filename_base(
            entrada.representado_cuit,
            entrada.periodo_desde,
            entrada.periodo_hasta,
            entrada.denominacion,
        )
        destino_xlsx = runtime.artifact_store.resolve(f"{base}.xlsx")
        await sesion.consultar_retper(
            desde_site=desde, hasta_site=hasta, destino_xlsx=destino_xlsx
        )
        await runtime.event_sink.progress(
            phase="PROCESANDO", percent=65, message="Generando PDF"
        )
        destino_pdf = runtime.artifact_store.resolve(f"{base}.pdf")
        await sesion.generar_pdf(destino_pdf=destino_pdf)
        with open(destino_pdf, "rb") as fh:
            if not _looks_like_pdf(fh.read(256)):
                raise TargetUnavailableError("el reporte PDF llego corrupto")
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": entrada.representado_cuit,
            "periodo_desde": entrada.periodo_desde,
            "periodo_hasta": entrada.periodo_hasta,
            "archivos": [destino_xlsx.name, destino_pdf.name],
        }
        artefactos: list[dict[str, Any]] = []
        if getattr(entrada, "subir_archivo", True):
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message="Subiendo reportes"
            )
            for artifact_id, nombre in (
                (ID_ARTEFACTO_XLSX, destino_xlsx.name),
                (ID_ARTEFACTO_PDF, destino_pdf.name),
            ):
                try:
                    referencia = await runtime.artifact_store.upload(
                        artifact_id, nombre
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
            datos["sha256_xlsx"] = artefactos[0]["sha256"]
            datos["sha256_pdf"] = artefactos[1]["sha256"]
        return datos, artefactos
