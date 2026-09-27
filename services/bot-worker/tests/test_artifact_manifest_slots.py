"""Subida de artefactos declarados por el manifiesto del plugin.

El sobre puede no enumerar cada artefacto por bot; en ese caso el worker pide
una URL prefirmada bajo demanda, pero solo para identificadores que el
manifiesto del plugin declara.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from bot_worker.runtime.context import ArtifactSlot, ArtifactStore

DECLARADOS = {
    "historico.xls": (("application/vnd.ms-excel",), 52_428_800),
    "sct_reporte": (("application/pdf",), 1_024),
}


def _presign_de_prueba(llamadas: list[tuple[str, str, int]]):
    async def presign(artifact_id: str, content_type: str, size_bytes: int) -> dict:
        llamadas.append((artifact_id, content_type, size_bytes))
        return {"upload_url": "https://bucket.invalid/put", "object_key": f"jobs/j/1/{artifact_id}"}

    return presign


def test_upload_declara_slot_bajo_demanda_con_el_manifiesto(tmp_path: Path) -> None:
    archivo = tmp_path / "historico_20123456789.xls"
    archivo.write_bytes(b"x" * 32)
    llamadas: list[tuple[str, str, int]] = []
    store = ArtifactStore(
        work_dir=tmp_path,
        slots={},
        presign=_presign_de_prueba(llamadas),
        declarados=DECLARADOS,
    )

    referencia = asyncio.run(
        store.upload("historico_xls", archivo.name)
    )
    assert [c[0] for c in llamadas] == ["historico_xls"]
    assert llamadas[0][2] == 32
    assert llamadas[0][1].startswith("application/")
    assert referencia["object_key"] == "jobs/j/1/historico_xls"
    assert referencia["size_bytes"] == 32


def test_upload_acepta_familias_sin_extension(tmp_path: Path) -> None:
    archivo = tmp_path / "sct_reporte_deudas_csv.csv"
    archivo.write_bytes(b"a,b\n1,2\n")
    llamadas: list[tuple[str, str, int]] = []
    store = ArtifactStore(
        work_dir=tmp_path,
        slots={},
        presign=_presign_de_prueba(llamadas),
        declarados=DECLARADOS,
    )

    referencia = asyncio.run(store.upload("sct_reporte_deudas_csv", archivo.name))

    assert llamadas and llamadas[0][0] == "sct_reporte_deudas_csv"
    assert referencia["content_type"] in ("text/csv", "application/octet-stream")


def test_upload_rechaza_artefacto_no_declarado(tmp_path: Path) -> None:
    archivo = tmp_path / "ajeno.bin"
    archivo.write_bytes(b"x")
    store = ArtifactStore(
        work_dir=tmp_path,
        slots={},
        presign=_presign_de_prueba([]),
        declarados=DECLARADOS,
    )

    with pytest.raises(ValueError):
        asyncio.run(store.upload("ajeno_bin", archivo.name))


def test_upload_respeta_max_bytes_del_manifiesto(tmp_path: Path) -> None:
    archivo = tmp_path / "sct_reporte_vencimientos_csv.csv"
    archivo.write_bytes(b"x" * 2_048)
    store = ArtifactStore(
        work_dir=tmp_path,
        slots={},
        presign=_presign_de_prueba([]),
        declarados=DECLARADOS,
    )

    with pytest.raises(ValueError):
        asyncio.run(store.upload("sct_reporte_vencimientos_csv", archivo.name))


def test_slot_del_sobre_sigue_siendo_el_camino_principal(tmp_path: Path) -> None:
    archivo = tmp_path / "historico_20123456789.xls"
    archivo.write_bytes(b"x" * 8)
    llamadas: list[tuple[str, str, int]] = []
    store = ArtifactStore(
        work_dir=tmp_path,
        slots={
            "historico_xls": ArtifactSlot(
                artifact_id="historico_xls",
                put_url="https://bucket.invalid/firmado",
                object_key="jobs/j/1/historico_xls",
            )
        },
        presign=_presign_de_prueba(llamadas),
        declarados=DECLARADOS,
    )

    referencia = asyncio.run(store.upload("historico_xls", archivo.name))

    assert llamadas == []
    assert referencia["object_key"] == "jobs/j/1/historico_xls"
