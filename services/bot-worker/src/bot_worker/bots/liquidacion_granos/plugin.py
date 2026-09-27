"""Plugin ``liquidacion_granos``: liquidacion primaria de granos (ola 3).

Porta ``api-bots-mrbot-v2/app/bot/liquidacion_granos_bot.py``
(``descargar_liquidacion_granos`` / ``liquidacion_granos``: LPG
emitidas/recibidas, LSG emitidas/recibidas y certificados de deposito)
al contrato S7. Cambios obligatorios respecto de V2:

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
from bot_worker.bots.liquidacion_granos.schema import (
    ENTRADAS,
    esquema_entrada,
    normalizar_fecha,
)
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "LIQUIDACIÓN PRIMARIA DE GRANOS"
ID_ARTEFACTO_PLANILLA = "planilla_xlsx"
ID_ARTEFACTO_COMPROBANTE = "comprobante_pdf"

_SECCIONES = (
    "lpg_emitidas",
    "lpg_recibidas",
    "lsg_emitidas",
    "lsg_recibidas",
    "certificados_deposito",
)


def nombre_base_archivo(
    representado_cuit: str, desde: str, hasta: str, seccion: str
) -> str:
    """Replica el patron de nombres V2 ``'<cuit> - <seccion> - ...'``."""
    digitos = re.sub(r"\D", "", representado_cuit or "")
    desde_limpio = normalizar_fecha(desde).replace("/", "")
    hasta_limpio = normalizar_fecha(hasta).replace("/", "")
    etiqueta = seccion.replace("_", " ").upper()
    return f"{digitos} - {etiqueta} - {desde_limpio} - {hasta_limpio}"


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


class LiquidacionGranosPlugin:
    """Liquidacion primaria de granos de ARCA: planillas y comprobantes."""

    manifest = BotManifest(
        nombre="liquidacion_granos",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="planilla.xlsx",
                content_types=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet",
                ),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="comprobante.pdf",
                content_types=("application/pdf",),
                max_bytes=10_485_760,
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
        return "LiquidacionGranosPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "LiquidacionGranosPlugin":
        """Aplica la sección ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia del
        registro compartido. Solo se aceptan claves conocidas; el resto se
        ignora para no inyectar parametros imprevistos al flujo.
        """
        service = service if isinstance(service, dict) else {}
        for clave in (
            "lpg_emitidas",
            "lpg_recibidas",
            "lsg_emitidas",
            "lsg_recibidas",
            "certificados_deposito",
            "subir_archivos",
        ):
            if clave in service:
                self._defectos[clave] = service[clave]
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
                servicio = await sesion.open_service(
                    SERVICIO_ARCA, portal="liquidacion_granos"
                )
                await servicio.seleccionar_representado(entrada.representado_cuit)
                datos, artefactos = await self._consultar(servicio, entrada, runtime)
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
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Descarga planillas y comprobantes seccion por seccion (port V2)."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Descargando liquidaciones"
        )
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": entrada.representado_cuit,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
            "secciones": {},
        }
        artefactos: list[dict[str, Any]] = []
        for seccion in _SECCIONES:
            if not getattr(entrada, seccion):
                continue
            await runtime.cancellation.raise_if_cancelled()
            base = nombre_base_archivo(
                entrada.representado_cuit,
                entrada.fecha_desde,
                entrada.fecha_hasta,
                seccion,
            )
            destino = runtime.artifact_store.resolve(f"{base}.xlsx")
            await servicio.descargar_planilla(
                seccion=seccion,
                destino=destino,
                desde=entrada.fecha_desde,
                hasta=entrada.fecha_hasta,
            )
            await runtime.event_sink.progress(
                phase="PROCESANDO", percent=65, message=f"Procesando {seccion}"
            )
            resumen: dict[str, Any] = {
                "archivo": destino.name,
                "size_bytes": destino.stat().st_size if destino.is_file() else 0,
            }
            if entrada.subir_archivos:
                await runtime.event_sink.progress(
                    phase="SUBIENDO", percent=80, message=f"Subiendo {seccion}"
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        ID_ARTEFACTO_PLANILLA, destino.name
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                resumen["sha256"] = referencia["sha256"]
                resumen["size_bytes"] = referencia["size_bytes"]
            comprobantes = await servicio.descargar_comprobantes(
                seccion=seccion,
                destino_dir=destino.parent,
                desde=entrada.fecha_desde,
                hasta=entrada.fecha_hasta,
            )
            resumen["comprobantes"] = await self._subir_comprobantes(
                comprobantes, entrada, runtime
            )
            referencias = resumen["comprobantes"].pop("referencias")
            datos["secciones"][seccion] = resumen
            artefactos.extend(referencias)
        return datos, artefactos

    async def _subir_comprobantes(
        self, comprobantes: Any, entrada: Any, runtime: BotRuntime
    ) -> dict[str, Any]:
        """Sube los PDF descargados por el servicio y resume el lote."""
        rutas: list[str] = [str(p) for p in (comprobantes or [])]
        resumen: dict[str, Any] = {"cantidad": len(rutas), "archivos": []}
        referencias: list[dict[str, Any]] = []
        if not entrada.subir_archivos:
            resumen["archivos"] = [Path(p).name for p in rutas]
            resumen["referencias"] = referencias
            return resumen
        for ruta in rutas:
            try:
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_COMPROBANTE, Path(ruta).name
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            referencias.append(referencia)
            resumen["archivos"].append(referencia["name"])
        resumen["referencias"] = referencias
        return resumen
