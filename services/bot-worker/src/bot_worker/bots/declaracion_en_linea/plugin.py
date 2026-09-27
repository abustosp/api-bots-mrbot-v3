"""Plugin ``declaracion_en_linea``: DDJJ y VEP con navegador (worker V3).

Porta ``api-bots-mrbot-v2/app/bot/declaracion_en_linea_bot.py``
(``bot_declaracion_en_linea`` / ``declaracion_en_linea_consulta``) al
contrato worker V3. Cambios obligatorios respecto de V2:

- Sin ``SessionLocal``: la central persiste tras el callback.
- Sin ``download_dir`` del host ni ``tempfile`` propio: los PDF de DDJJ
  y VEP viven bajo ``runtime.work_dir`` via ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: proxy y credenciales
  llegan por ``runtime`` desde el sobre sellado.
- Sin subida a MinIO con claves: cada PDF se sube con
  ``artifact_store.upload`` (slots ``ddjj_pdf`` y ``vep_pdf``).
- Sin credenciales en logs: categoria + diagnostico redactado.

La seccion ``service`` del sobre sellado (vía :meth:`configure`) solo
ajusta el nombre visible del servicio.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from bot_worker.bots.declaracion_en_linea.schema import (
    ENTRADAS,
    esquema_entrada,
    generar_rango_periodos,
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

SERVICIO_ARCA = "DECLARACIÓN EN LÍNEA"
ID_ARTEFACTO_DDJJ = "ddjj_pdf"
ID_ARTEFACTO_VEP = "vep_pdf"


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


class DeclaracionEnLineaPlugin:
    """Consulta DDJJ en linea y VEP por rango de periodos AAAAMM."""

    manifest = BotManifest(
        nombre="declaracion_en_linea",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="ddjj.pdf",
                content_types=("application/pdf",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="vep.pdf",
                content_types=("application/pdf",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1800,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=True,
        costo_creditos_sugerido=2,
        idempotency_class="LECTURA",
        browser_instances_max=1,
        hosts_permitidos=(
            "www.afip.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __init__(self, servicio: str | None = None) -> None:
        """Valor por defecto; la seccion sellada lo ajusta en ``configure``."""
        self._servicio = servicio or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "DeclaracionEnLineaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "DeclaracionEnLineaPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Clave conocida: ``servicio`` (nombre visible del servicio ARCA).
        Sin seccion rige el valor por defecto V2.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("servicio"):
            self._servicio = str(service["servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT y rango AAAAMM antes del navegador."""
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
        """Ejecuta la consulta del rango y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotResult

        operacion, entrada = payload
        if runtime.credentials is None:
            raise CredentialsRejectedError("el plugin requiere credenciales fiscales")
        secretos = [runtime.credentials.clave]
        representado = entrada.representado_cuit or runtime.credentials.cuit_representante
        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="LOGIN", percent=10, message="Iniciando sesion fiscal"
        )
        if runtime.deadline.remaining_seconds() <= 0:
            raise DeadlineExceededError("deadline agotado antes de navegar")
        try:
            periodos = generar_rango_periodos(
                entrada.periodo_desde, entrada.periodo_hasta
            )
        except ValueError as exc:
            raise InvalidInputError(f"rango de periodos invalido: {exc}") from exc
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
                    self._servicio, portal="declaracion_en_linea"
                )
                await servicio.seleccionar_representado(representado)
                datos, artefactos = await self._consultar_rango(
                    servicio, entrada, representado, periodos, runtime
                )
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        datos["operacion"] = operacion
        return BotResult(result="OK", data=datos, artifacts=artefactos)

    async def _consultar_rango(
        self,
        servicio: Any,
        entrada: Any,
        representado: str,
        periodos: list[str],
        runtime: BotRuntime,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Descarga DDJJ y VEP por periodo a ``work_dir`` y sube por slot."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Consultando periodos"
        )
        detalle: list[dict[str, Any]] = []
        artefactos: list[dict[str, Any]] = []
        total = len(periodos)
        for indice, periodo in enumerate(periodos):
            await runtime.cancellation.raise_if_cancelled()
            destino_ddjj = runtime.artifact_store.resolve(
                f"ddjj_{representado}_{periodo}.pdf"
            )
            destino_vep = runtime.artifact_store.resolve(
                f"vep_{representado}_{periodo}.pdf"
            )
            resultado = await servicio.consultar_periodo(
                periodo=periodo,
                destino_ddjj=destino_ddjj,
                destino_vep=destino_vep,
            )
            registro: dict[str, Any] = {
                "periodo": periodo,
                "ddjj": destino_ddjj.name,
                "vep": destino_vep.name,
            }
            if isinstance(resultado, dict):
                registro["estado_vep"] = str(resultado.get("estado_vep", ""))
                registro["pagado"] = bool(resultado.get("pagado", False))
            if getattr(entrada, "subir_archivos", True):
                await runtime.event_sink.progress(
                    phase="SUBIENDO",
                    percent=60 + int(20 * (indice + 1) / max(total, 1)),
                    message=f"Subiendo periodo {indice + 1}/{total}",
                )
                hashes: dict[str, str] = {}
                for destino, id_artefacto, campo_hash in (
                    (destino_ddjj, ID_ARTEFACTO_DDJJ, "sha256_ddjj"),
                    (destino_vep, ID_ARTEFACTO_VEP, "sha256_vep"),
                ):
                    if not destino.is_file() or destino.stat().st_size == 0:
                        continue
                    try:
                        referencia = await runtime.artifact_store.upload(
                            id_artefacto, destino.name
                        )
                    except ValueError as exc:
                        raise ArtifactUploadError(str(exc)) from exc
                    artefactos.append(referencia)
                    if referencia.get("sha256"):
                        hashes[campo_hash] = str(referencia["sha256"])
                registro.update(hashes)
            if not destino_vep.is_file() or destino_vep.stat().st_size == 0:
                registro["vep"] = ""
            detalle.append(registro)
        datos: dict[str, Any] = {
            "representado_cuit": representado,
            "periodo_desde": entrada.periodo_desde,
            "periodo_hasta": entrada.periodo_hasta,
            "periodos_consultados": len(detalle),
        }
        if getattr(entrada, "incluir_json", True):
            datos["detalle"] = detalle
        return datos, artefactos
