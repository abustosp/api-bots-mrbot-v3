"""Plugin ``rcel``: comprobantes en linea (facturas) sin tocar V2.

Porta ``api-bots-mrbot-v2/app/bot/rcel_bot.py`` (``descargar_facturas``)
al contrato S7. Cambios obligatorios respecto de V2:

- Sin ``SessionLocal`` ni escritura de logs en base: la central
  persiste el resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni ``descargas/...``: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya construido
  viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

Soporta 0..N archivos por corrida: el manifiesto no declara cupo
previo (``artefactos_produce=()``) y cada PDF se sube con un
``artifact_id`` propio (``rcel_000.pdf``...); los slots sin URL se
resuelven por presign contra la central antes del PUT. Con 0
facturas la corrida es OK con lista vacia, sin subidas.

La sesion de navegador se obtiene de ``runtime.browser_factory`` con
la interfaz minima documentada abajo; el plugin nunca importa
Playwright directo ni desactiva headless/proxy/limpieza. El objeto
``servicio`` expone:

- ``seleccionar_representado(cuit)``: elige el representado.
- ``descargar_facturas(desde, hasta, destino_dir)``: descarga los PDF
  del rango en ``destino_dir`` y retorna la lista de nombres.
"""

from __future__ import annotations

import asyncio
import re
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
from bot_worker.bots.rcel.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "COMPROBANTES EN LÍNEA"
RCEL_BASE_URL_DEFAULT = "https://fe.afip.gob.ar/rcel/jsp/index_bis.jsp"


def nombre_base_rcel(cuit: str, desde: str, hasta: str, nombre: str) -> str:
    """Base de nombre ``'{fin} - RCEL - ...'`` (patron V2)."""
    digitos = re.sub(r"\D", "", cuit or "")
    fin = digitos[-1:] if digitos else "X"
    limpio = re.sub(r"\s+", " ", (nombre or "").strip()) or "SIN_NOMBRE"
    limpio = re.sub(r'[\\/:*?"<>|]+', "", limpio).strip() or "SIN_NOMBRE"
    return f"{fin} - RCEL - {desde} - {hasta} - {digitos} - {limpio}"


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


class RcelPlugin:
    """Comprobantes en Linea de ARCA: descarga de facturas en PDF."""

    manifest = BotManifest(
        nombre="rcel",
        version="3.0.0",
        operaciones=("descargar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(),
        timeout_por_defecto_seconds=1800,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=2,
        idempotency_class="CONTINUACION",
        browser_instances_max=1,
        hosts_permitidos=(
            "fe.afip.gob.ar",
            "www.afip.gob.ar",
        ),
    )

    def __init__(self, base_url: str | None = None) -> None:
        """Inyecta el endpoint del servicio desde el sobre sellado."""
        self._base_url = base_url or RCEL_BASE_URL_DEFAULT

    def __repr__(self) -> str:
        return "RcelPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "RcelPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Sin seccion rige el endpoint RCEL.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("rcel_base_url"):
            self._base_url = str(service["rcel_base_url"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, rango y salidas antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "descargar"))
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
                await servicio.seleccionar_representado(entrada.representado_cuit)
                datos, artefactos, errores = await self._descargar(
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
        resultado = "OK" if not errores else "PARCIAL"
        return BotResult(
            result=resultado, data=datos, artifacts=artefactos, errors=errores
        )

    async def _descargar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[Any]]:
        """Descarga 0..N PDF a ``work_dir`` y sube cada uno por presign."""
        from bot_worker.runtime.context import BotError

        from bot_worker.bots.errors import Categoria

        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Descargando facturas"
        )
        base = nombre_base_rcel(
            entrada.representado_cuit,
            entrada.fecha_desde.replace("/", ""),
            entrada.fecha_hasta.replace("/", ""),
            entrada.representado_nombre,
        )
        destino = runtime.artifact_store.resolve(f"{base}.descargas")
        destino.mkdir(parents=True, exist_ok=True)
        nombres: list[str] = await servicio.descargar_facturas(
            desde=entrada.fecha_desde,
            hasta=entrada.fecha_hasta,
            destino_dir=destino,
        )
        datos: dict[str, Any] = {
            "operacion": "descargar",
            "representado_cuit": entrada.representado_cuit,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
            "cantidad": len(nombres),
            "facturas": [],
        }
        artefactos: list[dict[str, Any]] = []
        errores: list[Any] = []
        for indice, nombre in enumerate(nombres):
            await runtime.cancellation.raise_if_cancelled()
            ruta = runtime.artifact_store.resolve(f"{base}.descargas/{nombre}")
            resumen: dict[str, Any] = {"archivo": nombre}
            if getattr(entrada, "subir_pdf", True):
                await runtime.event_sink.progress(
                    phase="SUBIENDO", percent=80, message=f"Subiendo {nombre}"
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        f"rcel_{indice:03d}.pdf", f"{base}.descargas/{nombre}"
                    )
                except ValueError as exc:
                    errores.append(
                        BotError(
                            category=Categoria.ARTIFACT_UPLOAD_FAILED,
                            internal_diagnostic=f"subida de {nombre}: {exc}",
                            retryable=True,
                        )
                    )
                    continue
                artefactos.append(referencia)
                resumen["sha256"] = referencia["sha256"]
                resumen["size_bytes"] = referencia["size_bytes"]
            else:
                resumen["size_bytes"] = ruta.stat().st_size if ruta.is_file() else 0
            if getattr(entrada, "incluir_json", True):
                datos["facturas"].append(resumen)
        if not getattr(entrada, "incluir_json", True):
            datos.pop("facturas", None)
        return datos, artefactos, errores
