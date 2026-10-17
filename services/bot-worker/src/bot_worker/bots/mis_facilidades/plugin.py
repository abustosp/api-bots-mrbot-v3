"""Plugin ``mis_facilidades``: planes de facilidades de pago (ola 3).

Porta ``api-bots-mrbot-v2/app/bot/mis_facilidades_bot.py``
(``bot_mis_facilidades``: detalle de planes, cuotas y obligaciones con
exportacion a PDF y Excel) al contrato S7. Cambios obligatorios
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
import re
import time
from pathlib import Path
from typing import Any, Mapping

from bot_worker.bots.errors import (
    ArtifactUploadError,
    BrowserCrashedError,
    Categoria,
    CaptchaUnsolvableError,
    CredentialsRejectedError,
    DeadlineExceededError,
    ErrorDeBot,
    InvalidInputError,
    TargetUnavailableError,
    sin_secretos,
)
from bot_worker.bots.mis_facilidades.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest
from bot_worker.runtime.context import ruta_para_upload

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "MIS FACILIDADES"
ID_ARTEFACTO_XLSX = "planes_xlsx"
ID_ARTEFACTO_PDF = "plan_pdf"


def nombre_base_archivo(representado_cuit: str, plan: str = "") -> str:
    """Replica el patron de nombres V2 ``'<cuit> - plan - ...'``."""
    digitos = re.sub(r"\D", "", representado_cuit or "")
    sufijo = f" - {re.sub(r'[\\\\/:*?\"<>|]', '_', plan).strip()}" if plan else ""
    return f"{digitos} - MIS FACILIDADES{sufijo}"


def _sanear_componente(valor: str) -> str:
    """Quita caracteres invalidos de nombre de archivo, como V1 ``safe_filename``."""
    limpio = re.sub(r"[\\/:*?\"<>|]+", "_", str(valor or ""))
    limpio = re.sub(r"\s+", " ", limpio).strip()
    return limpio or "reporte"


def nombre_base_plan(representado_cuit: str, plan: str, denominacion: str) -> str:
    """Base de nombre de V1: ``'{fin} - {cuit} - {plan} - {denominacion}'``.

    Cada vista agrega su etiqueta (``pagos``, ``cuotas``, ...) al final, como
    hace ``build_report_path`` en V1.
    """
    digitos = re.sub(r"\D", "", representado_cuit or "")
    fin = digitos[-1:] if digitos else "X"
    return _sanear_componente(
        f"{fin} - {digitos} - {plan or 'plan'} - {denominacion or 'denominacion'}"
    )


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


class MisFacilidadesPlugin:
    """Mis Facilidades de ARCA: planes, cuotas y obligaciones."""

    manifest = BotManifest(
        nombre="mis_facilidades",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="planes.xlsx",
                content_types=(
                    "application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet",
                ),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="plan.pdf",
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
        return "MisFacilidadesPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "MisFacilidadesPlugin":
        """Aplica la sección ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia del
        registro compartido. Solo se aceptan claves conocidas; el resto se
        ignora para no inyectar parametros imprevistos al flujo.
        """
        service = service if isinstance(service, dict) else {}
        for clave in (
            "situacion_excluyente",
            "incluir_pdf",
            "incluir_xlsx",
            "subir_archivos",
        ):
            if clave in service:
                self._defectos[clave] = service[clave]
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT y salidas antes de abrir el navegador."""
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
                servicio = await sesion.open_service(SERVICIO_ARCA, portal="mis_facilidades")
                # Como V1: los CUIT disponibles se leen antes de elegir el representado.
                disponibles = await self._leer_cuits_disponibles(servicio)
                cuit_objetivo = (
                    entrada.representado_cuit or runtime.credentials.cuit_representante
                )
                await servicio.seleccionar_representado(cuit_objetivo)
                datos, artefactos, errores = await self._consultar(
                    servicio, entrada, runtime, cuit_objetivo
                )
                datos["cuits_disponibles"] = disponibles
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        resultado = "OK" if not errores else "PARCIAL"
        return BotResult(
            result=resultado, data=datos, artifacts=artefactos, errors=errores
        )

    @staticmethod
    async def _leer_cuits_disponibles(servicio: Any) -> list[str]:
        """CUITs del combo; si el portal no lo expone, lista vacia (como V1)."""
        leer = getattr(servicio, "cuits_disponibles", None)
        if leer is None:
            return []
        try:
            return [str(c) for c in (await leer() or [])]
        except ErrorDeBot:
            return []

    async def _consultar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime, cuit_objetivo: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[Any]]:
        """Lista planes, filtra por situacion y exporta PDF por vista y Excel (port V1).

        Como V1: un PDF por cada vista del plan (pagos, cuotas, obligaciones),
        un Excel de tablas por plan, y un error en un plan se registra sin
        cortar los demas.
        """
        from bot_worker.runtime.context import BotError

        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Listando planes"
        )
        planes = await servicio.listar_planes()
        excluyentes = [str(s).lower() for s in (entrada.situacion_excluyente or [])]
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": cuit_objetivo,
            "planes": [],
        }
        artefactos: list[dict[str, Any]] = []
        errores: list[Any] = []
        # El supervisor corta el job con ``asyncio.wait_for`` al agotarse el
        # deadline y en ese camino descarta todo lo producido. Para que el
        # parcial sobreviva, el bucle se detiene solo cuando el tiempo que queda
        # ya no alcanza para un plan como el mas lento visto hasta ahora.
        plan_mas_lento = 0.0
        for plan in planes or []:
            situacion = str((plan or {}).get("situacion", "") or "")
            if "plan cancelado" in situacion.lower():
                continue
            if excluyentes and any(e in situacion.lower() for e in excluyentes):
                continue
            await runtime.cancellation.raise_if_cancelled()
            reloj = getattr(runtime, "deadline", None)
            restante = reloj.remaining_seconds() if reloj is not None else None
            if plan_mas_lento and restante is not None and restante <= plan_mas_lento:
                errores.append(
                    BotError(
                        category=Categoria.DEADLINE_EXCEEDED,
                        internal_diagnostic=(
                            f"deadline: quedan {int(restante)} s y el ultimo plan "
                            f"tardo {int(plan_mas_lento)} s"
                        ),
                        retryable=False,
                    )
                )
                break
            inicio_plan = time.monotonic()
            numero = str((plan or {}).get("numero", "") or "s-n")
            # Como V1: la denominacion del request gana; si no llega, la del listado.
            denominacion_plan = (
                entrada.denominacion
                or str((plan or {}).get("denominacion", "") or "")
                or "denominacion"
            )
            base = nombre_base_plan(cuit_objetivo, numero, denominacion_plan)
            resumen: dict[str, Any] = {
                "numero": numero,
                "denominacion": denominacion_plan,
                "situacion": situacion,
            }
            try:
                if entrada.incluir_pdf:
                    vistas = await servicio.exportar_vistas_pdf(
                        numero=numero,
                        destino_dir=runtime.artifact_store.resolve(f"{base}.vistas"),
                        base=base,
                    )
                    resumen["pdf_vistas"] = []
                    for vista in vistas:
                        registro = await self._subir_vista_pdf(
                            vista, entrada, runtime
                        )
                        artefactos.extend(registro.pop("referencias"))
                        resumen["pdf_vistas"].append(registro)
                if entrada.incluir_xlsx:
                    destino = runtime.artifact_store.resolve(f"{base} - tablas.xlsx")
                    await servicio.exportar_plan_xlsx(numero=numero, destino=destino)
                    resumen["xlsx"] = await self._resumir_y_subir(
                        destino, ID_ARTEFACTO_XLSX, entrada, runtime, "Subiendo plan"
                    )
                    artefactos.extend(resumen["xlsx"].pop("referencias"))
            except Exception as exc:
                normalizado = _normalizar_error(exc, [])
                errores.append(
                    BotError(
                        category=normalizado.categoria,
                        internal_diagnostic=(
                            f"plan {numero}: "
                            f"{getattr(normalizado, 'diagnostic_code', None) or normalizado.diagnostico}"
                        ),
                        retryable=False,
                    )
                )
            leer_errores = getattr(servicio, "errores_de_plan", None)
            if callable(leer_errores):
                try:
                    errores_seccion = leer_errores(numero) or []
                except Exception:
                    errores_seccion = []
                for fallo in errores_seccion:
                    if not isinstance(fallo, dict):
                        continue
                    seccion = str(fallo.get("seccion") or "desconocida")
                    codigo = str(fallo.get("codigo") or "mis_facilidades_section_failed")
                    errores.append(
                        BotError(
                            category=Categoria.TARGET_UNAVAILABLE,
                            internal_diagnostic=(
                                f"plan {numero}: sección {seccion}: {codigo}"
                            ),
                            retryable=False,
                        )
                    )
            datos["planes"].append(resumen)
            plan_mas_lento = max(plan_mas_lento, time.monotonic() - inicio_plan)
        datos["planes_procesados"] = len(datos["planes"])
        return datos, artefactos, errores

    async def _subir_vista_pdf(
        self, vista: dict[str, Any], entrada: Any, runtime: BotRuntime
    ) -> dict[str, Any]:
        """Sube un PDF de vista por slot prefirmado y devuelve su resumen."""
        destino: Path = vista["destino"]
        registro: dict[str, Any] = {
            "vista": vista["vista"],
            "archivo": destino.name,
            "size_bytes": destino.stat().st_size if destino.is_file() else 0,
            "referencias": [],
        }
        if entrada.subir_archivos:
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message=f"Subiendo {vista['vista']}"
            )
            try:
                # ``upload`` resuelve contra work_dir: el PDF vive en la
                # subcarpeta ``<base>.vistas``, no en la raiz.
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_PDF,
                    ruta_para_upload(getattr(runtime, "work_dir", None), destino),
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            registro["referencias"].append(referencia)
            registro["sha256"] = referencia["sha256"]
            registro["size_bytes"] = referencia["size_bytes"]
        return registro

    async def _resumir_y_subir(
        self,
        destino: Path,
        artifact_id: str,
        entrada: Any,
        runtime: BotRuntime,
        mensaje: str,
    ) -> dict[str, Any]:
        """Resume un archivo de ``work_dir`` y lo sube por slot prefirmado."""
        resumen: dict[str, Any] = {
            "archivo": destino.name,
            "size_bytes": destino.stat().st_size if destino.is_file() else 0,
        }
        referencias: list[dict[str, Any]] = []
        if entrada.subir_archivos:
            await runtime.event_sink.progress(
                phase="SUBIENDO", percent=80, message=mensaje
            )
            try:
                referencia = await runtime.artifact_store.upload(
                    artifact_id, destino.name
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            referencias.append(referencia)
            resumen["sha256"] = referencia["sha256"]
            resumen["size_bytes"] = referencia["size_bytes"]
        resumen["referencias"] = referencias
        return resumen
