"""Contract checks for the worker's developer payload examples.

These tests call only each plugin's local ``validate`` method. They never
launch a browser, call an external service, or execute fiscal operations.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from bot_worker.bots.registry import REGISTRY

BOTS_DEV = Path(__file__).parents[1] / "src" / "bot_worker" / "bots_dev"


def test_bots_dev_directories_match_registered_plugins() -> None:
    fixture_bots = {path.name for path in BOTS_DEV.iterdir() if path.is_dir()}
    assert fixture_bots == set(REGISTRY)


@pytest.mark.parametrize("bot_name", sorted(REGISTRY))
def test_registered_plugin_has_valid_dev_payload_example(bot_name: str) -> None:
    fixture_dir = BOTS_DEV / bot_name
    note_path = fixture_dir / "NOTA.md"
    payload_path = fixture_dir / "payload_ejemplo.json"
    assert note_path.is_file(), f"Falta bots_dev/{bot_name}/NOTA.md"
    assert payload_path.is_file(), f"Falta bots_dev/{bot_name}/payload_ejemplo.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))

    # Validar el contrato es offline y no implica ejecutar el plugin.
    asyncio.run(REGISTRY[bot_name].validate(payload))
