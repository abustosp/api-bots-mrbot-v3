"""Plugin ``libro_sueldos_digital``: DDJJ por rango de períodos (worker V3).

Porta la versión dev ``bots_dev/libro_sueldos_digital/libro_sueldos_digital_bot.py``
al contrato worker V3. Cambios obligatorios respecto de la versión dev:

- Sin ``os.environ`` ni ``get_proxy_config()``: proxy y credenciales
  llegan por ``runtime`` desde el sobre sellado.
- Sin ``tempfile`` ni ``subir_archivo_a_minio`` con claves: cada PDF se
  guarda bajo ``runtime.work_dir`` y se sube con ``artifact_store.upload``
  (slot/artefacto ``lsd_pdf``); la central arma la URL a partir del
  ``object_key``.
- Sin capturas, HTML ni HAR: solo acciones del portal.
- Sin credenciales ni texto del sitio en logs: categoría + diagnóstico
  redactado con ``sin_secretos``.

Una operación (``descargar``): además de bajar el Libro de Sueldos Digital
de cada liquidación del rango, deja el detalle por período en el resultado
cuando el pedido lo pide (``incluir_json``).
"""

from __future__ import annotations

import asyncio
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
from bot_worker.bots.libro_sueldos_digital.schema import (
    ENTRADAS,
    esquema_entrada,
    generar_rango_periodos,
)
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

#: Nombre visible del servicio en el catálogo de Clave Fiscal.
SERVICIO_ARCA = "LIBRO DE SUELDOS DIGITAL"
#: Portal que implementa la navegación (``runtime/portals``).
PORTAL_ARCA = "libro_sueldos_digital"
#: Artefacto declarado para cada PDF del Libro de Sueldos Digital.
ID_ARTEFACTO_LSD = "lsd_pdf"
INVALID_FILENAME_CHARS = ('/', '\\', ':', '*', '?', '"', '<', '>', '|')


def _sanear(texto: str) -> str:
    limpio = str(texto or "")
    for caracter in INVALID_FILENAME_CHARS:
        limpio = limpio.replace(caracter, "_")
    return limpio.strip() or "sin_dato"


