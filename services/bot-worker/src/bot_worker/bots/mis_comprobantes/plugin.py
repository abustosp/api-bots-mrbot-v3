"""Plugin ``mis_comprobantes``: piloto con navegador (ola 2, plan 07).

Porta ``api-bots-mrbot-v2/app/bot/comprobantes_bot.py`` (1.508 LOC, tres
operaciones: consultar, solicitar e historial) al contrato S7. Cambios
obligatorios respecto de V2:

- Sin ``SessionLocal`` ni escritura de ``ConsultaLog``: la central
  persiste el resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni ``descargas/...``: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya construido
  viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin ``cookies_header`` en el resultado: ``solicitar`` devuelve solo
  ids de consulta (continuacion); las cookies nunca salen del worker.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory`` con la
interfaz minima documentada abajo; el plugin nunca importa Playwright
directo ni desactiva headless/proxy/limpieza.
"""

from __future__ import annotations

import asyncio
import csv
import shutil
import tempfile
import zipfile
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
from bot_worker.bots.mis_comprobantes.schema import (
    ENTRADAS,
    esquema_entrada,
    limpiar_cuit,
    normalizar_fecha,
)
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "MIS COMPROBANTES"
ID_ARTEFACTO_EMITIDOS = "emitidos_csv"
ID_ARTEFACTO_RECIBIDOS = "recibidos_csv"
COLUMNAS_FECHA = ("Fecha", "Fecha de Emisión", "Fecha de Emision")


def nombre_base_archivo(
    representado_cuit: str, representado_nombre: str, desde: str, hasta: str, tipo: str
) -> str:
    """Replica el patron de nombres V2 ``'{n} - MCE/MCR - ...'``."""
    cuit = limpiar_cuit(representado_cuit)
    desde_limpio = normalizar_fecha(desde).replace("/", "")
    hasta_limpio = normalizar_fecha(hasta).replace("/", "")
    sigla = "MCE" if tipo == "emitidos" else "MCR"
    return f"{cuit[-1]} - {sigla} - {desde_limpio} - {hasta_limpio} - {cuit} - {representado_nombre}"


