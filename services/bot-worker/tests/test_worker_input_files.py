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
from bot_worker.runtime.input_files import descargar_entradas, descargar_entradas_por_campo  # noqa: E402
from bot_worker.schemas import get_schema_document  # noqa: E402


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


def test_descarga_por_campo_entrega_nombres_locales_sin_contenido(tmp_path: Path) -> None:
    campos = asyncio.run(
        descargar_entradas_por_campo(
            [
                {"campo": "archivo_path", "nombre": "vep.txt", "url": "u1"},
                {"campo": "ventas_txt_path", "nombre": "ventas.txt", "url": "u2"},
            ],
            tmp_path,
            _Client({"u1": b"hola", "u2": "ventas ñ".encode("utf-8")}),
        )
    )
    assert campos == {"archivo_path": "vep.txt", "ventas_txt_path": "ventas.txt"}
    assert (tmp_path / campos["archivo_path"]).read_bytes() == b"hola"
    assert (tmp_path / campos["ventas_txt_path"]).read_text(encoding="utf-8") == "ventas ñ"


def test_descarga_por_campo_rechaza_campos_duplicados_y_archivos_vacios(tmp_path: Path) -> None:
    with pytest.raises(InvalidInputError):
        asyncio.run(
            descargar_entradas_por_campo(
                [
                    {"campo": "archivo_path", "nombre": "a.txt", "url": "u1"},
                    {"campo": "archivo_path", "nombre": "b.txt", "url": "u2"},
                ],
                tmp_path,
                _Client({"u1": b"a", "u2": b"b"}),
            )
        )
    with pytest.raises(InvalidInputError):
        asyncio.run(
            descargar_entradas_por_campo(
                [{"campo": "archivo_path", "nombre": "vacio.txt", "url": "u"}],
                tmp_path,
                _Client({"u": b""}),
            )
        )


@pytest.mark.parametrize(
    ("bot", "operation", "path_fields"),
    [
        ("carga_portal_iva", "cargar", {"liv_cbte_path", "liv_alicuota_path", "csv_cf_path"}),
        ("vep_archivo", "generar", {"archivo_path"}),
        ("portal_iva", "importar", {"ventas_txt_path", "ventas_alicuotas_txt_path"}),
        ("portal_iva", "gestionar", {"ventas_txt_path", "ventas_alicuotas_txt_path"}),
    ],
)
def test_schema_publica_solo_rutas_locales_para_archivos(
    bot: str, operation: str, path_fields: set[str]
) -> None:
    document = get_schema_document(bot, operation)
    schema = document["schema"]
    properties = schema.get("properties", {})
    examples = document["openapi_examples"]["consulta_habitual"]["value"]

    assert path_fields <= set(properties)
    assert path_fields <= set(examples)
    assert not any("b64" in name.lower() or "base64" in name.lower() for name in properties)
    assert all(isinstance(examples[name], str) for name in path_fields)
