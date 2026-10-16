"""Estado editable del autoscaler: política definida en el panel e instancias manuales.

Los valores viven en memoria y, con PostgreSQL configurado, se persisten en
la tabla clave/valor ``autoscale_state`` (migración 0026) para sobrevivir a
reinicios de la central. Sin base se usa solo la memoria.

``POLICY_OVERRIDES`` pisa los valores de ``AUTOSCALE_*`` del entorno. Solo se
admiten las claves de ``POLICY_FIELDS``, con rango validado.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from central_api.db import db_configurado, nueva_sesion

log = logging.getLogger(__name__)

# clave del panel -> (atributo de Settings, mínimo, máximo)
POLICY_FIELDS: dict[str, tuple[str, int, int]] = {
    "queue_threshold": ("autoscale_queue_threshold", 0, 100_000),
    "jobs_per_worker": ("autoscale_jobs_per_worker", 0, 1_000),
    "max_instances": ("autoscale_max_instances", 0, 100),
    "max_total_workers": ("autoscale_max_total_workers", 0, 1_000),
    "step": ("autoscale_step", 0, 100),
    "up_after_seconds": ("autoscale_up_after_seconds", 0, 3_600),
    "cooldown_seconds": ("autoscale_cooldown_seconds", 0, 3_600),
    "idle_seconds": ("autoscale_idle_seconds", 0, 86_400),
}

POLICY_OVERRIDES: dict[str, int] = {}
MANUAL_IDS: set[str] = set()


def validate_policy(values: dict[str, Any]) -> dict[str, int]:
    """Valida claves y rangos; lanza ``ValueError`` con un mensaje legible."""
    clean: dict[str, int] = {}
    for key, raw in values.items():
        if key not in POLICY_FIELDS:
            raise ValueError(f"parámetro de política desconocido: {key}")
        if isinstance(raw, bool) or raw is None:
            raise ValueError(f"{key} debe ser un entero")
        try:
            number = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} debe ser un entero") from exc
        _attr, low, high = POLICY_FIELDS[key]
        if not low <= number <= high:
            raise ValueError(f"{key} debe estar entre {low} y {high}")
        clean[key] = number
    return clean


def settings_updates() -> dict[str, int]:
    """Overrides traducidos a nombres de atributos de ``Settings``."""
    return {POLICY_FIELDS[key][0]: value for key, value in POLICY_OVERRIDES.items()}


def effective_settings():
    """``Settings`` con los overrides del panel aplicados (copia, sin mutar)."""
    from central_api.settings import get_settings

    settings = get_settings()
    updates = settings_updates()
    return settings.model_copy(update=updates) if updates else settings


async def _save(key: str, value: Any) -> None:
    if not db_configurado():
        return
    from sqlalchemy import text

    payload = json.dumps(value, sort_keys=True)
    try:
        async with nueva_sesion() as sesion:
            await sesion.execute(
                text(
                    "INSERT INTO autoscale_state (key, value) VALUES (:k, :v) "
                    "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
                    "updated_at = CURRENT_TIMESTAMP"
                ),
                {"k": key, "v": payload},
            )
            await sesion.commit()
    except Exception as exc:  # noqa: BLE001 - la memoria sigue siendo válida
        log.warning("no se pudo persistir autoscale_state[%s]: %s", key, type(exc).__name__)


async def load_state() -> None:
    """Carga política e instancias manuales persistidas (arranque)."""
    if not db_configurado():
        return
    from sqlalchemy import text

    async with nueva_sesion() as sesion:
        rows = (await sesion.execute(text("SELECT key, value FROM autoscale_state"))).all()
    for key, value in rows:
        try:
            data = json.loads(value)
        except ValueError:
            continue
        if key == "policy" and isinstance(data, dict):
            try:
                POLICY_OVERRIDES.clear()
                POLICY_OVERRIDES.update(validate_policy(data))
            except ValueError:
                log.warning("política persistida inválida, se ignora")
        elif key == "manual_instances" and isinstance(data, list):
            MANUAL_IDS.clear()
            MANUAL_IDS.update(str(item) for item in data)


async def save_policy() -> None:
    await _save("policy", dict(POLICY_OVERRIDES))


async def save_manual() -> None:
    await _save("manual_instances", sorted(MANUAL_IDS))
