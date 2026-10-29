"""Plugin ``ccma``: cuenta corriente de monotributistas/autonomos.

Porta ``api-bots-mrbot-v2/app/bot/ccma_bot.py`` (login fiscal, servicio
CCMA, seleccion de CUIT, periodo desde + CALCULO DE DEUDA, resumen de
saldos, movimientos paginados, PDF) al contrato S7. Cambios
obligatorios respecto de V2:

- Sin ``SessionLocal`` ni base de datos (W-1).
- Sin ``os.getcwd()`` ni temporales sueltos: el PDF vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya construido
  viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin HTML crudo en el resultado: el resumen vuelve como JSON
  tipado (deuda/credito capital+accesorios); los movimientos como
  lista acotada.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory`` con
``arca_session``; el plugin nunca importa Playwright directo ni
desactiva headless/proxy/limpieza.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Mapping

from bot_worker.bots.ccma.schema import esquema_entrada
from bot_worker.bots.ccma.schema import CcmaConsultarInput
from bot_worker.bots.ccma.movimientos import (
    CORTE_DEADLINE,
    CORTE_ESTADO_REPETIDO,
    CORTE_FALLBACK,
    CORTE_SIN_PROGRESO,
    CORTE_TOPE_MOVIMIENTOS,
    CORTE_TOPE_PAGINAS,
    recorrer_movimientos_con_fallback,
)
from bot_worker.bots.ccma.pdf import build_ccma_pdf_html
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
from bot_worker.bots.registry import ArtifactSpec, BotManifest
from mrbot_contracts.datos_csv import (
    nombre_ccma_movimientos,
    nombre_ccma_resumen,
    nombre_ccma_saldos,
    separar_ccma,
)

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "CCMA"
URL_CTACTE = (
    "https://servicios2.afip.gob.ar/tramites_con_clave_fiscal/ccam/P04_ctacte.asp"
)
ID_ARTEFACTO_PDF = "ccma_resumen.pdf"
# Saldos y movimientos se guardan siempre como CSV (con ``|``) y no en el JSON de
# la base: la central vuelve a armar el response al pedirlo.
ID_ARTEFACTO_SALDOS = "ccma_saldos.csv"
ID_ARTEFACTO_MOVIMIENTOS = "ccma_movimientos.csv"
MAX_MOVIMIENTOS = 500
MAX_PAGINAS = 100


def nombre_pdf_ccma(cuit_representado: str, ahora: datetime | None = None) -> str:
    """Nombre del PDF consolidado: ``ccma_resumen_{cuit representado}.pdf``."""
    return nombre_ccma_resumen(cuit_representado)


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnostico seguro."""
    if isinstance(exc, ErrorDeBot):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return TargetUnavailableError(
            "timeout del sitio del organismo",
            diagnostic_code="ccma_site_timeout",
        )
    texto = sin_secretos(f"{type(exc).__name__}", secretos)
    if "captcha" in type(exc).__name__.lower():
        return CaptchaUnsolvableError(f"desafio no resoluble: {texto}")
    if "browser" in type(exc).__name__.lower() or "playwright" in type(exc).__name__.lower():
        return BrowserCrashedError(f"navegador caido: {texto}")
    return TargetUnavailableError(
        f"falla del organismo: {texto}",
        diagnostic_code="ccma_unclassified_exception",
    )


