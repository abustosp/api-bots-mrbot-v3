"""Plugin ``siper``: detalle e historia de categorias SIPER.

Porta ``api-bots-mrbot-v2/app/bot/siper_bot.py`` (``bot_siper``) al
contrato S7. Reemplaza al stub F2 de ``registry.py`` (ola 3): cuando
la central lo registre, este modulo aporta el port real. Cambios
obligatorios respecto de V2:

- Sin ``SessionLocal`` ni escritura en base: la central persiste el
  resultado tras el callback idempotente.
- Sin ``os.getcwd()`` ni temporales sueltos: todo archivo vive bajo
  ``runtime.work_dir`` y se valida con ``artifact_store.resolve``.
- Sin ``os.environ`` ni ``get_proxy_config()``: el proxy ya
  construido viaja en ``runtime.proxy`` hacia ``browser_factory``.
- Sin ``subir_archivo_a_minio`` con credenciales: la subida usa
  ``artifact_store.upload`` con slots prefirmados del sobre.
- Sin b64 de capturas en el resultado: los PNG viajan como
  artefactos con sha256; el JSON lleva solo metadatos.
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

La sesion de navegador se obtiene de ``runtime.browser_factory``
con la interfaz minima documentada abajo; el plugin nunca importa
Playwright directo. El objeto ``servicio`` expone:

- ``seleccionar_representado(cuit)``: elige el representado (o el
  propio cuando coincide con el login).
- ``capturar_vista(vista, destino)``: guarda el PNG de ``vista``
  (``detalle`` | ``categorias``) en ``destino`` y retorna el
  resumen (``{"ancho": int, "alto": int}``).
"""

from __future__ import annotations

import asyncio
import re
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
from bot_worker.bots.siper.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import ArtifactSpec, BotManifest

#: Punto de entrada del padrón PUC (SIPER); V2 entraba directo por URL.
URL_PADRON = (
    "https://seti.afip.gob.ar/padron-puc-consulta-internet/"
    "ResponsiveIndexInternetAction.do"
)

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_SIPER = "SIPER"
ID_ARTEFACTO_DETALLE = "siper_detalle_png"
ID_ARTEFACTO_CATEGORIAS = "siper_categorias_png"
VISTAS = ("detalle", "categorias")


def nombre_captura_siper(cuit: str, vista: str) -> str:
    """Replica el patron de nombres V2 ``'{fin} - SIPER - ...'``."""
    digitos = re.sub(r"\D", "", cuit or "")
    fin = digitos[-1:] if digitos else "X"
    return f"{fin} - SIPER - {vista} - {digitos}.png"


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


class SiperRealPlugin:
    """SIPER de ARCA: detalle e historia de categorias por CUIT.

    Se llama ``SiperRealPlugin`` para no colisionar con el stub F2
    ``SiperPlugin`` de ``registry.py``; la central lo registrara en
    la ola 3 bajo el nombre canonico ``siper``.
    """

    manifest = BotManifest(
        nombre="siper",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(
            ArtifactSpec(
                nombre="siper_detalle.png",
                content_types=("image/png",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
            ArtifactSpec(
                nombre="siper_categorias.png",
                content_types=("image/png",),
                max_bytes=52_428_800,
                obligatorio=False,
            ),
        ),
        timeout_por_defecto_seconds=1200,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=1,
        idempotency_class="LECTURA",
        browser_instances_max=1,
        hosts_permitidos=(
            "seti.afip.gob.ar",
            "www.afip.gob.ar",
        ),
    )

    def __init__(self, servicio_nombre: str | None = None) -> None:
        """Inyecta el nombre del servicio SIPER desde el sobre sellado."""
        self._servicio_nombre = servicio_nombre or SERVICIO_SIPER

    def __repr__(self) -> str:
        return "SiperRealPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "SiperRealPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Sin seccion rige SIPER.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("siper_servicio"):
            self._servicio_nombre = str(service["siper_servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion, CUIT, vistas y salidas antes del navegador."""
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
                # V2 entraba al padrón por URL (login con system=padron-puc-...);
                # el servicio no siempre figura en el catálogo de Clave Fiscal.
                servicio = await sesion.open_service(
                    self._servicio_nombre,
                    url=URL_PADRON,
                    portal="siper",
                )
                await servicio.seleccionar_representado(entrada.representado_cuit)
                datos, artefactos = await self._capturar(
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

    async def _capturar(
        self, servicio: Any, entrada: Any, runtime: BotRuntime
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Captura las vistas pedidas y las sube por slot prefirmado."""
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Capturando vistas SIPER"
        )
        vistas: list[tuple[str, str]] = []
        if getattr(entrada, "incluir_detalle", True):
            vistas.append(("detalle", ID_ARTEFACTO_DETALLE))
        if getattr(entrada, "incluir_categorias", True):
            vistas.append(("categorias", ID_ARTEFACTO_CATEGORIAS))
        datos: dict[str, Any] = {
            "operacion": "consultar",
            "representado_cuit": entrada.representado_cuit,
            "vistas": [],
        }
        artefactos: list[dict[str, Any]] = []
        for vista, artifact_id in vistas:
            await runtime.cancellation.raise_if_cancelled()
            nombre = nombre_captura_siper(entrada.representado_cuit, vista)
            destino = runtime.artifact_store.resolve(nombre)
            resumen = await servicio.capturar_vista(
                vista=vista,
                destino=destino,
                representado_cuit=entrada.representado_cuit,
            )
            fila: dict[str, Any] = {
                "vista": vista,
                "archivo": destino.name,
                "ancho": int((resumen or {}).get("ancho", 0)),
                "alto": int((resumen or {}).get("alto", 0)),
            }
            if getattr(entrada, "subir", True):
                await runtime.event_sink.progress(
                    phase="SUBIENDO", percent=80, message=f"Subiendo {vista}"
                )
                try:
                    referencia = await runtime.artifact_store.upload(
                        artifact_id, destino.name
                    )
                except ValueError as exc:
                    raise ArtifactUploadError(str(exc)) from exc
                artefactos.append(referencia)
                fila["sha256"] = referencia["sha256"]
                fila["size_bytes"] = referencia["size_bytes"]
            if getattr(entrada, "incluir_json", True):
                datos["vistas"].append(fila)
        if not getattr(entrada, "incluir_json", True):
            datos.pop("vistas", None)
        return datos, artefactos
