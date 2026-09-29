"""Plugin ``facturometro``: lectura de Monotributo sin archivos (worker V3).

Porta ``api-bots-mrbot-v2/app/bot/facturometro_bot.py`` (``bot_facturometro``)
al contrato worker V3. Cambios obligatorios respecto de V2:

- Sin ``SessionLocal``: la central persiste tras el callback.
- Sin ``tempfile``/``screenshot`` a MinIO: el bot es de solo lectura;
  deja una copia redactada del resultado en ``work_dir/resultado.json``
  para trazabilidad local (se borra con el workspace).
- Sin ``os.environ`` ni ``get_proxy_config()``: proxy y credenciales
  llegan por ``runtime`` desde el sobre sellado.
- Sin credenciales en logs: categoria + diagnostico redactado.

V2 lee ``#spanFacturometroMonto``, ``#spanFacturometroCategoriaTope`` y
``#spanFacturometroCategoria2`` con fallback al endpoint
``inicio.aspx/CalcularFacturacion``; el plugin delega esa lectura al
handle del servicio y solo normaliza el trio resultante.

La seccion ``service`` del sobre sellado (vía :meth:`configure`) solo
ajusta el nombre visible del servicio.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

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
from bot_worker.bots.facturometro.schema import ENTRADAS, esquema_entrada
from bot_worker.bots.registry import BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

SERVICIO_ARCA = "MONOTRIBUTO"
#: Portal que implementa ``leer_facturometro`` (``runtime/portals``).
PORTAL_ARCA = "facturometro"


def _normalizar_error(exc: BaseException, secretos: list[str]) -> ErrorDeBot:
    """Mapea excepciones del flujo a errores tipados con diagnostico seguro."""
    if isinstance(exc, ErrorDeBot):
        return exc
    texto = sin_secretos(f"{type(exc).__name__}", secretos)
    if "captcha" in type(exc).__name__.lower():
        return CaptchaUnsolvableError(f"desafio no resoluble: {texto}")
    if "browser" in type(exc).__name__.lower() or "playwright" in type(exc).__name__.lower():
        return BrowserCrashedError(f"navegador caido: {texto}")
    return TargetUnavailableError(f"falla del organismo: {texto}")


class FacturometroPlugin:
    """Lee monto, tope y categoria del facturometro de Monotributo."""

    manifest = BotManifest(
        nombre="facturometro",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(),
        timeout_por_defecto_seconds=600,
        requiere_credenciales_fiscales=True,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=1,
        idempotency_class="LECTURA",
        browser_instances_max=1,
        hosts_permitidos=(
            "www.afip.gob.ar",
        ),
    )

    def __init__(self, servicio: str | None = None) -> None:
        """Valor por defecto; la seccion sellada lo ajusta en ``configure``."""
        self._servicio = servicio or SERVICIO_ARCA

    def __repr__(self) -> str:
        return "FacturometroPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "FacturometroPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Clave conocida: ``servicio`` (nombre visible del servicio ARCA).
        Sin seccion rige el valor por defecto V2.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("servicio"):
            self._servicio = str(service["servicio"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion y CUIT antes del navegador."""
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
        """Lee el facturometro y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotError, BotResult

        from bot_worker.bots.errors import Categoria

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
                await runtime.event_sink.progress(
                    phase="CONSULTA", percent=45, message="Leyendo facturometro"
                )
                servicio = await sesion.open_service(
                    self._servicio, portal=PORTAL_ARCA
                )
                lectura = await servicio.leer_facturometro(
                    representado_cuit=entrada.representado_cuit,
                )
        except ErrorDeBot:
            raise
        except Exception as exc:
            raise _normalizar_error(exc, secretos) from exc

        await runtime.cancellation.raise_if_cancelled()
        if not isinstance(lectura, dict):
            return BotResult(
                result="ERROR",
                data={},
                errors=[
                    BotError(
                        category=Categoria.INTERNAL,
                        internal_diagnostic="lectura no es objeto",
                        retryable=False,
                    )
                ],
            )
        datos = {
            "operacion": operacion,
            "representado_cuit": entrada.representado_cuit,
            "monto": lectura.get("monto"),
            "tope": lectura.get("tope"),
            "categoria": lectura.get("categoria"),
        }
        copia = runtime.work_dir / "resultado.json"
        copia.write_text(
            json.dumps(datos, ensure_ascii=False, separators=(",", ":"))[:1_048_576],
            encoding="utf-8",
        )
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return BotResult(result="OK", data=datos)
