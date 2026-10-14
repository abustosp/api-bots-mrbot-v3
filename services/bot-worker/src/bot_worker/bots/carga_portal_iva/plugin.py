"""Plugin ``carga_portal_iva``: carga de TXTs y CSVs en Portal IVA.

Porta ``api-bots-mrbot-v2/app/bot/carga_portal_iva_bot.py``
(``bot_portal_iva_carga``: TXTs de comprobantes+alicuotas por seccion
ventas/compras con reintentos, CSVs de apertura CF/CF-Rest/DF/DF-Rest,
total de credito fiscal computable desde el CSV) al contrato S7.
Cambios obligatorios respecto de V2:

- Sin ``SessionLocal`` ni base de datos (W-1).
- Sin ``os.getcwd()``/``DOWNLOAD_DIR``: los archivos del payload se
  materializan bajo ``runtime.work_dir`` via ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya construido
  viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin rutas de cliente: el worker recibe nombres locales generados desde
  referencias ``object_key`` y lee esos archivos bajo ``work_dir``.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

Efecto de escritura: idempotencia ``CARGA``; cada seccion informa su
estado para reintentos por seccion independiente como en V2.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import traceback
from typing import Any, Mapping

from bot_worker.bots.carga_portal_iva.schema import (
    CAMPOS_ARCHIVO,
    MAX_ARCHIVO_BYTES,
    esquema_entrada,
)
from bot_worker.bots.carga_portal_iva.schema import CargaPortalIvaInput
from bot_worker.bots.errors import (
    BrowserCrashedError,
    CaptchaUnsolvableError,
    CredentialsRejectedError,
    DeadlineExceededError,
    ErrorDeBot,
    InvalidInputError,
    TargetUnavailableError,
    sin_secretos,
)
from bot_worker.bots.registry import BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "PORTAL IVA"
SECCIONES_CSV = ("CF", "CF Restitucion", "DF", "DF Restitucion")
MAX_REINTENTOS_SECCION = 2


def calcular_total_cf_csv(contenido: bytes) -> str | None:
    """Total de 'Credito Fiscal Computable' de un CSV (port puro de V2)."""
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            texto = contenido.decode("latin-1")
        except UnicodeDecodeError:
            return None
    lineas = texto.splitlines()
    if not lineas:
        return None
    cabecera = lineas[0].strip().split(";")
    indice = next(
        (i for i, h in enumerate(cabecera) if "credito fiscal computable" in h.lower()),
        None,
    )
    if indice is None:
        return None
    total = 0.0
    for linea in lineas[1:]:
        linea = linea.strip()
        if not linea:
            continue
        partes = linea.split(";")
        if len(partes) > indice and partes[indice].strip():
            total += float(
                partes[indice].strip().replace(".", "").replace(",", ".")
            )
    return f"{total:.2f}"


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


_log = logging.getLogger("bot_worker.carga_portal_iva")


async def _diagnostico_seccion(clave: str, exc: BaseException, pagina: Any) -> None:
    """Registra traza, URL y titulo de la pagina al fallar una seccion (solo logs del worker)."""
    url = titulo = ""
    try:
        page = getattr(pagina, "page", None)
        if page is not None:
            url = str(page.url)
            titulo = await page.title()
            cuerpo = (await page.locator("body").inner_text(timeout=3_000)).replace("\n", " | ")[:700]
            ids = await page.evaluate("() => [...document.querySelectorAll('[id]')].map(e => e.id).slice(0, 60).join(',')")
            url = f"{url} BODY={cuerpo} IDS={ids}"
    except Exception:  # noqa: BLE001
        pass
    _log.warning(
        "DIAG seccion=%s exc=%s msg=%s url=%s titulo=%s\n%s",
        clave, type(exc).__name__, str(exc)[:300], url, titulo,
        "".join(traceback.format_exception(exc))[-1800:],
    )


class CargaPortalIvaPlugin:
    """Carga de TXTs y aperturas CSV en Portal IVA (ARCA)."""

    manifest = BotManifest(
        nombre="carga_portal_iva",
        version="3.0.0",
        operaciones=("cargar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(),
        timeout_por_defecto_seconds=900,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=3,
        idempotency_class="CARGA",
        browser_instances_max=1,
        hosts_permitidos=("www.afip.gob.ar", "liva.afip.gob.ar"),
    )

    def __init__(self, servicio: str | None = None) -> None:
        """Inyecta el nombre del servicio desde el sobre sellado."""
        self._servicio = servicio or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "CargaPortalIvaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "CargaPortalIvaPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Clave: ``portal_iva_servicio``.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("portal_iva_servicio"):
            self._servicio = str(service["portal_iva_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, periodo y archivos antes del navegador."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = str(payload.get("operacion", "cargar"))
        if operacion != "cargar":
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return ("cargar", CargaPortalIvaInput.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    def _materializar_archivos(self, entrada: CargaPortalIvaInput, runtime: BotRuntime) -> dict[str, str]:
        """Valida las rutas locales ya descargadas desde el bucket uploads."""
        rutas: dict[str, str] = {}
        for campo in CAMPOS_ARCHIVO:
            nombre = getattr(entrada, campo)
            if not nombre:
                continue
            destino = runtime.artifact_store.resolve(str(nombre))
            if not destino.is_file():
                raise InvalidInputError(f"archivo de entrada no encontrado: {campo}")
            contenido = destino.read_bytes()
            if not contenido.strip():
                raise InvalidInputError(f"archivo sin contenido legible: {campo}")
            if len(contenido) > MAX_ARCHIVO_BYTES:
                raise InvalidInputError(f"archivo supera el máximo permitido: {campo}")
            extension = ".csv" if campo.startswith("csv_") else ".txt"
            if destino.suffix.lower() != extension:
                raise InvalidInputError(f"extensión no admitida para {campo}: use {extension}")
            rutas[campo] = str(nombre)
        return rutas

    async def _importar_txts(
        self, pagina: Any, libro: str, cbte: Path, alicuota: Path, runtime: BotRuntime
    ) -> str:
        """Borra los registros previos del libro e importa el par (port V1 ``_cargar_txt_seccion``).

        El borrado va UNA sola vez, antes del reintento: si la importacion ya llego
        a entrar y solo falla la verificacion, reintentar no debe borrar ni volver
        a importar lo que ya quedo cargado. El borrado es obligatorio: sin el, la
        importacion duplicaria los comprobantes. Sin boton de borrado, el libro
        no se toca y la seccion falla.
        """
        await runtime.cancellation.raise_if_cancelled()
        await pagina.abrir_libro(libro)
        if not await pagina.eliminar_todo():
            raise TargetUnavailableError(
                f"{libro}: no se pudo borrar los registros previos",
                diagnostic_code="carga_eliminar_todo_failed",
            )
        ultimo_error = "importacion no intentada"
        for intento in range(1, MAX_REINTENTOS_SECCION + 1):
            await runtime.cancellation.raise_if_cancelled()
            try:
                await pagina.importar_txt(libro, cbte, alicuota)
                return f"{libro}: importado (intento {intento})"
            except Exception as exc:  # noqa: BLE001 - se reintenta por seccion
                ultimo_error = type(exc).__name__
                await _diagnostico_seccion(f"{libro}_intento{intento}", exc, pagina)
                await pagina.volver_al_inicio()
                if intento < MAX_REINTENTOS_SECCION:
                    await pagina.abrir_libro(libro)
        raise TargetUnavailableError(
            f"{libro}: {ultimo_error}",
            diagnostic_code="carga_txt_failed",
        )

    async def _cargar_apertura_csv(
        self, pagina: Any, seccion: str, csv_path: Path, runtime: BotRuntime
    ) -> str:
        """Carga un CSV de apertura desde "Informacion adicional" (port V1 ``cargar_apertura_csv``)."""
        await runtime.cancellation.raise_if_cancelled()
        await pagina.cargar_csv(seccion, csv_path)
        return f"{seccion}: cargado"

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Ejecuta la carga por secciones y retorna ``BotResult`` tipado."""
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
        rutas = self._materializar_archivos(entrada, runtime)
        # Rutas absolutas dentro de work_dir: el portal nunca recibe nombres sueltos.
        rutas_abs = {
            campo: runtime.artifact_store.resolve(nombre) for campo, nombre in rutas.items()
        }
        total_cf = (
            calcular_total_cf_csv(rutas_abs["csv_cf_path"].read_bytes())
            if rutas_abs.get("csv_cf_path")
            else None
        )
        resultados: dict[str, Any] = {
            "operacion": "cargar",
            "representado_cuit": entrada.representado_cuit,
            "periodo": entrada.periodo,
            "secciones": {},
        }
        if total_cf is not None:
            resultados["total_credito_fiscal_computable_cf"] = total_cf
        try:
            async with runtime.browser_factory.arca_session(
                credentials=runtime.credentials,
                proxy=runtime.proxy,
                deadline=runtime.deadline,
                cancellation=runtime.cancellation,
            ) as sesion:
                await sesion.login()
                await runtime.cancellation.raise_if_cancelled()
                pagina = await sesion.open_service(self._servicio, portal="carga_portal_iva")
                await pagina.seleccionar_representado(entrada.representado_cuit)
                preparar_periodo = getattr(pagina, "preparar_periodo", None)
                if preparar_periodo is not None:
                    periodos = await preparar_periodo(
                        entrada.periodo,
                        operaciones_ng_o_e=entrada.operaciones_ng_o_e,
                        prorrateo_global=entrada.prorrateo_global,
                        prorrateo_asignacion_directa=entrada.prorrateo_asignacion_directa,
                        prorrateo_ambos=entrada.prorrateo_ambos,
                        importacion_definitiva_bienes=entrada.importacion_definitiva_bienes,
                        importacion_servicios=entrada.importacion_servicios,
                        regimen_turiva=entrada.regimen_turiva,
                        bienes_usados=entrada.bienes_usados,
                        ninguna_anteriores=entrada.ninguna_anteriores,
                    )
                    if periodos:
                        resultados["periodos_seleccionables"] = periodos
                else:
                    # Backwards-compatible fake/adapter contract.
                    await pagina.seleccionar_periodo(entrada.periodo)
                await runtime.event_sink.progress(
                    phase="CONSULTA", percent=45, message="Cargando comprobantes"
                )
                for clave, libro, cbte, alic in (
                    ("ventas_txt", "ventas", "liv_cbte_path", "liv_alicuota_path"),
                    ("compras_txt", "compras", "lic_cbte_path", "lic_alicuota_path"),
                ):
                    if not rutas_abs.get(cbte):
                        continue
                    await self._seccion(
                        resultados["secciones"], clave,
                        self._importar_txts(pagina, libro, rutas_abs[cbte], rutas_abs[alic], runtime),
                        pagina,
                    )
                await runtime.event_sink.progress(
                    phase="PROCESANDO", percent=65, message="Cargando aperturas"
                )
                if entrada.prorrateo_global and total_cf is not None and rutas_abs.get("csv_cf_path"):
                    # Precondicion del portal (igual que el V1): el CF Computable Global
                    # debe estar cargado antes de abrir Informacion Adicional.
                    await self._seccion(
                        resultados["secciones"], "credito_fiscal_compras",
                        self._credito_fiscal_compras(pagina, total_cf, runtime),
                        pagina,
                    )
                    with contextlib.suppress(Exception):
                        await pagina.volver_al_inicio()
                for seccion, campo in (
                    ("CF", "csv_cf_path"),
                    ("CF Restitucion", "csv_cf_restitucion_path"),
                    ("DF", "csv_df_path"),
                    ("DF Restitucion", "csv_df_restitucion_path"),
                ):
                    if not rutas_abs.get(campo):
                        continue
                    await self._seccion(
                        resultados["secciones"], seccion,
                        self._cargar_apertura_csv(pagina, seccion, rutas_abs[campo], runtime),
                        pagina,
                    )
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return self._resultado(resultados)

    async def _seccion(
        self,
        secciones: dict[str, Any],
        clave: str,
        operacion: Any,
        pagina: Any = None,
    ) -> None:
        """Corre una seccion aislada: si falla, queda registrada y el job sigue (V1).

        Un error de seccion no tumba el job completo: las demas secciones siguen y el
        resultado final queda PARCIAL. Las cancelaciones y deadlines sí se propagan.
        """
        from bot_worker.bots.errors import CredentialsRejectedError as _Credenciales
        from bot_worker.bots.errors import DeadlineExceededError as _Deadline
        from bot_worker.runtime.context import JobCancelled

        try:
            secciones[clave] = await operacion
        except (JobCancelled, _Deadline, _Credenciales):
            raise
        except ErrorDeBot as exc:
            secciones[clave] = f"error: {exc.__class__.__name__}"
            await _diagnostico_seccion(clave, exc, pagina)
        except Exception as exc:  # noqa: BLE001 - una seccion no tumba el job
            secciones[clave] = f"error: {type(exc).__name__}"
            await _diagnostico_seccion(clave, exc, pagina)

    async def _credito_fiscal_compras(self, pagina: Any, total_cf: str, runtime: BotRuntime) -> str:
        """Crédito fiscal computable global de compras (V1 ``_ir_a_editar_credito_fiscal_en_compras``)."""
        await runtime.cancellation.raise_if_cancelled()
        manejado = await pagina.cargar_credito_fiscal_compras(total_cf)
        return "ok" if manejado else "sin_modal"

    @staticmethod
    def _resultado(resultados: dict[str, Any]) -> BotResult:
        """OK si todas las secciones cargaron; PARCIAL si alguna fallo y otra no."""
        from bot_worker.bots.errors import Categoria
        from bot_worker.runtime.context import BotError, BotResult

        secciones = resultados.get("secciones", {})
        fallidas = [k for k, v in secciones.items() if isinstance(v, str) and v.startswith("error:")]
        if not fallidas:
            return BotResult(result="OK", data=resultados)
        if len(fallidas) == len(secciones):
            return BotResult(
                result="ERROR",
                data=resultados,
                errors=[
                    BotError(
                        category=Categoria.TARGET_UNAVAILABLE,
                        internal_diagnostic="carga_todas_las_secciones_fallaron",
                        retryable=True,
                    )
                ],
            )
        return BotResult(
            result="PARCIAL",
            data=resultados,
            warnings=["secciones con error: " + ", ".join(fallidas)],
        )
