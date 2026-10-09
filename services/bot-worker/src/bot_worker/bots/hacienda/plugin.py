"""Plugin ``hacienda``: comprobantes de hacienda con navegador (worker V3).

Porta ``api-bots-mrbot-v2/app/bot/hacienda_bot.py`` (``hacienda`` /
``descargar_hacienda``) al contrato worker V3. Cambios obligatorios
respecto de V2:

- Sin ``SessionLocal``: la central persiste tras el callback.
- Sin ``download_dir`` del host: los comprobantes y el consolidado
  viven bajo ``runtime.work_dir`` via ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: proxy y credenciales
  llegan por ``runtime`` desde el sobre sellado.
- Sin ``_subir_archivos_a_minio`` con claves: el consolidado se sube
  con ``artifact_store.upload`` (slot ``hacienda_consolidado``).
- Sin pandas: V2 consolida con ``DataFrame``; el worker no garantiza
  esa dependencia, asi que el plugin escribe el Excel consolidado de V1
  (hojas ``consolidado``, ``por_emisor`` y ``por_receptor``) con el
  escritor XLSX de biblioteca estandar que ya usa ``mis_facilidades``.
- Descarga de PDF por comprobante activa por defecto, como V1: el portal
  recorre cada consulta una sola vez y de ahi salen el cuadro y los PDF.
- Sin credenciales en logs: categoria + diagnostico redactado.

El flujo V2 (login, ``COMPROBANTES EN LINEA``, seleccion de
denominacion, doble apertura de ``Hacienda y Carne - Liquidacion`` y
consulta por emisor/receptor con fechas) se delega al handle del
servicio; el plugin orquesta, consolida y sube.

La seccion ``service`` del sobre sellado (vía :meth:`configure`) solo
ajusta los nombres visibles de los servicios y la muestra JSON.
"""

from __future__ import annotations

import asyncio
import csv
from datetime import datetime
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
from bot_worker.bots.hacienda.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_COMPROBANTES = "COMPROBANTES EN LÍNEA"
SERVICIO_HACIENDA = "Hacienda y Carne - Liquidacion"
ID_ARTEFACTO_CONSOLIDADO = "hacienda_consolidado"
ID_ARTEFACTO_PDF = "hacienda_pdf"


def nombre_consolidado(
    representado_cuit: str, consulta: str, desde: str, hasta: str
) -> str:
    """Nombre estable del consolidado tabular por consulta (port de V2)."""
    desde_limpio = desde.replace("/", "")
    hasta_limpio = hasta.replace("/", "")
    return f"HACIENDA - {consulta.upper()} - {representado_cuit} - {desde_limpio} - {hasta_limpio}.csv"


def nombre_consolidado_xlsx(
    cuit_representante: str,
    denominacion: str,
    desde: str,
    hasta: str,
    marca: str,
) -> str:
    """Nombre del Excel consolidado con el patron de V1 (port de ``safe_filename``).

    V1: ``HACIENDA - TABLA CONSOLIDADA - <cuit representante> - <denominacion>
    - <desde> - <hasta> - <stamp>.xlsx``.
    """
    crudo = (
        f"HACIENDA - TABLA CONSOLIDADA - {cuit_representante} - "
        f"{denominacion or 'SIN_DENOMINACION'} - {desde} - {hasta} - {marca}.xlsx"
    )
    limpio = "".join(caracter for caracter in crudo if caracter not in '\\/:*?"<>|')
    return limpio or f"HACIENDA - TABLA CONSOLIDADA - {marca}.xlsx"


def _columnas_consolidado(filas: list[dict[str, Any]]) -> list[str]:
    """Union de claves en orden de aparicion (equivale a ``pd.concat``)."""
    columnas: list[str] = []
    for fila in filas:
        for clave in fila:
            if clave not in columnas:
                columnas.append(str(clave))
    return columnas


def hojas_consolidado(
    filas_por_consulta: Mapping[str, list[dict[str, Any]]]
) -> list[tuple[str, list[str], list[list[str]]]]:
    """Hojas del Excel consolidado de V1 (``consolidado`` + cada consulta).

    V1 arma ``consolidado`` con ``pd.concat`` de todos los cuadros y una hoja
    por consulta (``por_emisor``/``por_receptor``) solo si tiene registros. Sin
    registros deja una unica hoja con el aviso ``sin_datos``.
    """
    todas: list[dict[str, Any]] = []
    for filas in filas_por_consulta.values():
        todas.extend(filas)
    if not todas:
        return [
            (
                "consolidado",
                ["consulta", "mensaje"],
                [["sin_datos", "Sin registros de comprobantes"]],
            )
        ]
    hojas: list[tuple[str, list[str], list[list[str]]]] = [
        (
            "consolidado",
            _columnas_consolidado(todas),
            [
                [str(fila.get(columna, "")) for columna in _columnas_consolidado(todas)]
                for fila in todas
            ],
        )
    ]
    for consulta, filas in filas_por_consulta.items():
        if not filas:
            continue
        columnas = _columnas_consolidado(filas)
        hojas.append(
            (
                consulta[:31],
                columnas,
                [
                    [str(fila.get(columna, "")) for columna in columnas]
                    for fila in filas
                ],
            )
        )
    return hojas


