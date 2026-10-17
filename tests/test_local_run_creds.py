"""Regresiones del orden temporal de credenciales locales."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from tools.local_run import creds  # noqa: E402


def _crear_db(path: Path, rows: list[tuple]) -> None:
    with sqlite3.connect(path) as con:
        con.execute(
            "CREATE TABLE consulta_sct_logs "
            "(id INTEGER, timestamp TEXT, status TEXT, marker TEXT)"
        )
        con.executemany(
            "INSERT INTO consulta_sct_logs VALUES (?, ?, 'success', ?)", rows
        )


def test_filas_ordena_por_timestamp_antes_del_limite_y_entre_bases(
    tmp_path: Path, monkeypatch
) -> None:
    # Los nombres sugieren el orden opuesto, y el id mayor no es la fila más nueva.
    _crear_db(
        tmp_path / "00_older.db",
        [
            (900, "2026-10-15T10:00:00", "old-in-first-db"),
            (1, "2026-10-17T10:00:00", "new-in-first-db"),
        ],
    )
    _crear_db(
        tmp_path / "99_newer.db",
        [(700, "2026-10-16T10:00:00", "middle-in-second-db")],
    )
    monkeypatch.setattr(creds, "db_dirs", lambda: [tmp_path])

    result = creds.filas(modulo="sct", limit=1)

    assert [(item["db"], item["row"]["id"]) for item in result] == [
        ("00_older.db", 1),
        ("99_newer.db", 700),
    ]


def test_filas_usa_id_como_fallback_si_no_hay_timestamp(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "without_timestamp.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE consulta_sct_logs (id INTEGER, status TEXT)")
        con.executemany(
            "INSERT INTO consulta_sct_logs VALUES (?, 'success')", [(2,), (8,)]
        )
    monkeypatch.setattr(creds, "db_dirs", lambda: [tmp_path])

    result = creds.filas(modulo="sct")

    assert [item["row"]["id"] for item in result] == [8, 2]
