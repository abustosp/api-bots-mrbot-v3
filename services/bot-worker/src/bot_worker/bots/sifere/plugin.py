"""Plugin ``sifere``: retenciones y percepciones SIFERE (Convenio Multilateral).

Porta ``api-bots-mrbot/app/bot/sifere_bot.py`` (V1, ``bot_sifere`` y
``sifere_consultas``) al contrato S7. Cambios obligatorios respecto de V1:

- Sin ``SessionLocal`` ni escritura en base: la central persiste el
  resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni temporales sueltos: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya
  construido viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin DataFrames pandas ni openpyxl en el worker: los xlsx salen del
  escritor stdlib del portal y el resumen viaja como JSON plano.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory``
con la interfaz minima documentada abajo; el plugin nunca importa
Playwright directo. El objeto ``servicio`` expone:

- ``seleccionar_representado(cuit)``: elige el representado.
- ``consultar_sircar(metodo, jurisdiccion, periodo, destino_dir,
  representado_cuit, nombre_representado)``: descarga el Excel y el PDF
  de SIRCAR para un metodo (``retencion``/``percepcion``) y una
  jurisdiccion; devuelve ``{"archivos": [{"nombre", "tipo", "filas"}],
  "errores": [...]}``.
- ``consultar_consolidados(periodo, destino_dir, representado_cuit,
  nombre_representado)``: SIRCREB y SIRTAC del periodo; devuelve
  ``{"archivos": [nombre, ...], "errores": [...], "filas": {...}}``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Mapping

from bot_worker.bots.errors import (
    ArtifactUploadError,
    BrowserCrashedError,
    Categoria,
    CredentialsRejectedError,
    DeadlineExceededError,
    ErrorDeBot,
    InvalidInputError,
    TargetUnavailableError,
    sin_secretos,
)
from bot_worker.bots.sifere.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "CONVENIO MULTILATERAL – SIFERE WEB - CONSULTAS"
#: Familias de SIRCAR: un Excel y un PDF por metodo y jurisdiccion.
ID_ARTEFACTO_SIRCAR_XLSX = "sifere_sircar_xlsx"
ID_ARTEFACTO_SIRCAR_PDF = "sifere_sircar_pdf"
#: SIRCREB (descargas y tablas) y SIRTAC (tablas, jurisdicciones y
#: operaciones) del periodo.
ID_ARTEFACTO_CONSOLIDADO = "sifere_consolidado_xlsx"
#: Metodos de SIRCAR que recorre V1, en el mismo orden.
METODOS_SIRCAR = ("retencion", "percepcion")


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


class SiferePlugin:
    """SIFERE web de COMARB: retenciones/percepciones y consolidados."""

    manifest = BotManifest(
        nombre="sifere",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                # Un Excel por metodo (retencion/percepcion) y jurisdiccion:
                # sifere_sircar_xlsx_retencion_901, etc.
                nombre=ID_ARTEFACTO_SIRCAR_XLSX,
                content_types=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                # Un PDF por metodo y jurisdiccion:
                # sifere_sircar_pdf_retencion_901, etc.
                nombre=ID_ARTEFACTO_SIRCAR_PDF,
                content_types=("application/pdf",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                # SIRCREB (descargas y tablas) y SIRTAC (tablas, jurisdicciones
                # y operaciones) del periodo.
                nombre="sifere_consolidado",
                # Verificado en vivo (202401): las dos descargas de SIRCREB
                # llegan como PDF y TXT, ademas de los xlsx de tablas.
                content_types=(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "application/pdf",
                    "text/plain",
                ),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1800,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=2,
        idempotency_class="CONTINUACION",
        browser_instances_max=1,
        hosts_permitidos=(
            "app1.comarb.gob.ar",
            "www.afip.gob.ar",
        ),
    )

    def __init__(self, servicio_nombre: str | None = None) -> None:
        """Inyecta el nombre del servicio ARCA desde el sobre sellado."""
        self._servicio_nombre = servicio_nombre or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "SiferePlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "SiferePlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Sin seccion rige SIFERE web.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("sifere_servicio"):
            self._servicio_nombre = str(service["sifere_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, periodo y jurisdicciones antes de navegar."""
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
                servicio = await sesion.open_service(self._servicio_nombre, portal="sifere")
                await servicio.seleccionar_representado(entrada.representado_cuit)
                datos, artefactos, errores = await self._consultar(
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
        resultado = "OK" if not errores else "PARCIAL"
        return BotResult(
            result=resultado, data=datos, artifacts=artefactos, errors=errores
        )

    async def _consultar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]], list[Any]]:
        """Recorre SIRCAR por metodo y jurisdiccion, y sube Excel y PDF.

        V1 recorre ``retencion`` y ``percepcion`` y, dentro de cada metodo,
        cada jurisdiccion del padron; cada descarga que falla queda en
        ``errores`` sin cortar la corrida.
        """
        from bot_worker.runtime.context import BotError

        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Consultando jurisdicciones"
        )
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": entrada.representado_cuit,
            "periodo": entrada.periodo,
            "jurisdicciones": [],
        }
        artefactos: list[dict[str, Any]] = []
        errores: list[Any] = []
        subir = bool(getattr(entrada, "subir", True))
        incluir_json = bool(getattr(entrada, "incluir_json", True))
        pares = [
            (metodo, codigo)
            for metodo in METODOS_SIRCAR
            for codigo in entrada.jurisdicciones
        ]
        for indice, (metodo, codigo) in enumerate(pares):
            await runtime.cancellation.raise_if_cancelled()
            resumen = await servicio.consultar_sircar(
                metodo=metodo,
                jurisdiccion=codigo,
                periodo=entrada.periodo,
                destino_dir=runtime.artifact_store.resolve("."),
                representado_cuit=entrada.representado_cuit,
                nombre_representado=entrada.representado_nombre,
            )
            resumen = resumen if isinstance(resumen, dict) else {}
            fila: dict[str, Any] = {
                "jurisdiccion": codigo,
                "metodo": metodo,
                "etiqueta": resumen.get("etiqueta")
                or ("Retenciones" if metodo == "retencion" else "Percepciones"),
                "archivos": [],
            }
            for archivo in resumen.get("archivos", []) or []:
                if isinstance(archivo, dict):
                    nombre = str(archivo.get("nombre") or "")
                    tipo = str(archivo.get("tipo") or "xlsx").lower()
                else:
                    nombre, tipo = str(archivo), "xlsx"
                if not nombre:
                    continue
                item: dict[str, Any] = {"archivo": nombre, "tipo": tipo}
                if subir:
                    familia = (
                        ID_ARTEFACTO_SIRCAR_PDF
                        if tipo == "pdf"
                        else ID_ARTEFACTO_SIRCAR_XLSX
                    )
                    try:
                        referencia = await runtime.artifact_store.upload(
                            f"{familia}_{metodo}_{codigo}", nombre
                        )
                    except ValueError as exc:
                        raise ArtifactUploadError(str(exc)) from exc
                    artefactos.append(referencia)
                    item["sha256"] = referencia["sha256"]
                    item["size_bytes"] = referencia["size_bytes"]
                fila["archivos"].append(item)
            # Descargas individuales fallidas: no fatales (V1 fallaba en
            # silencio sin perder el resto de la corrida).
            for mensaje in resumen.get("errores", []) or []:
                errores.append(
                    BotError(
                        category=Categoria.TARGET_UNAVAILABLE,
                        internal_diagnostic=f"SIRCAR {metodo} {codigo}: {mensaje}",
                        retryable=False,
                    )
                )
            if incluir_json:
                datos["jurisdicciones"].append(fila)
            if subir and pares:
                await runtime.event_sink.progress(
                    phase="SUBIENDO",
                    percent=45 + int(35 * (indice + 1) / len(pares)),
                    message=f"Subiendo {codigo}",
                )
        if not incluir_json:
            datos.pop("jurisdicciones", None)
        if getattr(entrada, "consolidados", True):
            await self._consolidados(servicio, entrada, runtime, datos, artefactos, errores)
        return datos, artefactos, errores

    async def _consolidados(
        self,
        servicio: Any,
        entrada: Any,
        runtime: BotRuntime,
        datos: dict[str, Any],
        artefactos: list[dict[str, Any]],
        errores: list[Any],
    ) -> None:
        """SIRCREB (descargas y tablas) y SIRTAC (tablas por separado).

        Como V1, un fallo no tumba la corrida: las jurisdicciones ya subidas se
        conservan y queda un error no fatal. Las descargas individuales que
        fallan vienen en ``resumen["errores"]`` y se agregan una por una.
        """
        from bot_worker.runtime.context import BotError

        await runtime.event_sink.progress(
            phase="CONSULTA", percent=85, message="Descargando consolidados SIFERE"
        )
        try:
            resumen = await servicio.consultar_consolidados(
                periodo=entrada.periodo,
                destino_dir=runtime.artifact_store.resolve("."),
                representado_cuit=entrada.representado_cuit,
                nombre_representado=entrada.representado_nombre,
            )
        except ErrorDeBot as exc:
            errores.append(
                BotError(
                    category=Categoria.TARGET_UNAVAILABLE,
                    internal_diagnostic=(
                        "consolidados SIFERE: "
                        f"{getattr(exc, 'diagnostic_code', None) or exc.diagnostico}"
                    ),
                    retryable=False,
                )
            )
            return
        resumen = resumen if isinstance(resumen, dict) else {}
        for mensaje in resumen.get("errores", []) or []:
            errores.append(
                BotError(
                    category=Categoria.TARGET_UNAVAILABLE,
                    internal_diagnostic=f"consolidados SIFERE: {mensaje}",
                    retryable=False,
                )
            )
        consolidados: list[dict[str, Any]] = []
        for indice, nombre in enumerate(resumen.get("archivos", []) or [], start=1):
            fila: dict[str, Any] = {"archivo": nombre}
            if getattr(entrada, "subir", True):
                try:
                    referencia = await runtime.artifact_store.upload(
                        f"{ID_ARTEFACTO_CONSOLIDADO}_{indice}", nombre
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                fila["sha256"] = referencia["sha256"]
                fila["size_bytes"] = referencia["size_bytes"]
            consolidados.append(fila)
        if getattr(entrada, "incluir_json", True):
            datos["consolidados"] = consolidados
