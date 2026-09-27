"""Plugin ``srt``: consulta de alicuotas ART en E-Servicios SRT.

Porta ``api-bots-mrbot-v2/app/bot/srt_bot.py``
(``bot_srt_alicuotas``) al contrato S7. Cambios obligatorios
respecto de V2:

- Sin ``SessionLocal`` ni escritura en base: la central persiste el
  resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni temporales sueltos: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya
  construido viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin CAPTCHA resuelto con clave del entorno: el desafio se delega
  al perfil captcha del runtime (proveedor inyectado por la central).
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory``
con la interfaz minima documentada abajo; el plugin nunca importa
Playwright directo. El objeto ``servicio`` expone:

- ``consultar_cuit(cuit)``: consulta la alicuota ART del CUIT y
  retorna ``{"alicuota": str, "tablas": [...]}`` o
  ``{"sin_datos": "<motivo>"}`` cuando no hay afiliacion vigente.
"""

from __future__ import annotations

import asyncio
import json
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
from bot_worker.bots.srt.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_SRT = "E-SERVICIOS SRT"
SRT_ALICUOTAS_URL_DEFAULT = "https://eservicios.srt.gob.ar/Consultas/Alicuotas/Default.aspx"
ID_ARTEFACTO_JSON = "srt_alicuotas_json"

_NO_DATA_PATTERNS = (
    re.compile(r"no\s+tiene\s+afiliaci[oó]n\s+vigente", re.I),
    re.compile(r"no\s+registra\s+afiliaci[oó]n", re.I),
)


def es_respuesta_sin_datos(texto: str) -> str | None:
    """Detecta el motivo de afiliacion ausente (port de V2).

    Retorna el patron coincidente o ``None`` si hay datos.
    """
    for patron in _NO_DATA_PATTERNS:
        if patron.search(texto or ""):
            return patron.pattern
    return None


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


class SrtPlugin:
    """E-Servicios SRT: alicuotas ART por lote de CUIT."""

    manifest = BotManifest(
        nombre="srt",
        version="3.0.0",
        operaciones=("consultar_alicuotas",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="srt_alicuotas.json",
                content_types=("application/json",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1800,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=True,
        costo_creditos_sugerido=2,
        idempotency_class="LECTURA",
        browser_instances_max=1,
        hosts_permitidos=(
            "eservicios.srt.gob.ar",
            "www.afip.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __init__(
        self,
        servicio_nombre: str | None = None,
        alicuotas_url: str | None = None,
    ) -> None:
        """Inyecta servicio y URL de consulta desde el sobre sellado."""
        self._servicio_nombre = servicio_nombre or SERVICIO_SRT
        self._alicuotas_url = alicuotas_url or SRT_ALICUOTAS_URL_DEFAULT

    def __repr__(self) -> str:
        return "SrtPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "SrtPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Sin seccion rigen los defaults SRT.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("srt_servicio"):
            self._servicio_nombre = str(service["srt_servicio"])
        if service.get("srt_alicuotas_url"):
            self._alicuotas_url = str(service["srt_alicuotas_url"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion y lote de CUIT antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "consultar_alicuotas"))
        modelo = ENTRADAS.get(operacion)
        if modelo is None:
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return (operacion, modelo.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Ejecuta el lote y retorna ``BotResult`` tipado."""
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
                servicio = await sesion.open_service(
                    self._servicio_nombre, url=self._alicuotas_url, portal="srt"
                )
                datos, artefactos = await self._consultar_lote(
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

    async def _consultar_lote(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Consulta cada CUIT y guarda el consolidado en ``work_dir``."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Consultando alicuotas SRT"
        )
        consultas: list[dict[str, Any]] = []
        total = len(entrada.cuits_consulta)
        for indice, cuit in enumerate(entrada.cuits_consulta):
            await runtime.cancellation.raise_if_cancelled()
            respuesta = await servicio.consultar_cuit(cuit=cuit)
            fila: dict[str, Any] = {"cuit": cuit, **dict(respuesta or {})}
            consultas.append(fila)
            await runtime.event_sink.progress(
                phase="CONSULTA",
                percent=45 + int(35 * (indice + 1) / max(total, 1)),
                message=f"Consultado {indice + 1}/{total}",
            )
        datos: dict[str, Any] = {
            "operacion": "consultar_alicuotas",
            "cantidad": len(consultas),
        }
        artefactos: list[dict[str, Any]] = []
        consolidado = runtime.artifact_store.resolve("srt_alicuotas.json")
        consolidado.write_text(
            json.dumps({"consultas": consultas}, ensure_ascii=False)[:1_048_576],
            encoding="utf-8",
        )
        if getattr(entrada, "subir_json", True):
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message="Subiendo consolidado"
            )
            try:
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_JSON, consolidado.name
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            artefactos.append(referencia)
            datos["sha256"] = referencia["sha256"]
            datos["size_bytes"] = referencia["size_bytes"]
        if getattr(entrada, "incluir_json", True):
            datos["consultas"] = consultas
        return datos, artefactos
