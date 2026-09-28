"""Plugin ``comprobantes``: Mis Comprobantes de ARCA con navegador.

Porta ``api-bots-mrbot-v2/app/bot/comprobantes_bot.py`` (tres
operaciones: consultar, solicitar e historial) al contrato worker V3.
Cambios obligatorios respecto de V2:

- Sin ``SessionLocal`` ni escritura de ``ConsultaLog``: la central
  persiste el resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni ``descargas/...``: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya construido
  viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin ``cookies_header`` en el resultado: ``solicitar`` devuelve solo
  ids de consulta (continuacion); las cookies nunca salen del worker.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory`` con la
misma interfaz que el piloto ``mis_comprobantes``; el plugin nunca
importa Playwright directo ni desactiva headless/proxy/limpieza.

La seccion ``service`` del sobre sellado (vía :meth:`configure`) solo
ajusta el nombre visible del servicio y la muestra JSON; las
credenciales y el proxy llegan por ``runtime``, nunca por entorno.
"""

from __future__ import annotations

import asyncio
import csv
import io
from pathlib import Path
from typing import Any, Mapping

from bot_worker.bots.comprobantes.schema import (
    ENTRADAS,
    esquema_entrada,
    limpiar_cuit,
    normalizar_fecha,
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
from bot_worker.bots.planillas import (
    COLUMNAS_FECHA,
    leer_texto_planilla,
    materializar_csv_descargado,
)
from bot_worker.bots.planillas import filtrar_csv_por_rango as _filtrar_compartido
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "MIS COMPROBANTES"
ID_ARTEFACTO_EMITIDOS = "emitidos_csv"
ID_ARTEFACTO_RECIBIDOS = "recibidos_csv"


def nombre_base_archivo(
    representado_cuit: str, representado_nombre: str, desde: str, hasta: str, tipo: str
) -> str:
    """Replica el patron de nombres V2 ``'{n} - MCE/MCR - ...'``."""
    cuit = limpiar_cuit(representado_cuit)
    desde_limpio = normalizar_fecha(desde).replace("/", "")
    hasta_limpio = normalizar_fecha(hasta).replace("/", "")
    sigla = "MCE" if tipo == "emitidos" else "MCR"
    return f"{cuit[-1]} - {sigla} - {desde_limpio} - {hasta_limpio} - {cuit} - {representado_nombre}"


def _leer_planilla(ruta: Path) -> str:
    """Texto de una planilla del organismo, tolerando codificaciones locales."""
    return leer_texto_planilla(Path(ruta))


def filtrar_csv_por_rango(
    origen: Path, destino: Path, desde: str, hasta: str
) -> int:
    """Filtra un CSV de comprobantes por rango inclusive (port de V2).

    Delega en :mod:`bot_worker.bots.planillas`, que detecta el delimitador real
    de la planilla y acepta fechas locales o ISO. Un archivo que no sea una
    planilla reconocible se reporta como formato inesperado del portal.
    """
    try:
        return _filtrar_compartido(
            Path(origen),
            Path(destino),
            normalizar_fecha(desde),
            normalizar_fecha(hasta),
            columnas_fecha=COLUMNAS_FECHA,
        )
    except ValueError as exc:
        raise TargetUnavailableError(
            str(exc),
            diagnostic_code="portal_csv_unexpected_format",
        ) from exc


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


class ComprobantesPlugin:
    """Mis Comprobantes de ARCA: consulta, solicitud e historial."""

    manifest = BotManifest(
        nombre="comprobantes",
        version="3.0.0",
        operaciones=("consultar", "solicitar", "historial"),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="emitidos.csv",
                content_types=("text/csv",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="recibidos.csv",
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
        idempotency_class="CONTINUACION",
        browser_instances_max=1,
        hosts_permitidos=(
            "www.afip.gob.ar",
            "portalcf.cloud.afip.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __init__(self, servicio: str | None = None, muestra_json: int = 5) -> None:
        """Valores por defecto; la seccion sellada los ajusta en ``configure``."""
        self._servicio = servicio or SERVICIO_ARCA
        self._muestra_json = muestra_json

    def __repr__(self) -> str:
        return "ComprobantesPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "ComprobantesPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia del
        registro compartido. Claves conocidas: ``servicio`` (nombre visible
        del servicio ARCA) y ``muestra_json`` (filas de muestra en la
        respuesta). Sin seccion rigen los valores por defecto V2.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("servicio"):
            self._servicio = str(service["servicio"])
        try:
            muestra = int(service.get("muestra_json", self._muestra_json))
        except (TypeError, ValueError):
            muestra = self._muestra_json
        self._muestra_json = max(0, min(20, muestra))
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, periodo y flags antes del navegador."""
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
        """Ejecuta la operacion validada y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotError, BotResult

        from bot_worker.bots.errors import Categoria

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
                servicio = await sesion.open_service(self._servicio)
                await servicio.seleccionar_representado(entrada.representado_cuit)
                if operacion == "solicitar":
                    datos = await self._solicitar(servicio, entrada, runtime)
                    artefactos: list[dict[str, Any]] = []
                else:
                    datos, artefactos = await self._descargar(
                        servicio, entrada, runtime, operacion
                    )
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        if not isinstance(datos, dict):
            return BotResult(
                result="ERROR",
                data={},
                errors=[
                    BotError(
                        category=Categoria.INTERNAL,
                        internal_diagnostic="respuesta no es objeto",
                        retryable=False,
                    )
                ],
            )
        return BotResult(result="OK", data=datos, artifacts=artefactos)

    async def _solicitar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> dict[str, Any]:
        """Pide la consulta async y devuelve ids, nunca cookies (port V2)."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Solicitando consulta"
        )
        ids_consulta: dict[str, str] = {}
        for tipo in ("emitidos", "recibidos"):
            if not getattr(entrada, tipo):
                continue
            await runtime.cancellation.raise_if_cancelled()
            id_consulta = await servicio.solicitar_consulta(
                tipo=tipo,
                desde=entrada.fecha_desde,
                hasta=entrada.fecha_hasta,
            )
            ids_consulta[tipo] = str(id_consulta)
        return {
            "operacion": "solicitar",
            "representado_cuit": entrada.representado_cuit,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
            "ids_consulta": ids_consulta,
        }

    async def _descargar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime, operacion: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Descarga CSV a ``work_dir``, filtra por rango y sube por slot."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Descargando comprobantes"
        )
        datos: dict[str, Any] = {
            "operacion": operacion,
            "representado_cuit": entrada.representado_cuit,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
        }
        artefactos: list[dict[str, Any]] = []
        for tipo, id_artefacto in (
            ("emitidos", ID_ARTEFACTO_EMITIDOS),
            ("recibidos", ID_ARTEFACTO_RECIBIDOS),
        ):
            if not getattr(entrada, tipo):
                continue
            await runtime.cancellation.raise_if_cancelled()
            base = nombre_base_archivo(
                entrada.representado_cuit,
                entrada.representado_nombre,
                entrada.fecha_desde,
                entrada.fecha_hasta,
                tipo,
            )
            crudo = runtime.artifact_store.resolve(f"{base}.crudo.csv")
            final = runtime.artifact_store.resolve(f"{base}.csv")
            await servicio.descargar_csv(
                tipo=tipo,
                destino=crudo,
                desde=entrada.fecha_desde,
                hasta=entrada.fecha_hasta,
            )
            await runtime.event_sink.progress(
                phase="PROCESANDO", percent=65, message=f"Filtrando {tipo}"
            )
            # El botón ``CSV`` del portal entrega un ZIP con el CSV adentro en
            # producción; el archivo directo también se acepta.
            materializar_csv_descargado(crudo)
            filas = filtrar_csv_por_rango(
                crudo, final, entrada.fecha_desde, entrada.fecha_hasta
            )
            crudo.unlink(missing_ok=True)
            resumen: dict[str, Any] = {"filas": filas, "archivo": final.name}
            if getattr(entrada, "subir_csv", True):
                await runtime.event_sink.progress(
                    phase="SUBIENDO", percent=80, message=f"Subiendo {tipo}"
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        id_artefacto, final.name
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                resumen["sha256"] = referencia["sha256"]
                resumen["size_bytes"] = referencia["size_bytes"]
            if getattr(entrada, "incluir_json", True):
                resumen["muestra"] = self._muestra(final)
            datos[tipo] = resumen
        return datos, artefactos

    def _muestra(self, archivo: Path) -> list[dict[str, str]]:
        """Lee las primeras filas del CSV final para el JSON de respuesta."""
        with io.StringIO(_leer_planilla(archivo), newline="") as fh:
            lector = csv.DictReader(fh)
            return [dict(fila) for _, fila in zip(range(self._muestra_json), lector)]
