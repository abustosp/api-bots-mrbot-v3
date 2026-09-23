"""Plugin ``vep_archivo``: genera VEP desde archivo .txt en ARCA.

Porta ``api-bots-mrbot-v2/app/bot/vep_archivo_bot.py``
(``generar_vep_desde_archivo``) al contrato S7. Cambios obligatorios
respecto de V2:

- Sin ``SessionLocal`` ni escritura en base: la central persiste el
  resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni temporales sueltos: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya
  construido viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin pandas en el worker: la validacion del .txt es stdlib
  (cabecera ``01`` + registros ``02``, hasta 600 por lote).
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

Generar un VEP es efecto fiscal: el manifiesto declara
``idempotency_class="EFECTO"``.

La sesion de navegador se obtiene de ``runtime.browser_factory``
con la interfaz minima documentada abajo; el plugin nunca importa
Playwright directo. El objeto ``servicio`` expone:

- ``generar_vep_desde_archivo(archivo, medio_pago)``: sube el .txt
  y genera el VEP, retornando ``{"lote": str, "registros": int}``.
- ``descargar_detalle(destino)``: guarda el PDF del detalle.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any, Mapping

from bot_worker.bots.errors import (
    ArtifactUploadError,
    BrowserCrashedError,
    CredentialsRejectedError,
    DeadlineExceededError,
    ErrorDeBot,
    InvalidInputError,
    TargetUnavailableError,
    sin_secretos,
)
from bot_worker.bots.vep_archivo.schema import (
    ENTRADAS,
    MAX_REGISTROS_POR_LOTE,
    esquema_entrada,
)
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "PRESENTACIÓN DE DDJJ Y PAGOS"
ID_ARTEFACTO_DETALLE = "vep_archivo_detalle_pdf"


def generar_registro(
    nro_form: str,
    cod_tipo_pago: str,
    contribuyente_cuit: str,
    concepto: str,
    sub_concepto: str,
    periodo_fiscal: str,
    obligacion_impuesto: str,
    importe: float,
) -> str:
    """Construye una linea ``02`` del archivo VEP (port de V2)."""
    return (
        f'02<VEP nroFormulario="{nro_form}" codTipoPago="{cod_tipo_pago}" '
        f'contribuyenteCUIT="{contribuyente_cuit}" concepto="{concepto}" '
        f'subConcepto="{sub_concepto}" periodoFiscal="{periodo_fiscal}" '
        f'importe="{importe}"> <Obligacion impuesto="{obligacion_impuesto}" '
        f'importe="{importe}"/></VEP>'
    )


def generar_cabecera(
    cuit: str,
    cantidad_de_registros: int,
    version: str = "0100",
    nro_formulario: str = "20001",
    impuesto: str = "003",
    concepto: str = "003",
) -> str:
    """Construye la linea ``01`` de cabecera del archivo VEP (port de V2)."""
    return (
        "01"
        + cuit.zfill(11)
        + nro_formulario.zfill(5)
        + version.zfill(5)
        + impuesto.zfill(3)
        + concepto.zfill(3)
        + str(cantidad_de_registros).zfill(4)
    )


def validar_archivo_vep(texto: str) -> int:
    """Valida cabecera ``01`` + registros ``02`` y retorna su cantidad.

    Funcion pura, sin red ni secretos. Lanza ``ValueError`` si la
    estructura es invalida o supera el lote maximo de V2.
    """
    lineas = [ln for ln in texto.splitlines() if ln.strip()]
    if not lineas or not lineas[0].startswith("01"):
        raise ValueError("el archivo debe iniciar con cabecera 01")
    registros = [ln for ln in lineas[1:] if ln.startswith("02")]
    if len(registros) != len(lineas) - 1:
        raise ValueError("todas las lineas tras la cabecera deben ser 02")
    if not registros:
        raise ValueError("el archivo no trae registros 02")
    if len(registros) > MAX_REGISTROS_POR_LOTE:
        raise ValueError(
            f"el archivo excede {MAX_REGISTROS_POR_LOTE} registros por lote"
        )
    return len(registros)


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnostico seguro."""
    if isinstance(exc, ErrorDeBot):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return TargetUnavailableError("timeout del sitio del organismo")
    texto = sin_secretos(f"{type(exc).__name__}", secretos)
    nombre = type(exc).__name__.lower()
    if "browser" in nombre or "playwright" in nombre:
        return BrowserCrashedError(f"navegador caido: {texto}")
    return TargetUnavailableError(f"falla del organismo: {texto}")


