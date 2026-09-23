"""Plugin ``consulta_cuit``: piloto sin navegador (ola 1, plan 07).

Porta ``api-bots-mrbot-v2/app/bot/consulta_cuit.py`` al contrato S7:

- Sin ``SessionLocal`` ni imports de base de datos (W-1).
- Sin leer variables de entorno en la logica de negocio: las URL y las
  credenciales del servicio de constancias se inyectan por constructor
  desde el sobre sellado (RSA+Fernet) que abre el worker; si no vienen,
  se llama al endpoint publico sin parametros de auth, igual que V2
  cuando el ambiente no los tenia.
- Solo escribe descendientes de ``runtime.work_dir`` (copia redactada
  del resultado para trazabilidad local; se borra con el workspace).
- Nunca registra credenciales: el diagnostico de error lleva solo
  categoria + diagnostico redactado, sin usuario, api_key ni cuerpo.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

import httpx

from bot_worker.bots.consulta_cuit.schema import (
    ConsultaCuitInput,
    ConsultaCuitMasivaInput,
    esquema_entrada,
)
from bot_worker.bots.errors import (
    Categoria,
    InvalidInputError,
    TargetUnavailableError,
    sin_secretos,
)
from bot_worker.bots.registry import ArtifactSpec, BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]

URL_SIMPLE = "https://api-constancias-de-inscripcion.mrbot.com.ar/consulta_constancia/"
URL_MASIVA = (
    "https://api-constancias-de-inscripcion.mrbot.com.ar/consulta_constancia_masiva/"
)


class ConsultaCuitPlugin:
    """Consulta de constancias de inscripcion por CUIT, sin navegador."""

    manifest = BotManifest(
        nombre="consulta_cuit",
        version="3.0.0",
        operaciones=("consultar", "consultar_masivo"),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(),
        timeout_por_defecto_seconds=120,
        requiere_credenciales_fiscales=False,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=1,
        idempotency_class="LECTURA",
        browser_instances_max=0,
        hosts_permitidos=(
            "api-constancias-de-inscripcion.mrbot.com.ar",
        ),
    )

    def __init__(
        self,
        base_url: str | None = None,
        masiva_url: str | None = None,
        servicio_usuario: str | None = None,
        servicio_api_key: str | None = None,
    ) -> None:
        """Inyecta endpoint y auth del servicio desde el sobre sellado.

        Sin inyección rige el endpoint público por defecto. La lógica de
        negocio no lee el entorno: el worker no tiene variables y todo
        llega provisionado por la central en cada asignación.
        """
        base_url = base_url or URL_SIMPLE
        masiva_url = masiva_url or URL_MASIVA
        self._base_url = base_url
        self._masiva_url = masiva_url
        self._servicio_usuario = servicio_usuario
        self._servicio_api_key = servicio_api_key

    def __repr__(self) -> str:
        return "ConsultaCuitPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "ConsultaCuitPlugin":
        """Aplica la sección ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia del
        registro compartido. Sin sección, rige el endpoint público.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("cuit_base_url"):
            self._base_url = str(service["cuit_base_url"])
        if service.get("cuit_masiva_url"):
            self._masiva_url = str(service["cuit_masiva_url"])
        if service.get("cuit_usuario"):
            self._servicio_usuario = str(service["cuit_usuario"])
        if service.get("cuit_api_key"):
            self._servicio_api_key = str(service["cuit_api_key"])
        return self

    def _parametros_auth(self) -> dict[str, str]:
        """Parametros de auth solo si el sobre sellado los proveyo."""
        params: dict[str, str] = {}
        if self._servicio_usuario:
            params["usuario"] = self._servicio_usuario
        if self._servicio_api_key:
            params["api_key"] = self._servicio_api_key
        return params

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion y CUIT antes de cualquier llamada de red."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = payload.get("operacion", "consultar")
        datos = {k: v for k, v in payload.items() if k != "operacion"}
        try:
            if operacion == "consultar_masivo":
                return ("consultar_masivo", ConsultaCuitMasivaInput.model_validate(datos))
            if operacion == "consultar":
                return ("consultar", ConsultaCuitInput.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc
        raise InvalidInputError(f"operacion desconocida: {operacion}")

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Ejecuta la consulta y retorna ``BotResult`` tipado."""
        from bot_worker.runtime.context import BotError, BotResult

        operacion, entrada = payload
        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Consultando constancia"
        )
        timeout_s = max(5.0, runtime.deadline.remaining_seconds(maximum=60.0))
        secretos = [self._servicio_usuario, self._servicio_api_key]
        try:
            async with httpx.AsyncClient(timeout=timeout_s) as cliente:
                if operacion == "consultar_masivo":
                    assert isinstance(entrada, ConsultaCuitMasivaInput)
                    respuesta = await cliente.post(
                        self._masiva_url,
                        params=self._parametros_auth(),
                        json={"cuits": entrada.cuits},
                    )
                else:
                    assert isinstance(entrada, ConsultaCuitInput)
                    respuesta = await cliente.get(
                        self._base_url,
                        params={"cuit": entrada.cuit, **self._parametros_auth()},
                    )
                respuesta.raise_for_status()
                datos = respuesta.json()
        except InvalidInputError:
            raise
        except httpx.TimeoutException as exc:
            raise TargetUnavailableError(
                sin_secretos(f"timeout del servicio de constancias: {type(exc).__name__}")
            ) from exc
        except httpx.HTTPStatusError as exc:
            codigo = exc.response.status_code
            raise TargetUnavailableError(
                f"servicio de constancias devolvio http {codigo}"
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise TargetUnavailableError(
                sin_secretos(
                    f"falla de red o respuesta invalida: {type(exc).__name__}",
                    secretos,
                )
            ) from exc

        await runtime.cancellation.raise_if_cancelled()
        copia = runtime.work_dir / "resultado.json"
        copia.write_text(
            json.dumps(datos, ensure_ascii=False, separators=(",", ":"))[:1_048_576],
            encoding="utf-8",
        )
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        if isinstance(datos, dict):
            return BotResult(result="OK", data=datos)
        return BotResult(
            result="ERROR",
            data={},
            errors=[
                BotError(
                    category=Categoria.INTERNAL,
                    internal_diagnostic="respuesta no es objeto JSON",
                    retryable=False,
                )
            ],
        )
