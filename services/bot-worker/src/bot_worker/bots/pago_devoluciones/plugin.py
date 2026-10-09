"""Plugin ``pago_devoluciones``: pagos de devoluciones (ola 3).

Porta ``api-bots-mrbot/app/bot/pago_devoluciones_bot.py``
(``bot_pago_devoluciones``: consulta y exportacion del Excel de pagos,
con errores agrupados por seccion) al contrato S7. Cambios
obligatorios respecto de V1:

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
from datetime import datetime
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
from bot_worker.bots.pago_devoluciones.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "PAGO DEVOLUCIONES"
ID_ARTEFACTO_XLSX = "pagos_xlsx"
INVALID_FILENAME_RE = re.compile(r'[\\/:*?"<>|]+')


def safe_filename(value: str) -> str:
    """Sanitiza el nombre de archivo (port de V1 ``safe_filename``)."""
    limpio = INVALID_FILENAME_RE.sub("_", value or "")
    limpio = re.sub(r"\s+", "_", limpio).strip("_")
    return limpio or "archivo"


def nombre_base_archivo(representado_cuit: str) -> str:
    """Replica el patron de nombres V1 ``'pago_devoluciones_<cuit>_<sello>'``."""
    digitos = re.sub(r"\D", "", representado_cuit or "")
    sello = datetime.now().strftime("%Y%m%d_%H%M%S")
    return safe_filename(f"pago_devoluciones_{digitos}_{sello}")


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


#: Secciones cuyo error invalida la consulta (port de V1 ``_has_critical_errors``).
SECCIONES_CRITICAS = frozenset(
    {"validacion", "proxy", "login", "servicio", "representado", "consulta", "general"}
)


def _append_section_error(
    errores: dict[str, list[str]], seccion: str, mensaje: str
) -> None:
    """Registra un error bajo su sección (port de V1 ``_append_section_error``)."""
    limpio = (mensaje or "").strip() or "Error desconocido"
    errores.setdefault(seccion, []).append(limpio)


def _has_critical_errors(errores: dict[str, list[str]]) -> bool:
    """True si alguna sección crítica tiene errores (port de V1)."""
    return any(errores.get(seccion) for seccion in SECCIONES_CRITICAS)


def _estado_por_secciones(errores: dict[str, list[str]]) -> str:
    """ERROR si hay una sección crítica, PARCIAL si solo hay no críticas, OK si no hay."""
    if _has_critical_errors(errores):
        return "ERROR"
    return "PARCIAL" if any(errores.values()) else "OK"


class PagoDevolucionesPlugin:
    """Pago Devoluciones de ARCA: consulta y Excel de pagos."""

    manifest = BotManifest(
        nombre="pago_devoluciones",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="pagos.xlsx",
                content_types=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet",
                ),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=900,
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
        return "PagoDevolucionesPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "PagoDevolucionesPlugin":
        """Aplica la sección ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia del
        registro compartido. Solo se aceptan claves conocidas; el resto se
        ignora para no inyectar parametros imprevistos al flujo.
        """
        service = service if isinstance(service, dict) else {}
        for clave in ("incluir_json", "subir_archivo"):
            if clave in service:
                self._defectos[clave] = service[clave]
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion y CUIT antes de abrir el navegador."""
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
                    SERVICIO_ARCA, portal="pago_devoluciones"
                )
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
        estado = _estado_por_secciones(datos.get("errores_por_seccion", {}))
        return BotResult(result=estado, data=datos, artifacts=artefactos)

    async def _consultar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime, cuit_objetivo: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Consulta pagos y exporta el Excel a ``work_dir`` (port V2).

        Los errores no fatales se agrupan por seccion en
        ``errores_por_seccion``, como en V2, sin exponer secretos.
        """
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Consultando pagos"
        )
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": cuit_objetivo,
            "archivo": None,
            "errores_por_seccion": {},
        }
        errores: dict[str, list[str]] = datos["errores_por_seccion"]
        artefactos: list[dict[str, Any]] = []
        destino = runtime.artifact_store.resolve(
            f"{nombre_base_archivo(cuit_objetivo)}.xlsx"
        )
        try:
            await servicio.consultar_y_exportar(
                cuit_representado=cuit_objetivo, destino=destino
            )
        except ErrorDeBot as exc:
            # Fallo de exportación: seccion "consulta" (critica, como en V1).
            _append_section_error(errores, "consulta", getattr(exc, "diagnostico", str(exc)))
            raise
        await runtime.event_sink.progress(
            phase="PROCESANDO", percent=65, message="Procesando pagos"
        )
        resumen: dict[str, Any] = {
            "archivo": destino.name,
            "size_bytes": destino.stat().st_size if destino.is_file() else 0,
        }
        if entrada.subir_archivo:
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message="Subiendo Excel"
            )
            try:
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_XLSX, destino.name
                )
            except ValueError as exc:
                # Contrato V3: una subida fallida es ArtifactUploadError
                # reintentable. No se degrada a PARCIAL en silencio.
                raise ArtifactUploadError(str(exc)) from exc
            artefactos.append(referencia)
            resumen["sha256"] = referencia["sha256"]
            resumen["size_bytes"] = referencia["size_bytes"]
        datos["archivo"] = resumen["archivo"] if entrada.incluir_json else None
        datos["detalle_archivo"] = resumen
        return datos, artefactos
