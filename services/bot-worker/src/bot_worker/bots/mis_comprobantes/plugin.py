"""Plugin ``mis_comprobantes``: piloto con navegador (ola 2, plan 07).

Porta ``api-bots-mrbot-v2/app/bot/comprobantes_bot.py`` (1.508 LOC, tres
operaciones: consultar, solicitar e historial) al contrato S7. Cambios
obligatorios respecto de V2:

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
interfaz minima documentada abajo; el plugin nunca importa Playwright
directo ni desactiva headless/proxy/limpieza.
"""

from __future__ import annotations

import asyncio
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
from bot_worker.bots.planillas import (
    TIMEOUT_MC_SEGUNDOS_POR_DEFECTO,
    filtrar_csv_comprobantes,
    leer_muestra_mis_comprobantes,
    materializar_csv_descargado,
)
from bot_worker.bots.mis_comprobantes.schema import (
    ENTRADAS,
    esquema_entrada,
    limpiar_cuit,
    normalizar_fecha,
)
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "MIS COMPROBANTES"
ID_ARTEFACTO_EMITIDOS = "emitidos_csv"
ID_ARTEFACTO_RECIBIDOS = "recibidos_csv"
# Historial de consultas: mismo portal que ``comprobantes`` (port V1 compartido).
PORTAL_HISTORIAL = "comprobantes"


def nombre_base_archivo(
    representado_cuit: str, representado_nombre: str, desde: str, hasta: str, tipo: str
) -> str:
    """Replica el patron de nombres V2 ``'{n} - MCE/MCR - ...'``."""
    cuit = limpiar_cuit(representado_cuit)
    desde_limpio = normalizar_fecha(desde).replace("/", "")
    hasta_limpio = normalizar_fecha(hasta).replace("/", "")
    sigla = "MCE" if tipo == "emitidos" else "MCR"
    return f"{cuit[-1]} - {sigla} - {desde_limpio} - {hasta_limpio} - {cuit} - {representado_nombre}"


def filtrar_csv_por_rango(
    origen: Path, destino: Path, desde: str, hasta: str, tipo: str = "emitidos"
) -> int:
    """Filtra un CSV de comprobantes con el criterio de V1 (port de V1/V2).

    Delega en :mod:`bot_worker.bots.planillas`, que renombra las columnas por
    posición según el tipo, rellena los importes que el portal no informa,
    recorta el rango inclusive y detecta el delimitador y el formato de fecha
    reales de la planilla. Funcion pura, sin red ni secretos.
    """
    return filtrar_csv_comprobantes(
        Path(origen),
        Path(destino),
        normalizar_fecha(desde),
        normalizar_fecha(hasta),
        tipo,
    )



