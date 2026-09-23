"""Plugin ``apoc``: consulta local de condicion apocrifa por CUIT.

Porta ``api-bots-mrbot-v2/app/bot/apoc.py`` al contrato S7:

- Sin ``SessionLocal`` ni base de datos (W-1).
- Sin leer variables de entorno ni archivos del repo: la base APOC
  (``base.txt`` en V2) llega provisionada por la central en la seccion
  ``service`` del sobre sellado como texto inline (``apoc_base_text``);
  la logica de negocio nunca toca el filesystem salvo ``work_dir``.
- Sin navegador ni credenciales: operacion de lectura pura.
- Solo escribe descendientes de ``runtime.work_dir`` (copia redactada
  del resultado para trazabilidad local; se borra con el workspace).
- Errores por categoria: entrada invalida falla en ``validate``.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from bot_worker.bots.apoc.schema import ApocConsultarInput, esquema_entrada
from bot_worker.bots.errors import InvalidInputError
from bot_worker.bots.registry import BotManifest

try:
    from bot_worker.runtime.context import BotResult, BotRuntime
except ImportError:  # pragma: no cover - solo para tipado estatico
    BotResult = Any  # type: ignore[assignment,misc]
    BotRuntime = Any  # type: ignore[assignment,misc]


def buscar_en_base(cuit: str, base_text: str) -> dict[str, Any]:
    """Busca un CUIT en el texto de la base (port puro de V2).

    Lineas ``CUIT, Fecha Condicion, Fecha Publicacion[, ...]``; se
    ignoran vacias, comentarios ``#`` y lineas con menos de 3 campos.
    Funcion pura, sin red ni secretos.
    """
    for linea in base_text.splitlines():
        if not linea.strip() or linea.startswith("#"):
            continue
        partes = linea.strip().split(",")
        if len(partes) < 3:
            continue
        if partes[0].strip() == cuit:
            return {
                "cuit": partes[0].strip(),
                "fecha_condicion": partes[1].strip(),
                "fecha_publicacion": partes[2].strip(),
                "apoc": True,
            }
    return {
        "cuit": cuit,
        "fecha_condicion": None,
        "fecha_publicacion": None,
        "apoc": False,
    }


class ApocPlugin:
    """Condicion de apocrifo (AFIP): busqueda local sin navegador."""

    manifest = BotManifest(
        nombre="apoc",
        version="3.0.0",
        operaciones=("consultar",),
        esquema_entrada=esquema_entrada(),
        artefactos_produce=(),
        timeout_por_defecto_seconds=60,
        requiere_credenciales_fiscales=False,
        requiere_proxy=False,
        requiere_captcha=False,
        costo_creditos_sugerido=1,
        idempotency_class="LECTURA",
        browser_instances_max=0,
        hosts_permitidos=(),
    )

    def __init__(self, base_text: str | None = None) -> None:
        """Inyecta el contenido de la base desde el sobre sellado."""
        self._base_text = base_text

    def __repr__(self) -> str:
        return "ApocPlugin(<redacted>)"

    def configure(self, service: dict[str, Any] | None) -> "ApocPlugin":
        """Aplica la seccion ``service`` del sobre sellado (por job).

        Se invoca sobre una copia del plugin, nunca sobre la instancia
        del registro compartido. Clave: ``apoc_base_text``.
        """
        service = service if isinstance(service, dict) else {}
        if service.get("apoc_base_text"):
            self._base_text = str(service["apoc_base_text"])
        return self

    async def validate(self, payload: Mapping[str, Any]) -> Any:
        """Valida operacion y CUIT antes de cualquier computo."""
        if not isinstance(payload, Mapping):
            raise InvalidInputError("payload debe ser objeto")
        operacion = payload.get("operacion", "consultar")
        if operacion != "consultar":
            raise InvalidInputError(f"operacion desconocida: {operacion}")
        try:
            datos = {k: v for k, v in payload.items() if k != "operacion"}
            return ("consultar", ApocConsultarInput.model_validate(datos))
        except ValueError as exc:
            raise InvalidInputError(f"entrada invalida: {exc}") from exc

    async def execute(self, payload: Any, runtime: BotRuntime) -> BotResult:
        """Busca el CUIT en la base provisionada y retorna ``BotResult``."""
        from bot_worker.runtime.context import BotResult

        _, entrada = payload
        await runtime.cancellation.raise_if_cancelled()
        await runtime.event_sink.progress(
            phase="CONSULTA", percent=45, message="Buscando CUIT en base APOC"
        )
        advertencias: list[str] = []
        if self._base_text:
            datos = buscar_en_base(entrada.cuit, self._base_text)
        else:
            datos = {
                "cuit": entrada.cuit,
                "fecha_condicion": None,
                "fecha_publicacion": None,
                "apoc": False,
            }
            advertencias.append("base APOC no provisionada en el sobre sellado")
        copia = runtime.work_dir / "resultado.json"
        copia.write_text(
            json.dumps(datos, ensure_ascii=False, separators=(",", ":"))[:1_048_576],
            encoding="utf-8",
        )
        await runtime.event_sink.progress(
            phase="FINALIZANDO", percent=90, message="Normalizando resultado"
        )
        return BotResult(result="OK", data=datos, warnings=advertencias)
