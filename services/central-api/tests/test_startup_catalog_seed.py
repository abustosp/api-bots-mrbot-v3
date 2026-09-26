"""El arranque de la central siembra el catálogo cuando hay base configurada."""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api import main as central_main  # noqa: E402
from central_api.settings import get_settings  # noqa: E402


def test_lifespan_siembra_catalogo_con_base(monkeypatch) -> None:
    llamadas: list[int] = []

    async def fake_seed() -> tuple[int, int]:
        llamadas.append(1)
        return 32, 42

    async def fake_cerrar() -> None:
        return None

    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(central_main, "db_configurado", lambda: True)
    monkeypatch.setattr(central_main, "cerrar_motor", fake_cerrar)
    monkeypatch.setattr("central_api.migrate.seed_catalog_async", fake_seed)
    try:
        with TestClient(central_main.create_app()) as cliente:
            assert cliente.get("/health").status_code == 200
        assert llamadas == [1]
    finally:
        get_settings.cache_clear()


def test_lifespan_no_bloquea_si_el_seed_falla(monkeypatch) -> None:
    async def seed_roto() -> tuple[int, int]:
        raise RuntimeError("esquema sin migrar")

    async def fake_cerrar() -> None:
        return None

    monkeypatch.setenv("SCHEDULER_ENABLED", "false")
    get_settings.cache_clear()
    monkeypatch.setattr(central_main, "db_configurado", lambda: True)
    monkeypatch.setattr(central_main, "cerrar_motor", fake_cerrar)
    monkeypatch.setattr("central_api.migrate.seed_catalog_async", seed_roto)
    try:
        with TestClient(central_main.create_app()) as cliente:
            assert cliente.get("/health").status_code == 200
    finally:
        get_settings.cache_clear()
