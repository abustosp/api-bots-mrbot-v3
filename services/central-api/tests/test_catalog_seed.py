"""Semilla del catálogo persistido desde el código (revisión de despliegue).

Sin filas en ``bots``/``bot_operations`` la FK ``fk_jobs_operation``
rechaza toda creación con 503; este test fija el mapeo código → costos y
clase de efecto que usa el seed de ``migrate``.
"""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.api.bots import (  # noqa: E402
    CATALOGUE,
    OPERATIONS,
    catalog_seed_rows,
)


def test_seed_cubre_todas_las_operaciones() -> None:
    filas = catalog_seed_rows()
    assert len(filas) == len(OPERATIONS)
    assert {(f["bot"], f["operation"]) for f in filas} == set(OPERATIONS)
    for fila in filas:
        assert fila["unit_cost"] > 0
        assert fila["effect_class"] in ("CONSULTA", "EFECTO")
        assert fila["display_name"].strip()


def test_seed_clasifica_efecto() -> None:
    por_clave = {(f["bot"], f["operation"]): f["effect_class"] for f in catalog_seed_rows()}
    assert por_clave[("ccma", "consultar")] == "CONSULTA"
    assert por_clave[("mis_comprobantes", "consultar")] == "CONSULTA"
    assert por_clave[("mis_comprobantes", "historial")] == "CONSULTA"
    assert por_clave[("mis_comprobantes", "solicitar")] == "EFECTO"
    assert por_clave[("portal_iva", "descargar")] == "EFECTO"
    assert por_clave[("vep_archivo", "generar")] == "EFECTO"


def test_seed_solo_bots_del_catalogo() -> None:
    conocidos = {str(item["bot"]) for item in CATALOGUE}
    for fila in catalog_seed_rows():
        assert fila["bot"] in conocidos