class VepArchivoPlugin:
    """VEP desde archivo: genera el volante y descarga su detalle."""

    manifest = BotManifest(
        nombre="vep_archivo",
        version="3.0.0",
        operaciones=("generar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="vep_archivo_detalle.pdf",
                content_types=("application/pdf",),
                max_bytes=52_428_800,
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
        hosts_permitidos=("www.afip.gob.ar",),
    )

    def __init__(self, servicio_nombre: str | None = None) -> None:
        """Inyecta el nombre del servicio ARCA desde el sobre sellado."""
        self._servicio_nombre = servicio_nombre or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "VepArchivoPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "VepArchivoPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Sin seccion rige DDJJ y Pagos.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("vep_servicio"):
            self._servicio_nombre = str(service["vep_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, medio de pago y archivo antes de navegar."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "generar"))
        modelo = ENTRADAS.get(operacion)
        if modelo is None:
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            validada = modelo.model_validate(datos)
            try:
                texto = base64.b64decode(str(validada.archivo_b64)).decode(
                    "utf-8", errors="strict"
                )
                registros = validar_archivo_vep(texto)
            except ValueError as exc:
                raise InvalidInputError(f"archivo invalido: {exc}") from exc
            return (operacion, validada, registros)
        except ValueError as exc:
            if isinstance(exc, InvalidInputError):
                raise
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Ejecuta la generacion y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotResult

        operacion, entrada, registros = payload
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
            archivo = runtime.artifact_store.resolve(entrada.archivo_nombre)
            archivo.write_bytes(base64.b64decode(str(entrada.archivo_b64)))
            async with runtime.browser_factory.arca_session(
                credentials=runtime.credentials,
                proxy=runtime.proxy,
                deadline=runtime.deadline,
                cancellation=runtime.cancellation,
            ) as sesion:
                await sesion.login()
                await runtime.cancellation.raise_if_cancelled()
                servicio = await sesion.open_service(self._servicio_nombre)
                datos, artefactos = await self._generar(
                    servicio, entrada, registros, runtime
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

    async def _generar(
        self, servicio: Any, entrada: Any, registros: int, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Genera el VEP desde el .txt y sube el detalle por slot."""
        await runtime.event_sink.progress(
            phase="GENERANDO", percent=45, message="Generando VEP desde archivo"
        )
        archivo = runtime.artifact_store.resolve(entrada.archivo_nombre)
        resumen = await servicio.generar_vep_desde_archivo(
            archivo=archivo, medio_pago=entrada.medio_pago
        )
        await runtime.event_sink.progress(
            phase="PROCESANDO", percent=65, message="Descargando detalle"
        )
        detalle = runtime.artifact_store.resolve(
            f"{entrada.representado_cuit} - VEP - {entrada.medio_pago}.pdf"
        )
        await servicio.descargar_detalle(destino=detalle)
        datos: dict[str, Any] = {
            "operacion": "generar",
            "representado_cuit": entrada.representado_cuit,
            "medio_pago": entrada.medio_pago,
            "registros": registros,
            "lote": str((resumen or {}).get("lote", "")),
            "detalle": detalle.name,
        }
        artefactos: list[dict[str, Any]] = []
        if getattr(entrada, "subir_pdf", True):
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message="Subiendo detalle"
            )
            try:
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_DETALLE, detalle.name
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            artefactos.append(referencia)
            datos["sha256"] = referencia["sha256"]
            datos["size_bytes"] = referencia["size_bytes"]
        if not getattr(entrada, "incluir_json", True):
            datos = {"detalle": datos["detalle"]}
        return datos, artefactos
