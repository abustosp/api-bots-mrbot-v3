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
import re
from typing import Any, Mapping

from bot_worker.bots.ccma.schema import esquema_entrada
from bot_worker.bots.ccma.schema import CcmaConsultarInput
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
MAX_MOVIMIENTOS = 500


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

    async def _extraer_resumen(self, pagina: Any, cuit: str) -> dict[str, str]:
        """Extrae saldos del resumen CCMA (port de ``_extract_ccma_summary``)."""
        return await pagina.evaluate(
            """({ cuit }) => {
                const clean = (v) => (v || "").replace(/\\u00a0/g, " ").trim();
                const cells = Array.from(
                    document.querySelectorAll("td.CeldaTitularResaltado")
                ).map((c) => clean(c.textContent)).filter(Boolean);
                const byXpath = (xp) => {
                    const r = document.evaluate(xp, document, null, 9, null);
                    return clean(r.singleNodeValue ? r.singleNodeValue.textContent : "");
                };
                const xp = (row, col) => `/html/body/table[2]/tbody/tr[2]/td[2]/form/table[1]/tbody/tr[2]/td/table/tbody/tr[2]/td/table/tbody/tr[${row}]/td[${col}]/table/tbody/tr/td[1]`;
                return {
                    celdas: cells.slice(0, 8).join(" | "),
                    deuda_capital: byXpath(xp(2, 2)),
                    deuda_accesorios: byXpath(xp(3, 2)),
                    credito_capital: byXpath(xp(2, 4)),
                    credito_accesorios: byXpath(xp(3, 4)),
                };
            }""",
            {"cuit": cuit},
        )

    async def _extraer_movimientos(self, pagina: Any) -> list[dict[str, str]]:
        """Recorre la tabla de movimientos con paginado (port de V2)."""
        movimientos: list[dict[str, str]] = []
        vistos: set[str] = set()
        while len(movimientos) < MAX_MOVIMIENTOS:
            filas = await pagina.locator(
                "table[width='729'] tbody tr[valign='middle'][align='center']"
            ).all_inner_texts()
            for fila in filas:
                clave = fila.strip()
                if clave and clave not in vistos:
                    vistos.add(clave)
                    celdas = [c.strip() for c in clave.split("\t")]
                    movimientos.append({"fila": " | ".join(celdas)})
            try:
                siguiente = pagina.locator("#mas1")
                if await siguiente.count() == 0:
                    break
                await siguiente.first.click()
                await pagina.wait_for_timeout(800)
            except Exception:
                break
        return movimientos[:MAX_MOVIMIENTOS]

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
                pagina = await sesion.open_service(self._servicio)
                try:  # versiones con listbox + "Elegir CUIT" (port V2)
                    await pagina.get_by_role("listbox").select_option(
                        entrada.representado_cuit, timeout=2_000
                    )
                    await pagina.get_by_role(
                        "button", name=re.compile(r"Elegir CUIT", re.I)
                    ).click()
                except Exception:
                    pass
                await pagina.get_by_role(
                    "cell", name="PERÍODO DESDE (MM/AAAA)", exact=True
                ).click()
                campo = pagina.locator("input[name='perdesde2']")
                await campo.click()
                await campo.fill(entrada.periodo_desde)
                await pagina.get_by_role(
                    "button", name="CALCULO DE DEUDA"
                ).click()
                await pagina.goto(URL_CTACTE)
                await runtime.event_sink.progress(
                    phase="CONSULTA", percent=45, message="Extrayendo saldos CCMA"
                )
                try:
                    await pagina.locator("select[name='rango']").select_option(
                        "48", timeout=3_000
                    )
                except Exception:
                    pass
                resumen = await self._extraer_resumen(
                    pagina, entrada.representado_cuit
                )
                datos: dict[str, Any] = {
                    "operacion": "consultar",
                    "representado_cuit": entrada.representado_cuit,
                    "periodo_desde": entrada.periodo_desde,
                    **resumen,
                }
                if entrada.incluir_movimientos:
                    datos["movimientos"] = await self._extraer_movimientos(pagina)
                artefactos: list[dict[str, Any]] = []
                if entrada.incluir_pdf:
                    nombre = (
                        f"CCMA - {entrada.representado_cuit} - "
                        f"{entrada.periodo_desde.replace('/', '')}.pdf"
                    )
                    destino = runtime.artifact_store.resolve(nombre)
                    await pagina.pdf(path=str(destino))
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
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return BotResult(result="OK", data=datos, artifacts=artefactos)
