"""Plugin ``portal_iva``: libro IVA y DDJJ de Portal IVA (ola 3).

Porta ``api-bots-mrbot-v2/app/bot/portal_iva_bot.py``
(``bot_portal_iva``: ``ejecutar_descarga`` de CSV de ventas/compras y
``ejecutar_carga`` de TXT) al contrato S7. Cambios obligatorios
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
from bot_worker.bots.portal_iva.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "Portal IVA"
ID_ARTEFACTO_VENTAS = "ventas_csv"
ID_ARTEFACTO_COMPRAS = "compras_csv"


def nombre_base_archivo(representado_cuit: str, periodo: str, libro: str) -> str:
    """Replica el patron de nombres V2 ``'PORTAL-IVA - <libro> - ...'``."""
    digitos = re.sub(r"\D", "", representado_cuit or "")
    return f"PORTAL-IVA - {libro} - {periodo} - {digitos}"


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


class PortalIvaPlugin:
    """Portal IVA de ARCA: descarga de libros e importacion de TXT."""

    manifest = BotManifest(
        nombre="portal_iva",
        version="3.0.0",
        operaciones=("descargar", "importar", "gestionar"),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="ventas.csv",
                content_types=("text/csv",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="compras.csv",
                content_types=("text/csv",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1800,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=True,
        costo_creditos_sugerido=3,
        idempotency_class="CARGA",
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
        return "PortalIvaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "PortalIvaPlugin":
        """Aplica la sección ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia del
        registro compartido. Solo se aceptan claves conocidas; el resto se
        ignora para no inyectar parametros imprevistos al flujo.
        """
        service = service if isinstance(service, dict) else {}
        for clave in (
            "descarga_ventas",
            "descarga_compras",
            "incluir_json",
            "subir_csv",
        ):
            if clave in service:
                self._defectos[clave] = service[clave]
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT y periodo antes de abrir el navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "descargar"))
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
                servicio = await sesion.open_service(SERVICIO_ARCA)
                cuit_objetivo = (
                    entrada.representado_cuit or runtime.credentials.cuit_representante
                )
                await servicio.seleccionar_representado(cuit_objetivo)
                await servicio.seleccionar_periodo(entrada.periodo)
                if operacion == "importar":
                    datos, artefactos = await self._importar(
                        servicio, entrada, runtime, cuit_objetivo
                    )
                elif operacion == "gestionar":
                    datos, artefactos = await self._gestionar(
                        servicio, entrada, runtime, cuit_objetivo
                    )
                else:
                    datos, artefactos = await self._descargar(
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
        return BotResult(result="OK", data=datos, artifacts=artefactos)

    async def _descargar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime, cuit_objetivo: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Descarga CSV de ventas/compras a ``work_dir`` (port V2)."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Descargando libros"
        )
        datos: dict[str, Any] = {
            "operacion": "descargar",
            "representado_cuit": cuit_objetivo,
            "periodo": entrada.periodo,
        }
        artefactos: list[dict[str, Any]] = []
        for libro, artifact_id in (
            ("ventas", ID_ARTEFACTO_VENTAS),
            ("compras", ID_ARTEFACTO_COMPRAS),
        ):
            if libro == "ventas" and not entrada.descarga_ventas:
                continue
            if libro == "compras" and not entrada.descarga_compras:
                continue
            await runtime.cancellation.raise_if_cancelled()
            base = nombre_base_archivo(cuit_objetivo, entrada.periodo, libro)
            destino = runtime.artifact_store.resolve(f"{base}.csv")
            await servicio.descargar_libro(libro=libro, destino=destino)
            await runtime.event_sink.progress(
                phase="PROCESANDO", percent=65, message=f"Procesando {libro}"
            )
            resumen: dict[str, Any] = {
                "filas": self._contar_filas(destino),
                "archivo": destino.name,
            }
            if entrada.subir_csv:
                await runtime.event_sink.progress(
                    phase="SUBIENDO", percent=80, message=f"Subiendo {libro}"
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        artifact_id, destino.name
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                resumen["sha256"] = referencia["sha256"]
                resumen["size_bytes"] = referencia["size_bytes"]
            if entrada.incluir_json:
                resumen["muestra"] = self._muestra(destino)
            datos[libro] = resumen
        return datos, artefactos

    async def _importar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime, cuit_objetivo: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Escribe los TXT en ``work_dir`` e importa libro por libro (V2)."""
        await runtime.event_sink.progress(
            phase="CARGA", percent=55, message="Importando libros"
        )
        datos: dict[str, Any] = {
            "operacion": "importar",
            "representado_cuit": cuit_objetivo,
            "periodo": entrada.periodo,
            "importaciones": [],
        }
        for libro, contenido in (
            ("ventas", entrada.ventas_txt),
            ("compras", entrada.compras_txt),
        ):
            if not contenido:
                continue
            await runtime.cancellation.raise_if_cancelled()
            fuente = runtime.artifact_store.resolve(
                f"{nombre_base_archivo(cuit_objetivo, entrada.periodo, libro)}.txt"
            )
            fuente.write_text(str(contenido), encoding="utf-8")
            ok = await servicio.importar_txt(libro=libro, fuente=fuente)
            datos["importaciones"].append({"libro": libro, "ok": bool(ok)})
        return datos, []

    async def _gestionar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime, cuit_objetivo: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Flujo completo V2: primero importa, luego descarga (port)."""
        datos_imp, _ = await self._importar(servicio, entrada, runtime, cuit_objetivo)
        datos_des, artefactos = await self._descargar(
            servicio, entrada, runtime, cuit_objetivo
        )
        datos_des["operacion"] = "gestionar"
        datos_des["importaciones"] = datos_imp["importaciones"]
        return datos_des, artefactos

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