def nombre_lsd_pdf(cuit: str, periodo: str, liquidacion: str) -> str:
    """Nombre determinístico del PDF (mismo patrón que publica el sitio).

    ``{cuit}_{AAAAMM}_{nro:05d}.pdf``; el número se normaliza a dígitos.
    """
    solo_digitos = "".join(c for c in str(liquidacion or "") if c.isdigit())
    nro = f"{int(solo_digitos):05d}" if solo_digitos else "00001"
    return f"{_sanear(cuit)}_{_sanear(periodo)}_{nro}.pdf"


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnóstico seguro."""
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


class LibroSueldosDigitalPlugin:
    """Descarga el Libro de Sueldos Digital (PDF) por rango de períodos."""

    manifest = BotManifest(
        nombre="libro_sueldos_digital",
        version="3.0.0",
        operaciones=("descargar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre=ID_ARTEFACTO_LSD,
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
            "auth.afip.gob.ar",
            "portalcf.cloud.afip.gob.ar",
            "serviciossegsoc.afip.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __init__(self, servicio: str | None = None) -> None:
        """Valor por defecto; la sección sellada lo ajusta en ``configure``."""
        self._servicio = servicio or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "LibroSueldosDigitalPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "LibroSueldosDigitalPlugin":
        """Aplica la sección ``service`` del sobre sellado (por job).

        Clave conocida: ``servicio`` (nombre visible del servicio ARCA).
        """
        service = service if isinstance(service, dict) else {}
        if service.get("servicio"):
            self._servicio = str(service["servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operación, CUIT y rango AAAAMM antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "descargar"))
        modelo = ENTRADAS.get(operacion)
        if modelo is None:
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return (operacion, modelo.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Descarga el rango completo y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotError, BotResult

        from bot_worker.bots.errors import Categoria

        operacion, entrada = payload
        if runtime.credentials is None:
            raise CredentialsRejectedError("el plugin requiere credenciales fiscales")
        secretos = [runtime.credentials.clave]
        representado = (
            entrada.representado_cuit or runtime.credentials.cuit_representante
        )
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

        empleador: dict[str, str] = {"cuit": "", "denominacion": ""}
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
                    self._servicio, portal=PORTAL_ARCA
                )
                # El servicio pide elegir representado cuando la cuenta
                # representa a más de un CUIT (IndexContribuyente + Aceptar):
                # se selecciona y confirma antes de leer el empleador.
                representado_seleccionado = await servicio.seleccionar_representado(
                    entrada.representado_cuit or ""
                )
                empleador = await servicio.empleador_actual()
                self._verificar_empleador(
                    empleador,
                    entrada.representado_cuit or representado_seleccionado,
                )
                await runtime.cancellation.raise_if_cancelled()
                datos, artefactos, errores = await self._descargar_rango(
                    servicio,
                    entrada,
                    representado,
                    periodos,
                    runtime,
                    empleador.get("cuit") or representado,
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
        datos["empleador"] = dict(empleador)
        if empleador.get("cuit"):
            datos["representado_cuit"] = empleador["cuit"]
        if not datos["periodos_descargados"]:
            raise TargetUnavailableError(
                "no se encontró ninguna liquidación descargable en el rango solicitado",
                diagnostic_code="lsd_no_liquidations",
            )
        if errores >= datos["liquidaciones_encontradas"]:
            return BotResult(
                result="ERROR",
                data=datos,
                artifacts=artefactos,
                errors=[
                    BotError(
                        category=Categoria.TARGET_UNAVAILABLE,
                        internal_diagnostic="lsd_todas_las_liquidaciones_fallaron",
                        retryable=True,
                    )
                ],
            )
        if errores:
            return BotResult(
                result="PARCIAL",
                data=datos,
                artifacts=artefactos,
                warnings=[
                    f"liquidaciones con error: {errores} de "
                    f"{datos['liquidaciones_encontradas']}"
                ],
            )
        return BotResult(result="OK", data=datos, artifacts=artefactos)

    def _verificar_empleador(
        self, empleador: dict[str, str], representado: str
    ) -> None:
        """Corta si la sesión no quedó posicionada en el representado esperado.

        ``representado`` es el CUIT pedido por el sobre o, si no vino, el
        confirmado en el selector de ``IndexContribuyente``. Falla cerrado
        siempre: la cabecera del empleador (``#ctl00_lblCuit``) debe informar
        un CUIT válido de 11 dígitos y, cuando hay expectativa, debe coincidir
        con ella. Sin expectativa rige el empleador de la sesión.
        """
        actual = "".join(c for c in str(empleador.get("cuit") or "") if c.isdigit())
        if len(actual) != 11:
            raise TargetUnavailableError(
                "el empleador de la sesión no informó un CUIT válido",
                diagnostic_code="lsd_represented_cuit_unverified",
            )
        esperado = "".join(c for c in str(representado or "") if c.isdigit())
        if not esperado:
            return
        if esperado != actual:
            raise TargetUnavailableError(
                "el empleador de la sesión no coincide con el representado solicitado",
                diagnostic_code="lsd_represented_cuit_mismatch",
            )

    async def _descargar_rango(
        self,
        servicio: Any,
        entrada: Any,
        representado: str,
        periodos: list[str],
        runtime: BotRuntime,
        empleador_cuit: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        """Descarga cada liquidación del rango a ``work_dir`` y sube por slot."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=35, message="Consultando periodos"
        )
        objetivos = await servicio.listar_liquidaciones(
            entrada.periodo_desde, entrada.periodo_hasta
        )
        total = len(objetivos)
        subir = bool(getattr(entrada, "subir_archivos", True))
        detalle: list[dict[str, Any]] = []
        artefactos: list[dict[str, Any]] = []
        con_error = 0

        for indice, objetivo in enumerate(objetivos):
            await runtime.cancellation.raise_if_cancelled()
            periodo = str(objetivo["periodo"])
            liquidacion = str(objetivo.get("liquidacion") or "")
            destino = runtime.artifact_store.resolve(
                nombre_lsd_pdf(empleador_cuit, periodo, liquidacion)
            )
            registro: dict[str, Any] = {
                "periodo": periodo,
                "liquidacion": liquidacion,
                "archivo": destino.name,
            }
            try:
                await servicio.descargar_libro(objetivo=objetivo, destino=destino)
            except ErrorDeBot as exc:
                con_error += 1
                registro["error"] = getattr(exc, "diagnostic_code", "lsd_download_error")
                detalle.append(registro)
                continue
            except Exception:
                con_error += 1
                registro["error"] = "lsd_download_error"
                detalle.append(registro)
                continue

            if subir:
                await runtime.event_sink.progress(
                    phase="SUBIENDO",
                    percent=50 + int(40 * (indice + 1) / max(total, 1)),
                    message=f"Subiendo liquidacion {indice + 1}/{total}",
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        ID_ARTEFACTO_LSD,
                        _ruta_relativa(runtime, destino),
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                registro["sha256"] = referencia["sha256"]
                registro["size_bytes"] = referencia["size_bytes"]
                registro["object_key"] = referencia.get("object_key") or ""
            detalle.append(registro)

        datos: dict[str, Any] = {
            "representado_cuit": representado,
            "periodo_desde": entrada.periodo_desde,
            "periodo_hasta": entrada.periodo_hasta,
            "periodos_solicitados": periodos,
            "periodos_encontrados": sorted({o["periodo"] for o in objetivos}),
            "liquidaciones_encontradas": total,
            "liquidaciones_descargadas": sum(1 for r in detalle if "error" not in r),
            "periodos_descargados": sorted(
                {r["periodo"] for r in detalle if "error" not in r}
            ),
        }
        if getattr(entrada, "incluir_json", True):
            datos["detalle"] = detalle
        return datos, artefactos, con_error


def _ruta_relativa(runtime: BotRuntime, destino: Path) -> str:
    """Ruta relativa a ``work_dir`` como exige ``artifact_store.upload``."""
    from bot_worker.runtime.context import ruta_para_upload

    return ruta_para_upload(runtime.work_dir, destino)