def timeout_mc_ms(entrada: Any) -> int:
    """Espera del boton ``CSV`` en milisegundos (V1: ``timeout_mc`` segundos)."""
    segundos = getattr(entrada, "timeout_mc", None) or TIMEOUT_MC_SEGUNDOS_POR_DEFECTO
    return int(segundos) * 1000


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnostico seguro."""
    if isinstance(exc, ErrorDeBot):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return TargetUnavailableError(
            "timeout del sitio del organismo",
            diagnostic_code="mis_comprobantes_site_timeout",
        )
    texto = sin_secretos(f"{type(exc).__name__}", secretos)
    if "captcha" in type(exc).__name__.lower():
        return CaptchaUnsolvableError(f"desafio no resoluble: {texto}")
    if "browser" in type(exc).__name__.lower() or "playwright" in type(exc).__name__.lower():
        return BrowserCrashedError(f"navegador caido: {texto}")
    return TargetUnavailableError(
        f"falla del organismo: {texto}",
        diagnostic_code="mis_comprobantes_unclassified_exception",
    )


class MisComprobantesPlugin:
    """Mis Comprobantes de ARCA: consulta, solicitud e historial."""

    manifest = BotManifest(
        nombre="mis_comprobantes",
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
            "auth.afip.gob.ar",
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
        return "MisComprobantesPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "MisComprobantesPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia del
        registro compartido. Claves conocidas: ``servicio`` (nombre visible
        del servicio ARCA) y ``muestra_json`` (filas de muestra en la
        respuesta). Sin seccion rigen los valores por defecto V1.
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
                portal = PORTAL_HISTORIAL if operacion == "historial" else None
                servicio = await sesion.open_service(self._servicio, portal=portal)
                await servicio.seleccionar_representado(entrada.representado_cuit)
                if operacion == "solicitar":
                    datos = await self._solicitar(servicio, entrada, runtime)
                    artefactos: list[dict[str, Any]] = []
                elif operacion == "historial":
                    datos, artefactos = await self._historial(servicio, entrada, runtime)
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
        if operacion == "historial":
            return self._resultado_historial(entrada, datos, artefactos)
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
                puntos_venta=entrada.puntos_venta(tipo),
                timeout_ms=timeout_mc_ms(entrada),
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
                puntos_venta=entrada.puntos_venta(tipo),
                timeout_ms=timeout_mc_ms(entrada),
            )
            datos[tipo] = await self._publicar_csv(
                crudo, final, tipo, id_artefacto, entrada, runtime, artefactos
            )
        return datos, artefactos

    async def _historial(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Descarga la consulta YA existente del rango (port V1). Sin consultas nuevas."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Leyendo historial de consultas"
        )
        datos: dict[str, Any] = {
            "operacion": "historial",
            "representado_cuit": entrada.representado_cuit,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
            "consultas": {},
            "faltantes": [],
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
            resultado = await servicio.historial(
                tipo=tipo,
                desde=entrada.fecha_desde,
                hasta=entrada.fecha_hasta,
                destino=crudo,
                timeout_ms=timeout_mc_ms(entrada),
            )
            datos["consultas"][tipo] = resultado.get("consultas", [])
            if not resultado.get("descargado"):
                datos["faltantes"].append(tipo)
                continue
            datos[tipo] = await self._publicar_csv(
                crudo, final, tipo, id_artefacto, entrada, runtime, artefactos
            )
        return datos, artefactos

    async def _publicar_csv(
        self,
        crudo: Path,
        final: Path,
        tipo: str,
        id_artefacto: str,
        entrada: Any,
        runtime: BotRuntime,
        artefactos: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Materializa, filtra por rango, sube por slot y resume ``tipo``."""
        resumen: dict[str, Any] = {}
        materializar_csv_descargado(crudo)
        await runtime.event_sink.progress(
            phase="PROCESANDO", percent=65, message=f"Filtrando {tipo}"
        )
        filas = filtrar_csv_por_rango(
            crudo, final, entrada.fecha_desde, entrada.fecha_hasta, tipo
        )
        crudo.unlink(missing_ok=True)
        resumen["filas"] = filas
        resumen["archivo"] = final.name
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
            resumen["muestra"] = self._muestra(final, tipo, entrada)
        return resumen

    @staticmethod
    def _resultado_historial(
        entrada: Any, datos: dict[str, Any], artefactos: list[dict[str, Any]]
    ) -> BotResult:
        """OK si estan todas las consultas pedidas; PARCIAL o ERROR si faltan."""
        from bot_worker.bots.errors import Categoria
        from bot_worker.runtime.context import BotError, BotResult

        pedidos = [t for t in ("emitidos", "recibidos") if getattr(entrada, t)]
        faltantes = list(datos.get("faltantes", []))
        if faltantes and len(faltantes) == len(pedidos):
            return BotResult(
                result="ERROR",
                data=datos,
                artifacts=artefactos,
                errors=[
                    BotError(
                        category=Categoria.TARGET_UNAVAILABLE,
                        internal_diagnostic="comprobantes_historial_sin_consulta",
                        retryable=False,
                    )
                ],
            )
        if faltantes:
            return BotResult(
                result="PARCIAL",
                data=datos,
                artifacts=artefactos,
                warnings=["sin consulta en el historial para: " + ", ".join(faltantes)],
            )
        return BotResult(result="OK", data=datos, artifacts=artefactos)

    def _muestra(
        self, archivo: Path, tipo: str = "emitidos", entrada: Any = None
    ) -> list[dict[str, str]]:
        """Filas del CSV final con las columnas auxiliares de V1."""
        cuit = getattr(entrada, "representado_cuit", "")
        return leer_muestra_mis_comprobantes(
            archivo, tipo, cuit, self._muestra_json
        )
