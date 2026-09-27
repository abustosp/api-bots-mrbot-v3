"""Plugin ``consulta_pagos_vep``: pagos VEP con navegador (worker V3).

Porta ``api-bots-mrbot-v2/app/bot/consulta_pagos_vep_bot.py``
(``bot_consulta_pagos_veps``) al contrato worker V3. Cambios
obligatorios respecto de V2:

- Sin ``SessionLocal``: la central persiste tras el callback.
- Sin ``tempfile``/``descargas`` del host: el CSV vive bajo
  ``runtime.work_dir`` via ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: proxy y credenciales
  llegan por ``runtime`` desde el sobre sellado.
- Sin ``_upload_csv_to_minio`` con claves: la subida usa
  ``artifact_store.upload`` con el slot ``pagos_vep_csv``.
- Sin credenciales en logs: categoria + diagnostico redactado.

La seccion ``service`` del sobre sellado (vía :meth:`configure`) solo
ajusta el nombre visible del servicio y la muestra JSON.
"""

from __future__ import annotations

import asyncio
import csv
from pathlib import Path
from typing import Any, Mapping

from bot_worker.bots.consulta_pagos_vep.schema import (
    ENTRADAS,
    esquema_entrada,
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
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "PRESENTACIÓN DE DDJJ Y PAGOS"
ID_ARTEFACTO_CSV = "pagos_vep_csv"


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


class ConsultaPagosVepPlugin:
    """Consulta de pagos VEP y exportacion del CSV por representado."""

    manifest = BotManifest(
        nombre="consulta_pagos_vep",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="pagos_vep.csv",
                content_types=("text/csv",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1200,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=True,
        costo_creditos_sugerido=1,
        idempotency_class="LECTURA",
        browser_instances_max=1,
        hosts_permitidos=(
            "www.afip.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __init__(self, servicio: str | None = None, muestra_json: int = 5) -> None:
        """Valores por defecto; la seccion sellada los ajusta en ``configure``."""
        self._servicio = servicio or SERVICIO_ARCA
        self._muestra_json = muestra_json

    def __repr__(self) -> str:
        return "ConsultaPagosVepPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "ConsultaPagosVepPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Claves conocidas: ``servicio`` (nombre visible del servicio ARCA)
        y ``muestra_json`` (filas de muestra en la respuesta). Sin seccion
        rigen los valores por defecto V2.
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
        """Valida operacion, CUIT y periodo antes del navegador."""
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
                servicio = await sesion.open_service(
                    self._servicio, portal="consulta_pagos_vep"
                )
                await servicio.seleccionar_representado(entrada.representado_cuit)
                datos, artefactos = await self._consultar(
                    servicio, entrada, runtime
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

    async def _consultar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Selecciona periodo, exporta el CSV a ``work_dir`` y sube por slot."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Exportando pagos VEP"
        )
        await runtime.cancellation.raise_if_cancelled()
        destino = runtime.artifact_store.resolve(
            f"pagos_vep_{entrada.representado_cuit}_{entrada.periodo}.csv"
        )
        await servicio.exportar_pagos_csv(
            destino=destino,
            periodo=entrada.periodo,
        )
        await runtime.event_sink.progress(
            phase="PROCESANDO", percent=65, message="Leyendo pagos VEP"
        )
        filas = self._contar_filas(destino)
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": entrada.representado_cuit,
            "periodo": entrada.periodo,
            "filas": filas,
            "archivo": destino.name,
        }
        artefactos: list[dict[str, Any]] = []
        if getattr(entrada, "subir_csv", True):
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
        if getattr(entrada, "incluir_json", True):
            datos["muestra"] = self._muestra(destino)
        return datos, artefactos

    @staticmethod
    def _contar_filas(archivo: Path) -> int:
        """Cuenta filas de datos del CSV exportado (port de V2)."""
        with open(archivo, "r", encoding="utf-8-sig", newline="") as fh:
            lector = csv.DictReader(fh)
            if lector.fieldnames is None:
                raise ValueError("csv sin encabezado")
            return sum(1 for _ in lector)

    def _muestra(self, archivo: Path) -> list[dict[str, str]]:
        """Lee las primeras filas del CSV para el JSON de respuesta."""
        with open(archivo, "r", encoding="utf-8-sig", newline="") as fh:
            lector = csv.DictReader(fh)
            return [dict(fila) for _, fila in zip(range(self._muestra_json), lector)]
