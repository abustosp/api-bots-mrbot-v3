"""Pruebas del bot ``comprobantes`` contra el formato real del portal.

El botón ``CSV`` de Mis Comprobantes devuelve un ZIP con un único miembro
``.csv`` y las fechas de esa planilla vienen en ISO (``aaaa-mm-dd``). Estas
pruebas fijan ese contrato: si el plugin vuelve a leer el ZIP como texto, o a
exigir ``dd/mm/aaaa``, el job termina sin filas o con formato inesperado.
"""

from __future__ import annotations

import asyncio
import csv
import io
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bot_worker.bots.comprobantes.plugin import (
    ComprobantesPlugin,
    filtrar_csv_por_rango,
)

CUIT = "20320160856"
ENCABEZADO = [
    "Fecha de Emisión",
    "Tipo de Comprobante",
    "Punto de Venta",
    "Número Desde",
    "Imp. Total",
]


def _planilla(filas: list[tuple[str, str, str]]) -> str:
    buffer = io.StringIO(newline="")
    escritor = csv.writer(buffer, delimiter=";")
    escritor.writerow(ENCABEZADO)
    for fecha, tipo, total in filas:
        escritor.writerow([fecha, tipo, "00001", "00000101", total])
    return buffer.getvalue()


def _zip_con_planilla(destino: Path, filas: list[tuple[str, str, str]]) -> None:
    with zipfile.ZipFile(destino, "w") as paquete:
        paquete.writestr(
            "comprobantes_consulta_csv_emitidos_1_2.csv", _planilla(filas)
        )


def test_filtrar_csv_por_rango_acepta_fechas_iso_de_la_planilla(tmp_path: Path) -> None:
    origen = tmp_path / "planilla.csv"
    destino = tmp_path / "filtrado.csv"
    origen.write_text(
        _planilla(
            [
                ("2025-12-31", "1", "100.00"),
                ("2026-01-10", "11", "200.00"),
                ("2026-01-31", "11", "300.00"),
                ("2026-02-01", "11", "400.00"),
            ]
        ),
        encoding="utf-8",
    )

    filas = filtrar_csv_por_rango(origen, destino, "01/01/2026", "31/01/2026")

    assert filas == 2
    texto = destino.read_text(encoding="utf-8")
    assert "2026-01-10" in texto and "2026-01-31" in texto
    assert "2025-12-31" not in texto and "2026-02-01" not in texto


class _ServicioZipeado:
    """Simula la descarga real: escribe un ZIP donde el plugin espera el CSV."""

    def __init__(self) -> None:
        self.destinos: list[Path] = []
        self.representados: list[str] = []

    async def seleccionar_representado(self, cuit: str) -> None:
        self.representados.append(cuit)

    async def descargar_csv(self, *, tipo: str, destino: Path, desde: str, hasta: str) -> None:
        self.destinos.append(Path(destino))
        _zip_con_planilla(
            Path(destino),
            [("2025-12-31", "1", "1"), ("2026-01-10", "11", "2")],
        )


class _SesionComprobantes:
    def __init__(self, servicio: _ServicioZipeado) -> None:
        self.servicio = servicio

    async def login(self) -> None:
        pass

    async def open_service(self, servicio: str, **kwargs: Any) -> _ServicioZipeado:
        return self.servicio


class _Fabrica:
    def __init__(self, sesion: _SesionComprobantes) -> None:
        self.sesion = sesion

    def arca_session(self, **kwargs: Any) -> Any:
        sesion = self.sesion

        class _Contexto:
            async def __aenter__(self) -> _SesionComprobantes:
                return sesion

            async def __aexit__(self, *args: Any) -> bool:
                return False

        return _Contexto()


class _Sink:
    async def progress(self, **kwargs: Any) -> None:
        pass


def test_execute_desempaqueta_el_zip_del_portal_antes_de_filtrar(tmp_path: Path) -> None:
    from bot_worker.runtime.context import ArtifactStore, BotRuntime, CancellationToken

    eventos: list[str] = []

    async def _presign(artifact_id: str, content_type: str, size: int) -> dict[str, Any]:
        eventos.append(artifact_id)
        return {"upload_url": "", "object_key": f"jobs/x/{artifact_id}"}

    async def _ejecutar() -> tuple[Any, _ServicioZipeado]:
        servicio = _ServicioZipeado()
        plugin = ComprobantesPlugin()
        entrada = await plugin.validate(
            {
                "operacion": "consultar",
                "representado_cuit": CUIT,
                "representado_nombre": "Empresa de Prueba",
                "fecha_desde": "01/01/2026",
                "fecha_hasta": "31/01/2026",
                "emitidos": True,
                "subir_csv": True,
            }
        )
        runtime = BotRuntime(
            job_id="job-comprobantes",
            work_dir=tmp_path,
            deadline=SimpleNamespace(remaining_seconds=lambda: 60),
            credentials=SimpleNamespace(clave="clave-ficticia"),
            proxy=None,
            artifact_store=ArtifactStore(
                tmp_path,
                slots={},
                presign=_presign,
                declarados={
                    "emitidos_csv": (("text/csv",), 52_428_800),
                    "recibidos_csv": (("text/csv",), 52_428_800),
                },
            ),
            event_sink=_Sink(),
            browser_factory=_Fabrica(_SesionComprobantes(servicio)),
            cancellation=CancellationToken(),
        )
        return await plugin.execute(entrada, runtime), servicio

    resultado, servicio = asyncio.run(_ejecutar())

    assert resultado.result == "OK"
    assert servicio.representados == [CUIT]
    assert resultado.data["emitidos"]["filas"] == 1
    assert eventos == ["emitidos_csv"]
    assert [(a["artifact_id"], a["size_bytes"]) for a in resultado.artifacts] == [
        ("emitidos_csv", len(_planilla([("2026-01-10", "11", "2")]).encode()))
    ]
    # El ZIP quedó reemplazado por el CSV filtrado dentro de work_dir.
    archivos = list(tmp_path.glob("*.csv"))
    assert len(archivos) == 1
    assert "2026-01-10" in archivos[0].read_text(encoding="utf-8")
    assert not servicio.destinos[0].exists() or not zipfile.is_zipfile(servicio.destinos[0])
