"""Subida de artefactos declarados por el manifiesto del plugin.

El sobre puede no enumerar cada artefacto por bot; en ese caso el worker pide
una URL prefirmada bajo demanda, pero solo para identificadores que el
manifiesto del plugin declara.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from bot_worker.bots.errors import ArtifactUploadError
from bot_worker.runtime.context import ArtifactSlot, ArtifactStore

DECLARADOS = {
    "historico.xls": (("application/vnd.ms-excel",), 52_428_800),
    "sct_reporte": (("application/pdf",), 1_024),
}


def _presign_de_prueba(llamadas: list[tuple[str, str, int]]):
    async def presign(artifact_id: str, content_type: str, size_bytes: int, filename: str = "") -> dict:
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


def test_planilla_con_codificacion_local_se_filtra(tmp_path: Path) -> None:
    """Un CSV del organismo en cp1252 no debe romper el filtrado por rango."""
    from bot_worker.bots.comprobantes.plugin import filtrar_csv_por_rango

    origen = tmp_path / "comprobantes.csv"
    origen.write_bytes(
        "Fecha,Razón Social,Importe\n01/09/2026,ACME S.A.,100,00\n"
        "15/09/2026,Cañerías Ñandú,200,00\n".encode("cp1252")
    )
    destino = tmp_path / "filtrado.csv"

    conservadas = filtrar_csv_por_rango(origen, destino, "01/09/2026", "30/09/2026")

    assert conservadas == 2
    assert "Cañerías" in destino.read_text(encoding="utf-8")


def test_mismo_artifact_id_dos_archivos_pide_url_nueva(tmp_path) -> None:
    import asyncio

    from bot_worker.runtime.context import ArtifactStore

    pedidos: list[str] = []

    async def presign(artifact_id: str, content_type: str, size: int, filename: str = "") -> dict:
        pedidos.append(artifact_id)
        n = len(pedidos)
        return {"upload_url": f"http://s3/u{n}", "object_key": f"jobs/j/1/{artifact_id}/{n}"}

    (tmp_path / "a.bin").write_bytes(b"uno")
    (tmp_path / "b.bin").write_bytes(b"dos")
    store = ArtifactStore(
        tmp_path, {}, presign=presign,
        declarados={"libros_iva.bin": (("application/octet-stream",), 1000)},
    )
    r1 = asyncio.run(store.upload("libros_iva_bin", "a.bin"))
    r2 = asyncio.run(store.upload("libros_iva_bin", "b.bin"))
    assert len(pedidos) == 2
    assert r1["object_key"] != r2["object_key"]


@pytest.mark.parametrize("fallo", [None, 403, 500, "transporte"])
def test_upload_confirma_http_y_normaliza_error_sin_url_firmada(tmp_path, fallo) -> None:
    (tmp_path / "historico.xls").write_bytes(b"contenido")

    def responder(request):
        if fallo == "transporte":
            raise httpx.ConnectError("URL firmada secreta", request=request)
        return httpx.Response(fallo or 200)

    async def ejecutar():
        async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
            store = ArtifactStore(
                tmp_path,
                {"historico.xls": ArtifactSlot(
                    artifact_id="historico.xls",
                    put_url="https://bucket.invalid/put?firma=secreta",
                    object_key="jobs/j/1/historico.xls",
                )},
                http_client=client,
            )
            if fallo is None:
                assert (await store.upload("historico.xls", "historico.xls"))["size_bytes"] == 9
            else:
                with pytest.raises(ArtifactUploadError) as exc:
                    await store.upload("historico.xls", "historico.xls")
                assert exc.value.categoria == "ARTIFACT_UPLOAD_FAILED"
                assert "secreta" not in str(exc.value)
                assert "bucket.invalid" not in str(exc.value)

    asyncio.run(ejecutar())


def test_el_worker_le_manda_a_la_central_el_nombre_real_del_archivo_en_cada_subida(tmp_path: Path) -> None:
    """ArtifactStore.upload -> presign(artifact_id, tipo, tamano, nombre): el nombre es el del archivo, no el artifact_id."""
    vistos: list[tuple[str, str]] = []

    async def presign(artifact_id: str, content_type: str, size_bytes: int, filename: str = "") -> dict:
        vistos.append((artifact_id, filename))
        return {"upload_url": "", "object_key": f"jobs/j/{filename}"}

    store = ArtifactStore(work_dir=tmp_path, slots={}, presign=presign, declarados=_DECLARADOS_CCMA)
    for nombre in ("ccma_saldos_20374730429.csv", "ccma_movimientos_20374730429.csv", "ccma_resumen_20374730429.pdf"):
        (tmp_path / nombre).write_bytes(b"x" * 10)
    ids = {"ccma_saldos_20374730429.csv": "ccma_saldos.csv", "ccma_movimientos_20374730429.csv": "ccma_movimientos.csv",
           "ccma_resumen_20374730429.pdf": "ccma_resumen.pdf"}

    async def _subir():
        return [await store.upload(i, n) for n, i in ids.items()]

    refs = asyncio.run(_subir())
    assert vistos == [(i, n) for n, i in ids.items()]
    assert [r["object_key"] for r in refs] == [f"jobs/j/{n}" for n in ids]


_DECLARADOS_CCMA = {
    "ccma_saldos.csv": (("text/csv",), 1_024),
    "ccma_movimientos.csv": (("text/csv",), 1_024),
    "ccma_resumen.pdf": (("application/pdf",), 1_024),
}
