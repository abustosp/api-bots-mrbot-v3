"""Paridad de ``plugin.execute`` de ``liquidacion_granos`` con V1.

V1 (``liquidacion_granos``) descarga planillas y comprobantes por seccion
(LPG emitidas y recibidas, LSG emitidas y recibidas, certificados de deposito)
dentro del rango de fechas. El plugin V3 fija el mismo orden, el mismo filtro
de secciones y la misma entrada con los nombres del contrato compartido.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.errors import InvalidInputError
from bot_worker.bots.liquidacion_granos.plugin import LiquidacionGranosPlugin, nombre_base_archivo
from bot_worker.runtime.context import ArtifactStore, CancellationToken
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.liquidacion_granos import LiquidacionGranosPortal

CUIT = "20123456786"
NOMBRE = "EJEMPLO SA"
PDF = b"%PDF-1.4 comprobante"
XLSX = b"PK fake planilla"


class _Servicio:
    def __init__(self) -> None:
        self.seleccion: tuple[str, str] | None = None
        self.secciones: list[str] = []
        self.comprobantes_por_seccion: dict[str, int] = {}

    async def seleccionar_representado(self, cuit: str, denominacion: str = "") -> None:
        self.seleccion = (cuit, denominacion)

    async def descargar_planilla(self, *, seccion: str, destino: Path, desde: str, hasta: str) -> Path:
        self.secciones.append(seccion)
        Path(destino).write_bytes(XLSX)
        return Path(destino)

    async def descargar_comprobantes(self, *, seccion: str, destino_dir: Path, desde: str, hasta: str) -> list[Path]:
        rutas: list[Path] = []
        for indice in range(self.comprobantes_por_seccion.get(seccion, 0)):
            ruta = Path(destino_dir) / f"{seccion}_{indice}.pdf"
            ruta.write_bytes(PDF)
            rutas.append(ruta)
        return rutas


class _Sesion:
    def __init__(self, servicio: _Servicio) -> None:
        self.servicio = servicio

    async def login(self) -> None:
        return None

    async def open_service(self, servicio: str, **_: Any) -> _Servicio:
        return self.servicio


class _Fabrica:
    def __init__(self, sesion: _Sesion) -> None:
        self.sesion = sesion

    def arca_session(self, **_: Any) -> Any:
        sesion = self.sesion

        class _Contexto:
            async def __aenter__(self) -> _Sesion:
                return sesion

            async def __aexit__(self, *_: Any) -> bool:
                return False

        return _Contexto()


class _Sink:
    async def progress(self, **_: Any) -> None:
        return None


class _Presign:
    def __init__(self) -> None:
        self.ids: list[str] = []

    async def __call__(self, artifact_id: str, content_type: str, size: int, filename: str = "") -> dict[str, Any]:
        self.ids.append(artifact_id)
        return {"upload_url": "", "object_key": f"jobs/x/{artifact_id}"}


def _entrada(**extra: Any) -> dict[str, Any]:
    return {
        "operacion": "consultar",
        "representado_cuit": CUIT,
        "denominacion": NOMBRE,
        "desde": "01/01/2026",
        "hasta": "31/01/2026",
        **extra,
    }


def _ejecutar(tmp_path: Path, *, comprobantes: dict[str, int] | None = None,
              **extra: Any) -> tuple[Any, _Servicio, _Presign]:
    plugin = LiquidacionGranosPlugin()
    validado = asyncio.run(plugin.validate(_entrada(**extra)))
    servicio = _Servicio()
    servicio.comprobantes_por_seccion = comprobantes or {}
    presign = _Presign()
    runtime = SimpleNamespace(
        job_id="job-granos",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia", cuit_representante="20111111116"),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path,
            slots={},
            presign=presign,
            declarados={
                "planilla.xlsx": (
                    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",), 52_428_800,
                ),
                "comprobante.pdf": (("application/pdf",), 10_485_760),
            },
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(_Sesion(servicio)),
        cancellation=CancellationToken(),
    )
    resultado = asyncio.run(plugin.execute(validado, runtime))
    return resultado, servicio, presign


def test_se_registra_por_descubrimiento() -> None:
    assert portal_para("liquidacion_granos") is LiquidacionGranosPortal


def test_selecciona_representado_por_denominacion(tmp_path: Path) -> None:
    _, servicio, _ = _ejecutar(tmp_path)

    assert servicio.seleccion == (CUIT, NOMBRE)


def test_descarga_las_cinco_secciones_en_orden_v1(tmp_path: Path) -> None:
    resultado, servicio, _ = _ejecutar(tmp_path)

    assert servicio.secciones == [
        "lpg_emitidas", "lpg_recibidas", "lsg_emitidas", "lsg_recibidas", "certificados_deposito",
    ]
    assert resultado.result == "OK"
    assert set(resultado.data["secciones"]) == set(servicio.secciones)


def test_seccion_desactivada_no_se_descarga(tmp_path: Path) -> None:
    resultado, servicio, _ = _ejecutar(tmp_path, lpg_emitidas=False, lsg_recibidas=False)

    assert "lpg_emitidas" not in servicio.secciones
    assert "lsg_recibidas" not in servicio.secciones
    assert "lpg_recibidas" in servicio.secciones


def test_planilla_y_comprobantes_se_suben_con_su_slot(tmp_path: Path) -> None:
    resultado, _, presign = _ejecutar(
        tmp_path,
        comprobantes={"lpg_emitidas": 2},
        lpg_recibidas=False, lsg_emitidas=False, lsg_recibidas=False, certificados_deposito=False,
    )

    # una planilla y dos comprobantes, cada uno con su slot
    assert presign.ids == ["planilla_xlsx", "comprobante_pdf", "comprobante_pdf"]
    seccion = resultado.data["secciones"]["lpg_emitidas"]
    assert seccion["comprobantes"]["cantidad"] == 2
    assert seccion["archivo"].endswith(".xlsx")


def test_minio_upload_false_no_sube_nada(tmp_path: Path) -> None:
    resultado, _, presign = _ejecutar(
        tmp_path,
        comprobantes={"lpg_emitidas": 1},
        minio_upload=False,
        lpg_recibidas=False, lsg_emitidas=False, lsg_recibidas=False, certificados_deposito=False,
    )

    assert presign.ids == []
    assert resultado.artifacts == []
    assert resultado.data["secciones"]["lpg_emitidas"]["comprobantes"]["archivos"]


def test_nombre_base_sigue_el_patron_v1() -> None:
    nombre = nombre_base_archivo(CUIT, "01/01/2026", "31/01/2026", "lpg_emitidas")
    assert nombre == f"{CUIT} - LPG EMITIDAS - 01012026 - 31012026"


def test_denominacion_vacia_se_rechaza_antes_del_navegador() -> None:
    plugin = LiquidacionGranosPlugin()
    with pytest.raises(InvalidInputError):
        asyncio.run(plugin.validate(_entrada(denominacion="")))


def test_sin_ninguna_seccion_se_rechaza() -> None:
    plugin = LiquidacionGranosPlugin()
    with pytest.raises(InvalidInputError):
        asyncio.run(
            plugin.validate(
                _entrada(
                    lpg_emitidas=False, lpg_recibidas=False, lsg_emitidas=False,
                    lsg_recibidas=False, certificados_deposito=False,
                )
            )
        )


def test_rango_invertido_se_rechaza() -> None:
    plugin = LiquidacionGranosPlugin()
    with pytest.raises(InvalidInputError):
        asyncio.run(plugin.validate(_entrada(desde="31/01/2026", hasta="01/01/2026")))


def test_proxy_y_clave_del_payload_se_descartan(tmp_path: Path) -> None:
    resultado, servicio, _ = _ejecutar(
        tmp_path,
        proxy_request=True,
        clave="no-debe-usarse",
        cuit_representante="20999999999",
        cuit_representado=CUIT,
    )

    assert resultado.result == "OK"
    assert servicio.seleccion == (CUIT, NOMBRE)
