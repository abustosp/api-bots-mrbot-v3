"""Plugin ``mis_retenciones_iva_simple``: IVA Simple (ola 3).

Porta ``api-bots-mrbot-v2/app/bot/mis_retenciones_iva_simple_bot.py``
(``bot_mis_retenciones_iva_simple``: consulta de retenciones en modo
IVA Simple con exportacion CSV) al contrato S7. Cambios obligatorios
respecto de V2:

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
import csv
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
from bot_worker.bots.mis_retenciones_iva_simple.schema import (
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

SERVICIO_ARCA = "MIS RETENCIONES"
ID_ARTEFACTO_CSV = "retenciones_csv"


def nombre_base_archivo(
    representado_cuit: str, desde: str, hasta: str, denominacion: str
) -> str:
    """Replica el patron de nombres V2 ``'<fin> - <cuit> - ...'``."""
    digitos = re.sub(r"\D", "", representado_cuit or "")
    fin = digitos[-1:] if digitos else ""
    partes = [
        fin,
        digitos,
        normalizar_fecha(desde).replace("/", ""),
        normalizar_fecha(hasta).replace("/", ""),
        "IVA-SIMPLE",
        re.sub(r'[\\\\/:*?"<>|]', "_", (denominacion or "").strip()),
    ]
    return " - ".join(p for p in partes if p)


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


class MisRetencionesIvaSimplePlugin:
    """Mis Retenciones (modo IVA Simple) de ARCA: consulta y CSV."""

    manifest = BotManifest(
        nombre="mis_retenciones_iva_simple",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="retenciones.csv",
                content_types=("text/csv",),
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
            "www.afip.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __init__(self, defectos: dict[str, Any] | None = None) -> None:
        """Valores por defecto que la seccion ``service`` puede presetear."""
        self._defectos: dict[str, Any] = dict(defectos or {})

    def __repr__(self) -> str:
        return "MisRetencionesIvaSimplePlugin(<redacted>)"

    def configure(
        self, service: dict[str, Any] | None
    ) -> "MisRetencionesIvaSimplePlugin":
        """Aplica la sección ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia del
        registro compartido. Solo se aceptan claves conocidas; el resto se
        ignora para no inyectar parametros imprevistos al flujo.
        """
        service = service if isinstance(service, dict) else {}
        for clave in ("incluir_json", "subir_csv"):
            if clave in service:
                self._defectos[clave] = service[clave]
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT y rango IVA Simple antes del navegador."""
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
                    SERVICIO_ARCA, portal="mis_retenciones_iva_simple"
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
        """Activa el modo IVA Simple y descarga el CSV a ``work_dir`` (V2)."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Consultando IVA Simple"
        )
        await servicio.activar_modo_iva_simple()
        await runtime.cancellation.raise_if_cancelled()
        base = nombre_base_archivo(
            entrada.representado_cuit,
            entrada.fecha_desde,
            entrada.fecha_hasta,
            entrada.representado_nombre,
        )
        destino = runtime.artifact_store.resolve(f"{base}.csv")
        await servicio.descargar_csv(
            destino=destino,
            desde=entrada.fecha_desde,
            hasta=entrada.fecha_hasta,
        )
        await runtime.event_sink.progress(
            phase="PROCESANDO", percent=65, message="Procesando retenciones"
        )
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": entrada.representado_cuit,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
            "filas": self._contar_filas(destino),
            "archivo": destino.name,
        }
        artefactos: list[dict[str, Any]] = []
        if entrada.subir_csv:
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message="Subiendo CSV"
            )
            try:
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_CSV, destino.name
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            artefactos.append(referencia)
            datos["sha256"] = referencia["sha256"]
            datos["size_bytes"] = referencia["size_bytes"]
        if entrada.incluir_json:
            datos["muestra"] = self._muestra(destino)
        return datos, artefactos

    @staticmethod
    def _contar_filas(archivo: Path) -> int:
        """Cuenta filas de datos del CSV sin cargarlo completo en memoria."""
        try:
            with open(archivo, "r", encoding="utf-8-sig", newline="") as fh:
                return max(0, sum(1 for _ in fh) - 1)
        except OSError:
            return 0

    @staticmethod
    def _muestra(archivo: Path, limite: int = 5) -> list[dict[str, str]]:
        """Lee las primeras filas del CSV final para el JSON de respuesta."""
        try:
            with open(archivo, "r", encoding="utf-8-sig", newline="") as fh:
                lector = csv.DictReader(fh)
                return [dict(fila) for _, fila in zip(range(limite), lector)]
        except OSError:
            return []