class CcmaPlugin:
    """Cuenta corriente CCMA: saldos, movimientos y PDF."""

    manifest = BotManifest(
        nombre="ccma",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="ccma_resumen.pdf",
                content_types=("application/pdf",),
                max_bytes=10_485_760,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="ccma_saldos.csv",
                content_types=("text/csv",),
                max_bytes=10_485_760,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="ccma_movimientos.csv",
                content_types=("text/csv",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=900,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=2,
        idempotency_class="LECTURA",
        browser_instances_max=1,
        hosts_permitidos=(
            "auth.afip.gob.ar",
            "www.afip.gob.ar",
            "servicios2.afip.gob.ar",
        ),
    )

    def __init__(self, servicio: str | None = None) -> None:
        """Inyecta el nombre del servicio desde el sobre sellado."""
        self._servicio = servicio or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "CcmaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "CcmaPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Clave: ``ccma_servicio``.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("ccma_servicio"):
            self._servicio = str(service["ccma_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, periodo y salidas antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "consultar"))
        if operacion != "consultar":
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return ("consultar", CcmaConsultarInput.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def _extraer_movimientos(self, pagina: Any, runtime: BotRuntime) -> tuple[list[dict[str, str]], str]:
        """Recorre la cuenta corriente con el fallback y los cortes de V1.

        ``pagina`` es el portal CCMA (``open_service(..., portal="ccma")``). Devuelve
        ``(movimientos, motivo_de_corte)``. Un corte no es un error: el resultado
        queda OK con lo reunido hasta ese punto y el motivo viaja en ``data``.
        """
        resultado = await recorrer_movimientos_con_fallback(
            pagina,
            restante_segundos=runtime.deadline.remaining_seconds,
            max_paginas=MAX_PAGINAS,
            max_movimientos=MAX_MOVIMIENTOS,
        )
        return resultado.movimientos, resultado.corte

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Consulta saldos CCMA y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotResult

        _, entrada = payload
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
                pagina = await sesion.open_service(self._servicio, portal="ccma")
                await pagina.seleccionar_cuit(entrada.representado_cuit)
                await pagina.calcular_deuda(entrada.periodo_desde)
                await pagina.ir_a_cuenta_corriente(URL_CTACTE)
                await runtime.event_sink.progress(
                    phase="CONSULTA", percent=45, message="Extrayendo saldos CCMA"
                )
                await pagina.seleccionar_rango("48")
                resumen = await pagina.resumen(
                    entrada.representado_cuit, entrada.periodo_desde
                )
                datos: dict[str, Any] = {
                    "operacion": "consultar",
                    "representado_cuit": entrada.representado_cuit,
                    "periodo_desde": entrada.periodo_desde,
                    **{k: v for k, v in resumen.items() if k != "periodo_desde"},
                }
                movimientos: list[dict[str, str]] = []
                if entrada.incluir_movimientos:
                    movimientos, corte = await self._extraer_movimientos(pagina, runtime)
                    datos["movimientos"] = movimientos
                    datos["movimientos_corte"] = corte
                    if corte in (
                        CORTE_SIN_PROGRESO,
                        CORTE_ESTADO_REPETIDO,
                        CORTE_DEADLINE,
                        CORTE_FALLBACK,
                        CORTE_TOPE_MOVIMIENTOS,
                        CORTE_TOPE_PAGINAS,
                    ):
                        datos["movimientos_completos"] = False
                artefactos: list[dict[str, Any]] = []
                if entrada.incluir_pdf:
                    nombre = nombre_pdf_ccma(entrada.representado_cuit)
                    destino = runtime.artifact_store.resolve(nombre)
                    html = build_ccma_pdf_html(
                        resumen, movimientos, generado=datetime.now()
                    )
                    await pagina.imprimir_pdf(html, destino)
                    if entrada.subir:
                        await runtime.event_sink.progress(
                            phase="SUBIENDO", percent=80, message="Subiendo PDF"
                        )
                        try:
                            referencia = await runtime.artifact_store.upload(
                                ID_ARTEFACTO_PDF, destino.name
                            )
                        except ValueError as exc:
                            raise ArtifactUploadError(str(exc)) from exc
                        artefactos.append(referencia)
                        datos["sha256"] = referencia["sha256"]
                        datos["size_bytes"] = referencia["size_bytes"]
                    else:
                        datos["archivo"] = destino.name
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        # El dato scrapeado sale del JSON y se guarda como CSV en S4.
        datos = await self._guardar_csv(datos, entrada, runtime, artefactos)
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return BotResult(result="OK", data=datos, artifacts=artefactos)

    async def _guardar_csv(
        self,
        datos: dict[str, Any],
        entrada: Any,
        runtime: BotRuntime,
        artefactos: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Sube ``CCMA_saldos_*.csv`` y ``CCMA_movimientos_*.csv``; devuelve el payload reducido."""
        reducido, archivos = separar_ccma(datos)
        nombres = {
            "saldos": (ID_ARTEFACTO_SALDOS, nombre_ccma_saldos),
            "movimientos": (ID_ARTEFACTO_MOVIMIENTOS, nombre_ccma_movimientos),
        }
        for clave, texto in archivos.items():
            id_artefacto, armar_nombre = nombres[clave]
            nombre = armar_nombre(entrada.representado_cuit)
            destino = runtime.artifact_store.resolve(nombre)
            destino.write_text(texto, encoding="utf-8", newline="")
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=85, message=f"Subiendo {clave}"
            )
            try:
                referencia = await runtime.artifact_store.upload(id_artefacto, destino.name)
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            artefactos.append(referencia)
        return reducido
