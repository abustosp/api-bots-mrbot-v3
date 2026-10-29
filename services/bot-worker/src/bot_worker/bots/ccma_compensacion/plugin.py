"""Plugin ``ccma_compensacion``: compensacion de saldos a favor de CCMA.

Porta ``app/bot/ccma_compensacion_bot.py`` (V1/V2, semilla
``bots_dev/ccma_compenscion``) al contrato S7. Reutiliza el flujo del bot
``ccma`` (login, servicio CCMA, seleccion de CUIT, calculo de deuda, resumen
y movimientos) y agrega la reafectacion de pagos con saldo a favor del portal
``ccma_compensacion``.

- Sin base de datos, entorno ni MinIO directo; el proxy llega en
  ``runtime.proxy`` y las credenciales en el sobre sellado.
- ``idempotency_class="EFECTO"``: con ``confirmar=True`` modifica la cuenta
  corriente en ARCA, por eso no se reintenta a ciegas. Con ``False`` devuelve
  la propuesta, cancela el formulario y no modifica la cuenta.
- Errores por categoria con diagnostico redactado (``sin_secretos``).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Mapping

from bot_worker.bots.ccma.movimientos import recorrer_movimientos_con_fallback
from bot_worker.bots.ccma.pdf import build_ccma_pdf_html
from bot_worker.bots.ccma.plugin import (
    MAX_MOVIMIENTOS,
    MAX_PAGINAS,
    URL_CTACTE,
    nombre_pdf_ccma,
)
from bot_worker.bots.ccma.schema import PERIODO_DESDE_DEFAULT
from bot_worker.bots.ccma_compensacion.schema import (
    CcmaCompensarInput,
    esquema_entrada,
)
from bot_worker.bots.errors import (
    BrowserCrashedError,
    CaptchaUnsolvableError,
    CredentialsRejectedError,
    DeadlineExceededError,
    ArtifactUploadError,
    ErrorDeBot,
    InvalidInputError,
    TargetUnavailableError,
    sin_secretos,
)
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "CCMA"
ID_ARTEFACTO_PDF = "ccma_compensacion.pdf"


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnostico seguro."""
    if isinstance(exc, ErrorDeBot):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return TargetUnavailableError(
            "timeout del sitio del organismo",
            diagnostic_code="ccma_compensacion_site_timeout",
        )
    texto = sin_secretos(f"{type(exc).__name__}", secretos)
    nombre = type(exc).__name__.lower()
    if "captcha" in nombre:
        return CaptchaUnsolvableError(f"desafio no resoluble: {texto}")
    if "browser" in nombre or "playwright" in nombre:
        return BrowserCrashedError(f"navegador caido: {texto}")
    return TargetUnavailableError(
        f"falla del organismo: {texto}",
        diagnostic_code="ccma_compensacion_unclassified_exception",
    )


def nombre_pdf_compensacion(cuit_representado: str) -> str:
    """``ccma_compensacion_{cuit}.pdf``; falla fuerte si el nombre base de CCMA cambia."""
    base = nombre_pdf_ccma(cuit_representado)
    prefijo = "ccma_resumen_"
    if not base.startswith(prefijo):
        raise ValueError(f"nombre de PDF de CCMA inesperado: {base}")
    return "ccma_compensacion_" + base[len(prefijo):]


class CcmaCompensacionPlugin:
    """Compensacion de saldos a favor de CCMA (reafectacion de pagos)."""

    manifest = BotManifest(
        nombre="ccma_compensacion",
        version="3.0.0",
        operaciones=("compensar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre=ID_ARTEFACTO_PDF,
                content_types=("application/pdf",),
                max_bytes=10_485_760,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1800,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=3,
        idempotency_class="EFECTO",
        browser_instances_max=1,
        hosts_permitidos=(
            "auth.afip.gob.ar",
            "www.afip.gob.ar",
            "servicios2.afip.gob.ar",
        ),
    )

    def __init__(self, servicio: str | None = None) -> None:
        self._servicio = servicio or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "CcmaCompensacionPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "CcmaCompensacionPlugin":
        """Aplica la seccion ``service`` del sobre (clave ``ccma_servicio``)."""
        service = service if isinstance(service, dict) else {}
        if service.get("ccma_servicio"):
            self._servicio = str(service["ccma_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion y entrada antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "compensar"))
        if operacion != "compensar":
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return ("compensar", CcmaCompensarInput.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Calcula la deuda, extrae el resumen y compensa los saldos a favor."""
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
        avisos: list[str] = []
        artefactos: list[dict[str, Any]] = []
        try:
            async with runtime.browser_factory.arca_session(
                credentials=runtime.credentials,
                proxy=runtime.proxy,
                deadline=runtime.deadline,
                cancellation=runtime.cancellation,
            ) as sesion:
                await sesion.login()
                await runtime.cancellation.raise_if_cancelled()
                pagina = await sesion.open_service(
                    self._servicio, portal="ccma_compensacion"
                )
                await pagina.seleccionar_cuit(entrada.representado_cuit)
                await pagina.calcular_deuda(PERIODO_DESDE_DEFAULT)
                await pagina.ir_a_cuenta_corriente(URL_CTACTE)
                await pagina.seleccionar_rango("48")
                await runtime.event_sink.progress(
                    phase="CONSULTA", percent=30, message="Extrayendo saldos CCMA"
                )
                resumen = await pagina.resumen(
                    entrada.representado_cuit, PERIODO_DESDE_DEFAULT
                )
                datos: dict[str, Any] = {
                    "operacion": "compensar",
                    "representado_cuit": entrada.representado_cuit,
                    "compensar": entrada.compensar,
                    "confirmar": entrada.confirmar,
                    **{k: v for k, v in resumen.items() if k != "periodo_desde"},
                }
                if entrada.incluir_movimientos:
                    recorrido = await recorrer_movimientos_con_fallback(
                        pagina,
                        restante_segundos=runtime.deadline.remaining_seconds,
                        max_paginas=MAX_PAGINAS,
                        max_movimientos=MAX_MOVIMIENTOS,
                    )
                    datos["movimientos"] = recorrido.movimientos
                    datos["movimientos_corte"] = recorrido.corte
                if entrada.incluir_pdf:
                    nombre = nombre_pdf_compensacion(entrada.representado_cuit)
                    destino = runtime.artifact_store.resolve(nombre)
                    html = build_ccma_pdf_html(
                        resumen,
                        datos.get("movimientos") or [],
                        generado=datetime.now(),
                    )
                    await pagina.imprimir_pdf(html, destino)
                    if entrada.subir:
                        await runtime.event_sink.progress(
                            phase="SUBIENDO", percent=45, message="Subiendo PDF"
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
                if entrada.compensar:
                    await runtime.cancellation.raise_if_cancelled()
                    await runtime.event_sink.progress(
                        phase="COMPENSANDO",
                        percent=55,
                        message="Reafectando pagos con saldo a favor",
                    )
                    detalle, avisos = await pagina.compensar_saldos_a_favor(
                        confirmar=entrada.confirmar,
                        sabana_fresca_inicial=not entrada.incluir_movimientos,
                    )
                    datos["compensaciones"] = detalle
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        compensaciones = datos.get("compensaciones") or {}
        con_errores = bool(avisos) or bool(compensaciones.get("errores"))
        warnings = [sin_secretos(aviso, secretos) for aviso in avisos]
        if warnings:
            datos["avisos"] = warnings
        return BotResult(
            result="PARCIAL" if con_errores else "OK",
            data=datos,
            artifacts=artefactos,
            warnings=warnings,
        )
