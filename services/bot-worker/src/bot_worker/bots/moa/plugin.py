"""Plugin ``moa``: despachos del arbol MOA (ola 3).

Porta ``api-bots-mrbot-v2/app/bot/moa_bot.py`` (``moa_bot``: apertura
del popup "MOA – REINGENIERIA", navegacion del arbol de empresa y
scrapeo de despachos con exportacion CSV) al contrato S7. Cambios
obligatorios respecto de V2:

- Sin ``SessionLocal`` ni escritura de ``ConsultaLog``: la central
  persiste el resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni archivos temporales sueltos: todo archivo vive
  bajo ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya construido
  viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
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
from datetime import datetime, timezone
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
from bot_worker.bots.moa.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "MOA – REINGENIERIA"
ID_ARTEFACTO_CSV = "despachos_csv"


def nombre_base_archivo(representado_cuit: str) -> str:
    """Nombre estable del CSV de despachos en ``work_dir``."""
    digitos = re.sub(r"\D", "", representado_cuit or "")
    return f"MOA - {digitos}"


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


def _registro_despacho_con_error(
    despacho: str, metodo: str, exc: ErrorDeBot
) -> dict[str, Any]:
    """Registro de un despacho fallido con la misma forma que uno exitoso.

    V1 guarda el dict completo tambien para los fallidos (``error`` con el
    texto y carátula vacia), asi el JSON y el CSV tienen siempre las mismas
    claves. ``timestamp`` se completa igual que en un exitoso.
    """
    return {
        "despacho": despacho,
        "metodo": metodo,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "datos_caratula": {},
        "estado_presentacion": {},
        "error": str(getattr(exc, "diagnostico", "") or type(exc).__name__),
    }


def _fila_despacho(registro: dict[str, Any]) -> dict[str, Any]:
    """Aplana un registro de despacho en columnas, como V1 ``_escribir_csv``.

    ``datos_caratula`` pasa a ``caratula_<campo>`` y ``estado_presentacion``
    a ``estado_<campo>``. ``despacho``, ``metodo``, ``timestamp`` y ``error``
    quedan siempre presentes (``error`` vacio si el despacho salio bien).
    """
    fila: dict[str, Any] = {
        "despacho": registro.get("despacho", ""),
        "metodo": registro.get("metodo", ""),
        "timestamp": registro.get("timestamp", "") or "",
        "error": registro.get("error") or "",
    }
    for clave, valor in dict(registro.get("datos_caratula") or {}).items():
        fila[f"caratula_{clave}"] = valor
    for clave, valor in dict(registro.get("estado_presentacion") or {}).items():
        fila[f"estado_{clave}"] = valor
    return fila


class MoaPlugin:
    """MOA de ARCA: scrapeo de despachos del arbol de empresa."""

    manifest = BotManifest(
        nombre="moa",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="despachos.csv",
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
        return "MoaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "MoaPlugin":
        """Aplica la sección ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia del
        registro compartido. Solo se aceptan claves conocidas; el resto se
        ignora para no inyectar parametros imprevistos al flujo.
        """
        service = service if isinstance(service, dict) else {}
        for clave in ("tipo_agente", "rol", "metodo", "incluir_json", "subir_csv"):
            if clave in service:
                self._defectos[clave] = service[clave]
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT y despachos antes de abrir el navegador."""
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
                servicio = await sesion.open_service(SERVICIO_ARCA, portal="moa")
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
        """Navega el arbol y scrapea despacho por despacho (port V2)."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Abriendo arbol MOA"
        )
        await servicio.navegar_arbol_empresa(
            cuit_representado=entrada.representado_cuit,
            tipo_agente=entrada.tipo_agente,
            rol=entrada.rol,
        )
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "ejecucion": datetime.now(timezone.utc).isoformat(),
            "metodo": entrada.metodo,
            "representado_cuit": entrada.representado_cuit,
            "despachos_procesados": 0,
            "despachos_exitosos": 0,
            "despachos_con_error": 0,
            "datos": [],
            "errores": [],
        }
        filas_csv: list[dict[str, Any]] = []
        artefactos: list[dict[str, Any]] = []
        total = len(entrada.despachos)
        for indice, despacho in enumerate(entrada.despachos):
            await runtime.cancellation.raise_if_cancelled()
            await runtime.event_sink.progress(
                phase="CONSULTA",
                percent=45 + int(20 * (indice / max(total, 1))),
                message=f"Scrapeando despacho {indice + 1}/{total}",
            )
            try:
                registro = await servicio.scrapear_despacho(
                    despacho=despacho, metodo=entrada.metodo
                )
            except ErrorDeBot as exc:
                datos["despachos_procesados"] += 1
                datos["despachos_con_error"] += 1
                datos["errores"].append(
                    {
                        "despacho": despacho,
                        "diagnostico": exc.diagnostico,
                        "diagnostic_code": getattr(exc, "diagnostic_code", None),
                    }
                )
                registro_error = _registro_despacho_con_error(despacho, entrada.metodo, exc)
                datos["datos"].append(registro_error)
                filas_csv.append(_fila_despacho(registro_error))
                continue
            datos["despachos_procesados"] += 1
            datos["despachos_exitosos"] += 1
            registro_ok = {"despacho": despacho, "metodo": entrada.metodo, **dict(registro or {})}
            registro_ok.setdefault("error", None)
            datos["datos"].append(registro_ok)
            filas_csv.append(_fila_despacho(registro_ok))
        destino = runtime.artifact_store.resolve(
            f"{nombre_base_archivo(entrada.representado_cuit)}.csv"
        )
        self._escribir_csv(destino, filas_csv)
        resumen_csv: dict[str, Any] = {
            "filas": len(filas_csv),
            "archivo": destino.name,
        }
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
            resumen_csv["sha256"] = referencia["sha256"]
            resumen_csv["size_bytes"] = referencia["size_bytes"]
        datos["csv"] = resumen_csv
        if not entrada.incluir_json:
            datos["datos"] = []
        return datos, artefactos

    @staticmethod
    def _escribir_csv(destino: Path, filas: list[dict[str, Any]]) -> None:
        """Escribe una fila por despacho con columnas aplanadas (port V1).

        V1 aplana ``caratula_<campo>`` y ``estado_<campo>``, y siempre incluye
        ``despacho``, ``metodo``, ``timestamp`` y ``error`` (vacio si no hubo
        error). Asi cada campo queda en su propia columna, nunca como repr.
        """
        campos: set[str] = {"despacho", "metodo", "timestamp", "error"}
        for fila in filas:
            campos.update(fila.keys())
        with open(destino, "w", encoding="utf-8", newline="") as fh:
            escritor = csv.DictWriter(fh, fieldnames=sorted(campos))
            escritor.writeheader()
            escritor.writerows(filas)
