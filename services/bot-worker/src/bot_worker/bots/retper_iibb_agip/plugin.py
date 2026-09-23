"""Plugin ``retper_iibb_agip``: retenciones/percepciones AGIP.

Porta ``api-bots-mrbot-v2/app/bot/retper_iibb_agip_bot.py``
(``bot_retper_iibb_agip``) al contrato S7. Cambios obligatorios
respecto de V2:

- Sin ``SessionLocal`` ni escritura en base: la central persiste el
  resultado tras el callback idempotente.
- Sin ``tempfile`` fuera del workspace: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya
  construido viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); usuario y clave nunca se interpolan.

La sesion de navegador se obtiene de ``runtime.browser_factory``
con la interfaz minima documentada abajo; el plugin nunca importa
Playwright directo. El objeto ``servicio`` expone:

- ``ingresar(usuario)``: login en ClaveCiudad con el usuario del
  sobre sellado (o el CUIT de las credenciales fiscales).
- ``consultar_retper(desde_mmyyyy, hasta_mmyyyy, destino)``:
  aplica el rango y guarda el reporte en ``destino``.
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
from bot_worker.bots.retper_iibb_agip.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

AGIP_LOGIN_URL_DEFAULT = "https://claveciudad.agip.gob.ar/"
ID_ARTEFACTO_REPORTE = "retper_agip_reporte"

INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')


def _safe_filename_part(value: str, default: str) -> str:
    """Sanitiza un tramo de nombre de archivo (port de V2)."""
    text = re.sub(r"\s+", " ", (value or "").strip())
    text = INVALID_FILENAME_RE.sub("", text).strip()
    return text or default


def _period_to_yyyymm(periodo: str) -> str:
    """Normaliza un periodo a ``AAAAMM`` (port de V2)."""
    digits = re.sub(r"\D", "", periodo or "")
    if len(digits) < 6:
        raise ValueError(f"Periodo invalido: {periodo!r}. Debe ser AAAAMM.")
    return digits[:6]


def _yyyymm_to_mmyyyy(periodo_yyyymm: str) -> str:
    """Convierte ``AAAAMM`` al formato de pantalla ``MM/AAAA``."""
    return f"{periodo_yyyymm[4:6]}/{periodo_yyyymm[:4]}"


def nombre_descarga_agip(
    cuit: str, tipo: str, desde: str, hasta: str, denominacion: str, extension: str
) -> str:
    """Replica el patron de nombres V2 ``'{fin} - {cuit} - ...'``."""
    digitos = re.sub(r"\D", "", cuit or "")
    return (
        f"{digitos[-1:]} - {digitos} - {_safe_filename_part(tipo, 'SIN_TIPO')} - "
        f"{desde} - {hasta} - "
        f"{_safe_filename_part(denominacion, 'SIN_DENOMINACION')}.{extension}"
    )


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnostico seguro."""
    if isinstance(exc, ErrorDeBot):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return TargetUnavailableError("timeout del sitio del organismo")
    texto = sin_secretos(f"{type(exc).__name__}", secretos)
    nombre = type(exc).__name__.lower()
    if "captcha" in nombre:
        return CaptchaUnsolvableError(f"desafio no resoluble: {texto}")
    if "browser" in nombre or "playwright" in nombre:
        return BrowserCrashedError(f"navegador caido: {texto}")
    return TargetUnavailableError(f"falla del organismo: {texto}")


class RetperIibbAgipPlugin:
    """Retenciones y percepciones IIBB de AGIP (Ciudad BA)."""

    manifest = BotManifest(
        nombre="retper_iibb_agip",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="retper_agip_reporte",
                content_types=("text/csv", "application/pdf", "text/html"),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1200,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=2,
        idempotency_class="CONTINUACION",
        browser_instances_max=1,
        hosts_permitidos=("claveciudad.agip.gob.ar",),
    )

    def __init__(
        self, login_url: str | None = None, usuario: str | None = None
    ) -> None:
        """Inyecta endpoint y usuario AGIP desde el sobre sellado."""
        self._login_url = login_url or AGIP_LOGIN_URL_DEFAULT
        self._usuario = usuario

    def __repr__(self) -> str:
        return "RetperIibbAgipPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "RetperIibbAgipPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Sin seccion rige ClaveCiudad AGIP y
        el usuario es el CUIT de las credenciales fiscales.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("agip_login_url"):
            self._login_url = str(service["agip_login_url"])
        if service.get("agip_usuario"):
            self._usuario = str(service["agip_usuario"])
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
            phase="LOGIN", percent=10, message="Iniciando sesion AGIP"
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
                await sesion.login(url=self._login_url)
                await runtime.cancellation.raise_if_cancelled()
                usuario = self._usuario or runtime.credentials.cuit
                await sesion.ingresar(usuario=sin_secretos(usuario, []))
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
        """Consulta el rango y sube el reporte por slot prefirmado."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Consultando retenciones AGIP"
        )
        desde = _yyyymm_to_mmyyyy(_period_to_yyyymm(entrada.periodo_desde))
        hasta = _yyyymm_to_mmyyyy(_period_to_yyyymm(entrada.periodo_hasta))
        nombre = nombre_descarga_agip(
            entrada.representado_cuit,
            "RETPER IIBB AGIP",
            entrada.periodo_desde,
            entrada.periodo_hasta,
            entrada.denominacion,
            "csv",
        )
        destino = runtime.artifact_store.resolve(nombre)
        await sesion.consultar_retper(desde_mmyyyy=desde, hasta_mmyyyy=hasta, destino=destino)
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": entrada.representado_cuit,
            "periodo_desde": entrada.periodo_desde,
            "periodo_hasta": entrada.periodo_hasta,
            "archivo": destino.name,
        }
        artefactos: list[dict[str, Any]] = []
        if getattr(entrada, "subir_archivo", True):
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message="Subiendo reporte"
            )
            try:
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_REPORTE, destino.name
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            artefactos.append(referencia)
            datos["sha256"] = referencia["sha256"]
            datos["size_bytes"] = referencia["size_bytes"]
        return datos, artefactos