def escribir_consolidado(destino: Path, filas: list[dict[str, Any]]) -> int:
    """Escribe el CSV consolidado con union de columnas (funcion pura).

    Retorna la cantidad de filas escritas. Sin red ni secretos.
    """
    columnas: list[str] = []
    for fila in filas:
        for clave in fila:
            if clave not in columnas:
                columnas.append(clave)
    if not columnas:
        columnas = ["aviso"]
    with open(destino, "w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=columnas, extrasaction="ignore")
        escritor.writeheader()
        for fila in filas:
            escritor.writerow({k: fila.get(k, "") for k in columnas})
    return len(filas)


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


class HaciendaPlugin:
    """Descarga comprobantes de hacienda por emisor/receptor y consolida."""

    manifest = BotManifest(
        nombre="hacienda",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="hacienda_consolidado",
                content_types=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "text/csv",
                ),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="hacienda_pdf",
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

    def __init__(
        self,
        servicio_comprobantes: str | None = None,
        servicio_hacienda: str | None = None,
        muestra_json: int = 5,
    ) -> None:
        """Valores por defecto; la seccion sellada los ajusta en ``configure``."""
        self._servicio_comprobantes = servicio_comprobantes or SERVICIO_COMPROBANTES
        self._servicio_hacienda = servicio_hacienda or SERVICIO_HACIENDA
        self._muestra_json = muestra_json

    def __repr__(self) -> str:
        return "HaciendaPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "HaciendaPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Claves conocidas: ``servicio_comprobantes``,
        ``servicio_hacienda`` (nombres visibles en ARCA) y
        ``muestra_json``. Sin seccion rigen los valores por defecto V2.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("servicio_comprobantes"):
            self._servicio_comprobantes = str(service["servicio_comprobantes"])
        if service.get("servicio_hacienda"):
            self._servicio_hacienda = str(service["servicio_hacienda"])
        try:
            muestra = int(service.get("muestra_json", self._muestra_json))
        except (TypeError, ValueError):
            muestra = self._muestra_json
        self._muestra_json = max(0, min(20, muestra))
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, denominacion y rango antes del navegador."""
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
                comprobantes = await sesion.open_service(
                    self._servicio_comprobantes, portal="hacienda"
                )
                await comprobantes.seleccionar_denominacion(
                    entrada.denominacion,
                )
                hacienda = await comprobantes.abrir_hacienda(
                    servicio=self._servicio_hacienda,
                    denominacion=entrada.denominacion,
                )
                datos, artefactos = await self._consultar(
                    hacienda, entrada, runtime
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

    async def _consultar(
        self, hacienda: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Recorre emisor/receptor, consolida el Excel V1 y sube por slot.

        Como V1, cada consulta se navega una sola vez: de ahí salen las filas
        del cuadro (hoja del Excel consolidado) y los PDF por comprobante.
        """
        datos: dict[str, Any] = {
            "representado_cuit": entrada.representado_cuit,
            "denominacion": entrada.denominacion,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
        }
        artefactos: list[dict[str, Any]] = []
        consultas = []
        if entrada.por_emisor:
            consultas.append("por_emisor")
        if entrada.por_receptor:
            consultas.append("por_receptor")
        filas_por_consulta: dict[str, list[dict[str, Any]]] = {}
        for consulta in consultas:
            await runtime.cancellation.raise_if_cancelled()
            await runtime.event_sink.progress(
                phase="CONSULTA", percent=45, message=f"Consultando {consulta}"
            )
            filas, pdfs = await self._consultar_filas(hacienda, consulta, entrada, runtime)
            if not isinstance(filas, list):
                raise TargetUnavailableError(
                    f"consulta {consulta} devolvio formato inesperado"
                )
            normalizadas = [dict(f) for f in filas if isinstance(f, dict)]
            filas_por_consulta[consulta] = normalizadas
            resumen: dict[str, Any] = {"filas": len(normalizadas)}
            if getattr(entrada, "incluir_json", True):
                resumen["muestra"] = normalizadas[: self._muestra_json]
            if pdfs is not None:
                resumen["pdf"] = await self._subir_pdfs(
                    pdfs, consulta, runtime, artefactos
                )
            datos[consulta] = resumen
        resumen_consolidado = await self._consolidar(
            filas_por_consulta, entrada, runtime, artefactos
        )
        if resumen_consolidado is not None:
            datos["consolidado"] = resumen_consolidado
        return datos, artefactos

    async def _consolidar(
        self,
        filas_por_consulta: dict[str, list[dict[str, Any]]],
        entrada: Any,
        runtime: BotRuntime,
        artefactos: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Escribe y sube el Excel consolidado de V1 (una hoja por consulta)."""
        from bot_worker.runtime.portals.hacienda import escribir_xlsx

        await runtime.event_sink.progress(
            phase="PROCESANDO", percent=65, message="Consolidando tablas"
        )
        cuit_representante = getattr(runtime.credentials, "cuit_representante", "")
        nombre = nombre_consolidado_xlsx(
            str(cuit_representante),
            entrada.denominacion,
            entrada.fecha_desde,
            entrada.fecha_hasta,
            datetime.now().strftime("%Y%m%d_%H%M%S"),
        )
        destino = runtime.artifact_store.resolve(nombre)
        hojas = hojas_consolidado(filas_por_consulta)
        escribir_xlsx(destino, hojas)
        resumen: dict[str, Any] = {
            "archivo": destino.name,
            "filas": sum(len(filas) for filas in filas_por_consulta.values()),
            "hojas": [hoja[0] for hoja in hojas],
        }
        if not getattr(entrada, "subir_excel", True):
            return resumen
        await runtime.event_sink.progress(
            phase="SUBIENDO", percent=80, message="Subiendo consolidado"
        )
        try:
            referencia = await runtime.artifact_store.upload(
                ID_ARTEFACTO_CONSOLIDADO, destino.name
            )
        except ValueError as exc:
            raise ArtifactUploadError(str(exc)) from exc
        artefactos.append(referencia)
        resumen["sha256"] = referencia["sha256"]
        resumen["size_bytes"] = referencia["size_bytes"]
        return resumen

    async def _consultar_filas(
        self,
        hacienda: Any,
        consulta: str,
        entrada: Any,
        runtime: BotRuntime,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]] | None]:
        """Filas tabulares y PDF de una consulta, en una sola navegacion (V1).

        Con ``descargar_pdf`` (activo por defecto, como V1) el portal recorre
        la consulta una vez y devuelve las filas del cuadro y los PDF; sin el
        flag conserva el contrato tabular de siempre.
        """
        if not getattr(entrada, "descargar_pdf", True):
            filas = await hacienda.consultar(
                consulta_key=consulta,
                desde=entrada.fecha_desde,
                hasta=entrada.fecha_hasta,
            )
            return filas, None
        await runtime.event_sink.progress(
            phase="PDF", percent=70, message=f"Descargando PDF {consulta}"
        )
        resultado = await hacienda.recorrer_consulta(
            consulta_key=consulta,
            desde=entrada.fecha_desde,
            hasta=entrada.fecha_hasta,
            destino_dir=runtime.artifact_store.resolve(f"pdf_{consulta}"),
            representado_cuit=entrada.representado_cuit,
            descargar_pdf=True,
        )
        filas = resultado.get("filas") if isinstance(resultado, dict) else None
        pdfs = resultado.get("pdfs") if isinstance(resultado, dict) else None
        return list(filas or []), pdfs

    async def _subir_pdfs(
        self,
        pdfs: list[dict[str, Any]],
        consulta: str,
        runtime: BotRuntime,
        artefactos: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Sube cada PDF bajado; los fallos por comprobante no detienen la consulta.

        ``artifact_store.upload`` espera una ruta relativa a ``work_dir``, por
        eso se usa ``pdf_<consulta>/<archivo>``.
        """
        subidos = 0
        errores: list[dict[str, str]] = []
        for pdf in pdfs:
            if "error" in pdf:
                errores.append({"archivo": pdf["archivo"], "error": pdf["error"]})
                continue
            relativo = f"pdf_{consulta}/{pdf['archivo']}"
            try:
                referencia = await runtime.artifact_store.upload(
                    ID_ARTEFACTO_PDF, relativo
                )
            except ValueError as exc:
                raise ArtifactUploadError(str(exc)) from exc
            artefactos.append(referencia)
            subidos += 1
        return {"total": len(pdfs), "subidos": subidos, "errores": errores}
