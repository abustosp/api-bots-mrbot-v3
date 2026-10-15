"""``ruta_para_upload`` y bots que escriben archivos en subcarpetas de work_dir."""

from __future__ import annotations

import asyncio
from pathlib import Path

import bot_worker.bots.registry  # noqa: F401  (evita el import circular de portales)
from bot_worker.bots.comprobantes.plugin import nombre_base_archivo as nombre_c
from bot_worker.bots.mis_comprobantes.plugin import nombre_base_archivo as nombre_m
from bot_worker.runtime.context import ArtifactStore, ruta_para_upload


def _store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path, {}, declarados={"x": (("application/pdf",), 1_000_000)})


def test_ruta_relativa_para_archivo_en_subcarpeta(tmp_path: Path) -> None:
    store = _store(tmp_path)
    archivo = store.resolve("base.vistas/a.pdf")
    archivo.parent.mkdir(parents=True)
    archivo.write_bytes(b"%PDF-1.4")

    ruta = ruta_para_upload(tmp_path, archivo)

    assert ruta == "base.vistas/a.pdf"
    ref = asyncio.run(store.upload("x", ruta))
    assert ref["size_bytes"] == 8
    # el nombre suelto no alcanza: es el bug original
    store2 = _store(tmp_path)
    try:
        asyncio.run(store2.upload("x", archivo.name))
    except ValueError:
        pass
    else:  # pragma: no cover
        raise AssertionError("subir solo el nombre deberia fallar")


def test_ruta_para_upload_cae_al_nombre_si_no_hay_work_dir(tmp_path: Path) -> None:
    archivo = tmp_path / "a.pdf"
    assert ruta_para_upload(None, archivo) == "a.pdf"
    assert ruta_para_upload("/otra/carpeta", archivo) == "a.pdf"


def test_nombres_de_comprobantes_no_contienen_separadores() -> None:
    for fn in (nombre_c, nombre_m):
        nombre = fn("20123456789", 'PEREZ S.R.L. / HNOS \\ "X": <y>|?*', "01/01/2026", "31/01/2026", "emitidos")
        assert not any(c in nombre for c in '/\\:*?"<>|')
        assert nombre.startswith("9 - MCE - 01012026 - 31012026 - 20123456789 - PEREZ S.R.L.")
