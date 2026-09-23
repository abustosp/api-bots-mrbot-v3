"""Plugin ``hacienda``: comprobantes de hacienda con navegador (worker V3).

Porta ``api-bots-mrbot-v2/app/bot/hacienda_bot.py`` (``hacienda`` /
``descargar_hacienda``) al contrato worker V3. Cambios obligatorios
respecto de V2:

- Sin ``SessionLocal``: la central persiste tras el callback.
- Sin ``download_dir`` del host: los comprobantes y el consolidado
  viven bajo ``runtime.work_dir`` via ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: proxy y credenciales
  llegan por ``runtime`` desde el sobre sellado.
- Sin ``_subir_archivos_a_minio`` con claves: el consolidado se sube
  con ``artifact_store.upload`` (slot ``hacienda_consolidado``).
- Sin pandas: V2 consolida con ``DataFrame``; el worker no garantiza
  esa dependencia, asi que el plugin escribe un CSV consolidado por
  consulta con la biblioteca estandar y la misma granularidad
  (por_emisor / por_receptor).
- Sin credenciales en logs: categoria + diagnostico redactado.

El flujo V2 (login, ``COMPROBANTES EN LINEA``, seleccion de
denominacion, doble apertura de ``Hacienda y Carne - Liquidacion`` y
consulta por emisor/receptor con fechas) se delega al handle del
servicio; el plugin orquesta, consolida y sube.

La seccion ``service`` del sobre sellado (vía :meth:`configure`) solo
ajusta los nombres visibles de los servicios y la muestra JSON.
"""

from __future__ import annotations

import asyncio
import csv
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
from bot_worker.bots.hacienda.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_COMPROBANTES = "COMPROBANTES EN LINEA"
SERVICIO_HACIENDA = "Hacienda y Carne - Liquidacion"
ID_ARTEFACTO_CONSOLIDADO = "hacienda_consolidado"


def nombre_consolidado(
    representado_cuit: str, consulta: str, desde: str, hasta: str
) -> str:
    """Nombre estable del CSV consolidado por consulta (port de V2)."""
    desde_limpio = desde.replace("/", "")
    hasta_limpio = hasta.replace("/", "")
    return f"HACIENDA - {consulta.upper()} - {representado_cuit} - {desde_limpio} - {hasta_limpio}.csv"


def escribir_consolidado(destino: Path, filas: list[dict[str, Any]]) -> int:
    """Escribe el CSV consolidado con union de columnas (funcion pura).

    Retorna la cantidad de filas escritas. Sin red ni secretos.
    """
    columnas: list[str] = []
    for fila in filas:
        for clave in fila:
            if clave not in columnas:
                columnas.append(clave)
    if not columnas:
        columnas = ["aviso"]
    with open(destino, "w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=columnas, extrasaction="ignore")
        escritor.writeheader()
        for fila in filas:
            escritor.writerow({k: fila.get(k, "") for k in columnas})
    return len(filas)


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


class HaciendaPlugin:
    """Descarga comprobantes de hacienda por emisor/receptor y consolida."""

    manifest = BotManifest(
        nombre="hacienda",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="hacienda_consolidado.csv",
                content_types=("text/csv",),
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
            "api.capmonster.cloud",
        ),
    )

    def __init__(
        self,
        servicio_comprobantes: str | None = None,
        servicio_hacienda: str | None = None,
        muestra_json: int = 5,
    ) -> None:
        """Valores por defecto; la seccion sellada los ajusta en ``configure``."""
        self._servicio_comprobantes = servicio_comprobantes or SERVICIO_COMPROBANTES
        self._servicio_hacienda = servicio_hacienda or SERVICIO_HACIENDA
        self._muestra_json = muestra_json

    def __repr__(self) -> str:
        return "HaciendaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "HaciendaPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Claves conocidas: ``servicio_comprobantes``,
        ``servicio_hacienda`` (nombres visibles en ARCA) y
        ``muestra_json``. Sin seccion rigen los valores por defecto V2.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("servicio_comprobantes"):
            self._servicio_comprobantes = str(service["servicio_comprobantes"])
        if service.get("servicio_hacienda"):
            self._servicio_hacienda = str(service["servicio_hacienda"])
        try:
            muestra = int(service.get("muestra_json", self._muestra_json))
        except (TypeError, ValueError):
            muestra = self._muestra_json
        self._muestra_json = max(0, min(20, muestra))
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
                comprobantes = await sesion.open_service(
                    self._servicio_comprobantes
                )
                await comprobantes.seleccionar_denominacion(
                    entrada.denominacion,
                )
                hacienda = await comprobantes.abrir_hacienda(
                    servicio=self._servicio_hacienda,
                    denominacion=entrada.denominacion,
                )
                datos, artefactos = await self._consultar(
                    hacienda, entrada, runtime
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

    async def _consultar(
        self, hacienda: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Consulta por emisor/receptor, consolida CSV y sube por slot."""
        datos: dict[str, Any] = {
            "representado_cuit": entrada.representado_cuit,
            "denominacion": entrada.denominacion,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
        }
        artefactos: list[dict[str, Any]] = []
        consultas = []
        if entrada.por_emisor:
            consultas.append("por_emisor")
        if entrada.por_receptor:
            consultas.append("por_receptor")
        for consulta in consultas:
            await runtime.cancellation.raise_if_cancelled()
            await runtime.event_sink.progress(
                phase="CONSULTA", percent=45, message=f"Consultando {consulta}"
            )
            filas = await hacienda.consultar(
                consulta_key=consulta,
                desde=entrada.fecha_desde,
                hasta=entrada.fecha_hasta,
            )
            if not isinstance(filas, list):
                raise TargetUnavailableError(
                    f"consulta {consulta} devolvio formato inesperado"
                )
            normalizadas = [dict(f) for f in filas if isinstance(f, dict)]
            destino = runtime.artifact_store.resolve(
                nombre_consolidado(
                    entrada.representado_cuit,
                    consulta,
                    entrada.fecha_desde,
                    entrada.fecha_hasta,
                )
            )
            await runtime.event_sink.progress(
                phase="PROCESANDO", percent=65, message=f"Consolidando {consulta}"
            )
            cantidad = escribir_consolidado(destino, normalizadas)
            resumen: dict[str, Any] = {"filas": cantidad, "archivo": destino.name}
            if getattr(entrada, "subir_excel", True):
                await runtime.event_sink.progress(
                    phase="SUBIENDO", percent=80, message=f"Subiendo {consulta}"
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        ID_ARTEFACTO_CONSOLIDADO, destino.name
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                resumen["sha256"] = referencia["sha256"]
                resumen["size_bytes"] = referencia["size_bytes"]
            if getattr(entrada, "incluir_json", True):
                resumen["muestra"] = normalizadas[: self._muestra_json]
            datos[consulta] = resumen
        return datos, artefactos
