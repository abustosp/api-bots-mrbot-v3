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
- Sin rutas de cliente: los TXT/CSV llegan como base64 validados en el
  esquema; aca solo se decodifican (port de ``_validar_archivo``).
- Sin credenciales en logs: el error es categoria + diagnostico
  redactado (``sin_secretos``); la clave fiscal nunca se interpola.

Efecto de escritura: idempotencia ``CARGA``; cada seccion informa su
estado para reintentos por seccion independiente como en V2.
"""

from __future__ import annotations

import asyncio
import base64
import re
from typing import Any, Mapping

from bot_worker.bots.carga_portal_iva.schema import CAMPOS_ARCHIVO, esquema_entrada
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
        """Decodifica los base64 a ``work_dir`` (port de ``_validar_archivo``)."""
        rutas: dict[str, str] = {}
        for campo in CAMPOS_ARCHIVO:
            b64 = getattr(entrada, campo)
            if not b64:
                continue
            contenido = base64.b64decode(str(b64), validate=True)
            if not contenido.strip():
                raise InvalidInputError(f"archivo sin contenido legible: {campo}")
            nombre = f"{campo.replace('_b64', '')}.txt"
            destino = runtime.artifact_store.resolve(nombre)
            destino.write_bytes(contenido)
            rutas[campo] = nombre
        return rutas

    async def _importar_txts(
        self, pagina: Any, libro: str, cbte: str, alicuota: str, runtime: BotRuntime
    ) -> str:
        """Importa un par comprobantes+alicuotas (port de ``_importar_txts``).

        Sube ambos archivos y dispara la importacion una sola vez, con
        reintentos por seccion independiente como en V2.
        """
        ultimo_error = "importacion no intentada"
        for intento in range(1, MAX_REINTENTOS_SECCION + 1):
            await runtime.cancellation.raise_if_cancelled()
            try:
                await pagina.get_by_role(
                    "link", name=re.compile(r"Importar\s+(?:desde\s+)?Archivos?", re.I)
                ).first.click()
                entradas = pagina.locator("input[type='file']")
                await entradas.nth(0).set_input_files(cbte)
                await entradas.nth(1).set_input_files(alicuota)
                await pagina.get_by_role(
                    "button", name=re.compile(r"Importar", re.I)
                ).first.click()
                await pagina.wait_for_timeout(5_000)
                return f"{libro}: importado (intento {intento})"
            except Exception as exc:  # noqa: BLE001 - se reintenta por seccion
                ultimo_error = type(exc).__name__
                await pagina.wait_for_timeout(3_000)
        raise TargetUnavailableError(f"{libro}: {ultimo_error}")

    async def _cargar_apertura_csv(
        self, pagina: Any, seccion: str, csv_nombre: str, runtime: BotRuntime
    ) -> str:
        """Carga un CSV de apertura (port de ``cargar_apertura_csv``)."""
        await runtime.cancellation.raise_if_cancelled()
        await pagina.get_by_role(
            "link", name=re.compile(rf"{re.escape(seccion)}", re.I)
        ).first.click()
        entrada_archivo = pagina.locator("input[type='file']").first
        await entrada_archivo.set_input_files(csv_nombre)
        await pagina.get_by_role(
            "button", name=re.compile(r"Cargar|Importar|Confirmar", re.I)
        ).first.click()
        await pagina.wait_for_timeout(5_000)
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
        total_cf = (
            calcular_total_cf_csv(base64.b64decode(str(entrada.csv_cf_b64)))
            if entrada.csv_cf_b64
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
                pagina = await sesion.open_service(self._servicio)
                await runtime.event_sink.progress(
                    phase="CONSULTA", percent=45, message="Cargando comprobantes"
                )
                if rutas.get("liv_cbte_b64"):
                    resultados["secciones"]["ventas_txt"] = await self._importar_txts(
                        pagina, "ventas", rutas["liv_cbte_b64"],
                        rutas["liv_alicuota_b64"], runtime,
                    )
                if rutas.get("lic_cbte_b64"):
                    resultados["secciones"]["compras_txt"] = await self._importar_txts(
                        pagina, "compras", rutas["lic_cbte_b64"],
                        rutas["lic_alicuota_b64"], runtime,
                    )
                await runtime.event_sink.progress(
                    phase="PROCESANDO", percent=65, message="Cargando aperturas"
                )
                for seccion, campo in (
                    ("CF", "csv_cf_b64"),
                    ("CF Restitucion", "csv_cf_restitucion_b64"),
                    ("DF", "csv_df_b64"),
                    ("DF Restitucion", "csv_df_restitucion_b64"),
                ):
                    if rutas.get(campo):
                        resultados["secciones"][seccion] = (
                            await self._cargar_apertura_csv(
                                pagina, seccion, rutas[campo], runtime
                            )
                        )
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return BotResult(result="OK", data=resultados)
