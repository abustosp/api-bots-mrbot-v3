"""Descarga de entradas del bucket uploads al work_dir."""

from __future__ import annotations

import asyncio
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bot_worker.bots.errors import InvalidInputError  # noqa: E402
from bot_worker.runtime.input_files import descargar_entradas  # noqa: E402


class _Resp:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, data: dict[str, bytes]) -> None:
        self.data = data

    async def get(self, url: str) -> _Resp:
        return _Resp(self.data[url])


def _zip(**miembros: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for nombre, contenido in miembros.items():
            zf.writestr(nombre, contenido)
    return buf.getvalue()


def test_descarga_pem_y_expande_zip(tmp_path: Path) -> None:
    client = _Client({"u1": b"PEM1", "u2": _zip(**{"sub/F8011.002.pem": b"PEM2"})})
    nombres = asyncio.run(
        descargar_entradas(
            [
                {"campo": "archivos", "nombre": "F8010.001.pem", "url": "u1"},
                {"campo": "archivos", "nombre": "lote.zip", "url": "u2"},
            ],
            tmp_path,
            client,
        )
    )
    assert nombres == ["F8010.001.pem", "F8011.002.pem"]
    assert (tmp_path / "F8010.001.pem").read_bytes() == b"PEM1"
    assert (tmp_path / "F8011.002.pem").read_bytes() == b"PEM2"
    assert not (tmp_path / "lote.zip").exists()


def test_rechaza_nombre_con_ruta(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError):
        asyncio.run(
            descargar_entradas(
                [{"campo": "archivos", "nombre": "../x.pem", "url": "u"}],
                tmp_path,
                _Client({"u": b"x"}),
            )
        )