def filtrar_csv_por_rango(
    origen: Path, destino: Path, desde: str, hasta: str
) -> int:
    """Filtra un CSV de comprobantes por rango inclusive (port de V2).

    Detecta la columna de fecha (``Fecha`` o ``Fecha de Emisión``),
    conserva las filas dentro de ``[desde, hasta]`` en formato
    ``dd/mm/aaaa`` y escribe el resultado en ``destino``. Retorna la
    cantidad de filas conservadas. Funcion pura, sin red ni secretos.
    """
    inicio = datetime.strptime(normalizar_fecha(desde), "%d/%m/%Y").date()
    fin = datetime.strptime(normalizar_fecha(hasta), "%d/%m/%Y").date()
    with open(origen, "r", encoding="utf-8-sig", newline="") as fh:
        muestra = fh.read(8192)
        fh.seek(0)
        try:
            dialecto = csv.Sniffer().sniff(muestra, delimiters=";,\t|")
            delimitador = dialecto.delimiter
        except csv.Error:
            delimitador = ";" if ";" in muestra else ","
        lector = csv.DictReader(fh, delimiter=delimitador)
        if lector.fieldnames is None:
            raise ValueError("csv sin encabezado")
        columna = next(
            (c for c in COLUMNAS_FECHA if c in lector.fieldnames), None
        )
        if columna is None:
            raise ValueError("csv sin columna de fecha conocida")
        filas = list(lector)
        campos = lector.fieldnames

    def parsear_fecha(valor: str) -> Any:
        texto = str(valor or "").strip()
        for formato in ("%d/%m/%Y", "%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(texto, formato).date()
            except ValueError:
                continue
        return None

    conservadas = []
    for fila in filas:
        dia = parsear_fecha(fila.get(columna, ""))
        if dia is None:
            continue
        if inicio <= dia <= fin:
            conservadas.append(fila)
    with open(destino, "w", encoding="utf-8", newline="") as fh:
        escritor = csv.DictWriter(fh, fieldnames=campos, delimiter=delimitador)
        escritor.writeheader()
        escritor.writerows(conservadas)
    return len(conservadas)


def materializar_csv_descargado(origen: Path) -> None:
    """Convierte en CSV una descarga directa o el ZIP que entrega ARCA.

    El botón ``CSV`` de Mis Comprobantes entrega un ZIP en producción. Algunas
    instalaciones de ARCA entregan el CSV directo, por lo que se aceptan ambos
    formatos. El miembro se copia a un temporal dentro del mismo directorio y
    luego se reemplaza de forma atómica para no leer un archivo parcialmente
    escrito.
    """
    if not zipfile.is_zipfile(origen):
        return
    with zipfile.ZipFile(origen) as archivo_zip:
        miembros = [
            miembro
            for miembro in archivo_zip.infolist()
            if not miembro.is_dir() and miembro.filename.lower().endswith(".csv")
        ]
        if not miembros:
            raise ValueError("descarga ZIP sin archivo CSV")
        miembro = miembros[0]
        with archivo_zip.open(miembro) as entrada, tempfile.NamedTemporaryFile(
            mode="wb", dir=origen.parent, prefix=f".{origen.stem}-", suffix=".tmp", delete=False
        ) as temporal:
            shutil.copyfileobj(entrada, temporal)
            temporal_path = Path(temporal.name)
    temporal_path.replace(origen)


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnostico seguro."""
    if isinstance(exc, ErrorDeBot):
        return exc
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return TargetUnavailableError(
            "timeout del sitio del organismo",
            diagnostic_code="mis_comprobantes_site_timeout",
        )
    texto = sin_secretos(f"{type(exc).__name__}", secretos)
    if "captcha" in type(exc).__name__.lower():
        return CaptchaUnsolvableError(f"desafio no resoluble: {texto}")
    if "browser" in type(exc).__name__.lower() or "playwright" in type(exc).__name__.lower():
        return BrowserCrashedError(f"navegador caido: {texto}")
    return TargetUnavailableError(
        f"falla del organismo: {texto}",
        diagnostic_code="mis_comprobantes_unclassified_exception",
    )


class MisComprobantesPlugin:
    """Mis Comprobantes de ARCA: consulta, solicitud e historial."""

    manifest = BotManifest(
        nombre="mis_comprobantes",
        version="3.0.0",
        operaciones=("consultar", "solicitar", "historial"),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="emitidos.csv",
                content_types=("text/csv",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="recibidos.csv",
                content_types=("text/csv",),
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
            "auth.afip.gob.ar",
            "www.afip.gob.ar",
            "portalcf.cloud.afip.gob.ar",
            "api.capmonster.cloud",
        ),
    )

    def __repr__(self) -> str:
        return "MisComprobantesPlugin(<redacted>)"

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, periodo y flags antes del navegador."""
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
                servicio = await sesion.open_service(SERVICIO_ARCA)
                await servicio.seleccionar_representado(entrada.representado_cuit)
                if operacion == "solicitar":
                    datos = await self._solicitar(servicio, entrada, runtime)
                    artefactos: list[dict[str, Any]] = []
                else:
                    datos, artefactos = await self._descargar(
                        servicio, entrada, runtime, operacion
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

    async def _solicitar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> dict[str, Any]:
        """Pide la consulta async y devuelve ids, nunca cookies (port V2)."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Solicitando consulta"
        )
        ids_consulta: dict[str, str] = {}
        for tipo in ("emitidos", "recibidos"):
            if not getattr(entrada, tipo):
                continue
            await runtime.cancellation.raise_if_cancelled()
            id_consulta = await servicio.solicitar_consulta(
                tipo=tipo,
                desde=entrada.fecha_desde,
                hasta=entrada.fecha_hasta,
            )
            ids_consulta[tipo] = str(id_consulta)
        return {
            "operacion": "solicitar",
            "representado_cuit": entrada.representado_cuit,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
            "ids_consulta": ids_consulta,
        }

    async def _descargar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime, operacion: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Descarga CSV a ``work_dir``, filtra por rango y sube por slot."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Descargando comprobantes"
        )
        datos: dict[str, Any] = {
            "operacion": operacion,
            "representado_cuit": entrada.representado_cuit,
            "fecha_desde": entrada.fecha_desde,
            "fecha_hasta": entrada.fecha_hasta,
        }
        artefactos: list[dict[str, Any]] = []
        for tipo, id_artefacto in (
            ("emitidos", ID_ARTEFACTO_EMITIDOS),
            ("recibidos", ID_ARTEFACTO_RECIBIDOS),
        ):
            if not getattr(entrada, tipo):
                continue
            await runtime.cancellation.raise_if_cancelled()
            base = nombre_base_archivo(
                entrada.representado_cuit,
                entrada.representado_nombre,
                entrada.fecha_desde,
                entrada.fecha_hasta,
                tipo,
            )
            crudo = runtime.artifact_store.resolve(f"{base}.crudo.csv")
            final = runtime.artifact_store.resolve(f"{base}.csv")
            await servicio.descargar_csv(
                tipo=tipo,
                destino=crudo,
                desde=entrada.fecha_desde,
                hasta=entrada.fecha_hasta,
            )
            materializar_csv_descargado(crudo)
            await runtime.event_sink.progress(
                phase="PROCESANDO", percent=65, message=f"Filtrando {tipo}"
            )
            filas = filtrar_csv_por_rango(
                crudo, final, entrada.fecha_desde, entrada.fecha_hasta
            )
            crudo.unlink(missing_ok=True)
            resumen: dict[str, Any] = {"filas": filas, "archivo": final.name}
            if getattr(entrada, "subir_csv", True):
                await runtime.event_sink.progress(
                    phase="SUBIENDO", percent=80, message=f"Subiendo {tipo}"
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        id_artefacto, final.name
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                resumen["sha256"] = referencia["sha256"]
                resumen["size_bytes"] = referencia["size_bytes"]
            if getattr(entrada, "incluir_json", True):
                resumen["muestra"] = self._muestra(final)
            datos[tipo] = resumen
        return datos, artefactos

    @staticmethod
    def _muestra(archivo: Path, limite: int = 5) -> list[dict[str, str]]:
        """Lee las primeras filas del CSV final para el JSON de respuesta."""
        with open(archivo, "r", encoding="utf-8", newline="") as fh:
            lector = csv.DictReader(fh)
            return [dict(fila) for _, fila in zip(range(limite), lector)]
