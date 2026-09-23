"""Plugin ``libros_portal_iva``: Portal IVA con navegador (worker V3).

Porta ``api-bots-mrbot-v2/app/bot/libros_portal_iva_bot.py``
(``bot_libros_portal_iva``) al contrato worker V3. Cambios obligatorios
respecto de V2:

- Sin ``SessionLocal``: la central persiste tras el callback.
- Sin ``tempfile`` propio: las descargas viven bajo
  ``runtime.work_dir`` via ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: proxy y credenciales
  llegan por ``runtime`` desde el sobre sellado.
- Sin ``_subir_a_minio`` con claves: cada archivo se sube con
  ``artifact_store.upload`` (slots ``libros_iva_archivo`` y
  ``ddjj_archivo``).
- Sin links de MinIO en el resultado (V2 los recorta con
  ``_strip_minio_links``): el resultado trae nombres, periodos y
  hashes, nunca URLs firmadas.
- Sin credenciales en logs: categoria + diagnostico redactado.

Dos operaciones (``descargar_libros`` y ``descargar_ddjj``) que reflejan
``_download_libros_iva`` y ``_download_ddjj`` de V2 sobre el Portal IVA
(``siapweb.cloud.afip.gob.ar/iva``).

La seccion ``service`` del sobre sellado (vía :meth:`configure`) solo
ajusta la URL del portal.
"""

from __future__ import annotations

import asyncio
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
from bot_worker.bots.libros_portal_iva.schema import (
    ENTRADAS,
    esquema_entrada,
    generar_rango_periodos,
)
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

PORTAL_IVA_URL = "https://siapweb.cloud.afip.gob.ar/iva"
ID_ARTEFACTO_LIBROS = "libros_iva_archivo"
ID_ARTEFACTO_DDJJ = "ddjj_archivo"

IDS_POR_OPERACION = {
    "descargar_libros": ID_ARTEFACTO_LIBROS,
    "descargar_ddjj": ID_ARTEFACTO_DDJJ,
}


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


class LibrosPortalIvaPlugin:
    """Descarga libros IVA y DDJJ del Portal IVA por rango AAAAMM."""

    manifest = BotManifest(
        nombre="libros_portal_iva",
        version="3.0.0",
        operaciones=("descargar_libros", "descargar_ddjj"),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="libros_iva.bin",
                content_types=("application/octet-stream",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="ddjj.bin",
                content_types=("application/octet-stream",),
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
            "www.afip.gob.ar",
            "siapweb.cloud.afip.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __init__(self, portal_url: str | None = None) -> None:
        """Valor por defecto; la seccion sellada lo ajusta en ``configure``."""
        self._portal_url = portal_url or PORTAL_IVA_URL

    def __repr__(self) -> str:
        return "LibrosPortalIvaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "LibrosPortalIvaPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Clave conocida: ``portal_iva_url`` (URL del Portal IVA). Sin
        seccion rige el valor por defecto V2.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("portal_iva_url"):
            self._portal_url = str(service["portal_iva_url"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT y rango AAAAMM antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "descargar_libros"))
        modelo = ENTRADAS.get(operacion)
        if modelo is None:
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return (operacion, modelo.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Ejecuta la descarga y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotResult

        operacion, entrada = payload
        if runtime.credentials is None:
            raise CredentialsRejectedError("el plugin requiere credenciales fiscales")
        secretos = [runtime.credentials.clave]
        representado = entrada.representado_cuit or runtime.credentials.cuit_representante
        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="LOGIN", percent=10, message="Iniciando sesion fiscal"
        )
        if runtime.deadline.remaining_seconds() <= 0:
            raise DeadlineExceededError("deadline agotado antes de navegar")
        try:
            periodos = generar_rango_periodos(
                entrada.periodo_desde, entrada.periodo_hasta
            )
        except ValueError as exc:
            raise InvalidInputError(f"rango de periodos invalido: {exc}") from exc
        try:
            async with runtime.browser_factory.arca_session(
                credentials=runtime.credentials,
                proxy=runtime.proxy,
                deadline=runtime.deadline,
                cancellation=runtime.cancellation,
            ) as sesion:
                await sesion.login()
                await runtime.cancellation.raise_if_cancelled()
                portal = await sesion.open_service(self._portal_url)
                await portal.seleccionar_representado(representado)
                datos, artefactos = await self._descargar(
                    portal, entrada, representado, periodos, operacion, runtime
                )
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        datos["operacion"] = operacion
        return BotResult(result="OK", data=datos, artifacts=artefactos)

    async def _descargar(
        self,
        portal: Any,
        entrada: Any,
        representado: str,
        periodos: list[str],
        operacion: str,
        runtime: BotRuntime,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Descarga por periodo a ``work_dir`` y sube cada archivo por slot."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Descargando periodos"
        )
        id_artefacto = IDS_POR_OPERACION[operacion]
        descargados: list[dict[str, Any]] = []
        con_error: list[str] = []
        artefactos: list[dict[str, Any]] = []
        total = len(periodos)
        for indice, periodo in enumerate(periodos):
            await runtime.cancellation.raise_if_cancelled()
            destino = runtime.artifact_store.resolve(
                f"{operacion}_{representado}_{periodo}.bin"
            )
            try:
                nombre = await portal.descargar_periodo(
                    operacion=operacion,
                    periodo=periodo,
                    destino=destino,
                )
            except ErrorDeBot:
                raise
            except Exception as exc:
                con_error.append(periodo)
                descargados.append({"periodo": periodo, "error": type(exc).__name__})
                continue
            registro: dict[str, Any] = {
                "periodo": periodo,
                "archivo": str(nombre or destino.name),
            }
            if getattr(entrada, "subir_archivos", True):
                await runtime.event_sink.progress(
                    phase="SUBIENDO",
                    percent=60 + int(20 * (indice + 1) / max(total, 1)),
                    message=f"Subiendo periodo {indice + 1}/{total}",
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        id_artefacto, destino.name
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                registro["sha256"] = referencia["sha256"]
                registro["size_bytes"] = referencia["size_bytes"]
            descargados.append(registro)
        datos: dict[str, Any] = {
            "representado_cuit": representado,
            "periodo_desde": entrada.periodo_desde,
            "periodo_hasta": entrada.periodo_hasta,
            "periodos_descargados": [
                d["periodo"] for d in descargados if "archivo" in d
            ],
            "periodos_error": con_error,
        }
        if getattr(entrada, "incluir_json", True):
            datos["detalle"] = descargados
        return datos, artefactos
