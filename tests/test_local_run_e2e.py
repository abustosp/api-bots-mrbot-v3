"""El estado terminal COMPLETO no implica que la consulta haya sido exitosa."""

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from tools.local_run import e2e_testing


@pytest.mark.parametrize(
    ("resultado", "exit_code"),
    [
        ({"status": "COMPLETO", "result": "OK"}, 0),
        ({"status": "COMPLETO", "result": "PARCIAL"}, 0),
        ({"status": "COMPLETO", "result": "ERROR"}, 1),
        ({"status": "FALLIDO", "result": "ERROR"}, 1),
        ({"status": "CORRIENDO", "result": "OK"}, 1),
        ({"error": "timeout esperando el job"}, 1),
    ],
)
def test_main_exige_estado_y_resultado_exitosos(monkeypatch, resultado, exit_code):
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "e2e_testing.py", "--bot", "sct", "--operacion", "consultar",
            "--api-key", "test-only", "--body-json", "{}",
        ],
    )
    monkeypatch.setattr(e2e_testing, "correr_caso", lambda *args, **kwargs: resultado)

    assert e2e_testing.main() == exit_code
