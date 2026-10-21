"""Las copias de los scripts _logger del worker no deben desviarse de la raíz bots_dev."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
HERRAMIENTA = REPO / "tools" / "sync_bots_dev_loggers.py"

pytestmark = pytest.mark.skipif(
    not HERRAMIENTA.is_file() or not (REPO / "bots_dev").is_dir(),
    reason="fuera del monorepo (p. ej. dentro de la imagen) no hay raíz bots_dev",
)


def _herramienta():
    spec = importlib.util.spec_from_file_location("sync_bots_dev_loggers", HERRAMIENTA)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_copias_sincronizadas_con_la_raiz() -> None:
    assert _herramienta().desvios() == [], (
        "Ejecute tools/sync_bots_dev_loggers.py para regenerar las copias del worker"
    )


def test_el_manifiesto_cubre_los_scripts_de_la_variante_logger() -> None:
    from bot_worker.runtime.logger_script import CARPETA, SCRIPTS

    manifiesto = _herramienta().MANIFIESTO
    for bot, relativo in SCRIPTS.items():
        carpeta, nombre = relativo.split("/")
        assert carpeta == bot, f"la carpeta de {bot} debe llamarse como el bot registrado"
        assert nombre in manifiesto[bot]
        assert (CARPETA / relativo).is_file()
