"""``certificacion_primaria_granos``: plugin y portal de certificados como depositante."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.certificacion_primaria_granos.plugin import (
    CertificacionPrimariaGranosPlugin,
    nombre_planilla,
)
from bot_worker.bots.errors import InvalidInputError
from bot_worker.runtime.context import ArtifactStore, CancellationToken
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.certificacion_primaria_granos import (
    CertificacionPrimariaGranosPortal,
)

CUIT = "20123456786"
NOMBRE = "EJEMPLO SA"


class _Servicio:
    def __init__(self, pdfs: int = 2) -> None:
        self.pdfs = pdfs
        self.seleccion: tuple[str, str] | None = None
        self.rango: tuple[str, str] | None = None

    async def seleccionar_representado(self, cuit: str, denominacion: str = "") -> None:
        self.seleccion = (cuit, denominacion)

    async def descargar_certificados_depositante(
        self, *, destino_xlsx: Path, destino_dir: Path, desde: str, hasta: str
    ) -> tuple[Path, list[Path]]:
        self.rango = (desde, hasta)
        Path(destino_xlsx).write_bytes(b"PK fake")
        rutas = []
        for i in range(self.pdfs):
            ruta = Path(destino_dir) / f"cert_{i}.pdf"
            ruta.write_bytes(b"%PDF-1.4")
            rutas.append(ruta)
        return Path(destino_xlsx), rutas


class _Fabrica:
    def __init__(self, servicio: _Servicio) -> None:
        self.servicio = servicio
        self.portal: str | None = None

    def arca_session(self, **_: Any) -> Any:
        fabrica = self

        class _Sesion:
            async def login(self) -> None:
                return None

            async def open_service(self, _nombre: str, **kw: Any) -> _Servicio:
                fabrica.portal = kw.get("portal")
                return fabrica.servicio

        class _Ctx:
            async def __aenter__(self) -> _Sesion:
                return _Sesion()

            async def __aexit__(self, *_: Any) -> bool:
                return False

        return _Ctx()


class _Sink:
    async def progress(self, **_: Any) -> None:
        return None


class _Presign:
    def __init__(self) -> None:
        self.ids: list[str] = []

    async def __call__(self, artifact_id: str, content_type: str, size: int) -> dict[str, Any]:
        self.ids.append(artifact_id)
        return {"upload_url": "", "object_key": f"jobs/x/{artifact_id}"}


def _entrada(**extra: Any) -> dict[str, Any]:
    return {
        "operacion": "consultar",
        "representado_cuit": CUIT,
        "denominacion": NOMBRE,
        "desde": "01/08/2025",
        "hasta": "31/10/2025",
        **extra,
    }


def _ejecutar(tmp_path: Path, pdfs: int = 2, **extra: Any):
    plugin = CertificacionPrimariaGranosPlugin()
    validado = asyncio.run(plugin.validate(_entrada(**extra)))
    servicio = _Servicio(pdfs)
    fabrica = _Fabrica(servicio)
    presign = _Presign()
    runtime = SimpleNamespace(
        job_id="job-cpg",
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
                    ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
                    52_428_800,
                ),
                "certificado.pdf": (("application/pdf",), 10_485_760),
            },
        ),
        event_sink=_Sink(),
        browser_factory=fabrica,
        cancellation=CancellationToken(),
    )
    return asyncio.run(plugin.execute(validado, runtime)), servicio, fabrica, presign


def test_se_registra_por_descubrimiento() -> None:
    assert portal_para("certificacion_primaria_granos") is CertificacionPrimariaGranosPortal


def test_flujo_completo_sube_planilla_y_un_pdf_por_certificado(tmp_path: Path) -> None:
    resultado, servicio, fabrica, presign = _ejecutar(tmp_path)

    assert fabrica.portal == "certificacion_primaria_granos"
    assert servicio.seleccion == (CUIT, NOMBRE)
    assert servicio.rango == ("01/08/2025", "31/10/2025")
    assert resultado.result == "OK"
    assert resultado.data["cantidad_certificados"] == 2
    assert presign.ids == ["planilla_xlsx", "certificado_pdf", "certificado_pdf"]


def test_subir_archivos_false_no_sube_nada(tmp_path: Path) -> None:
    resultado, _, _, presign = _ejecutar(tmp_path, minio_upload=False)

    assert presign.ids == [] and resultado.artifacts == []
    assert len(resultado.data["certificados"]) == 2


def test_sin_certificados_solo_devuelve_planilla(tmp_path: Path) -> None:
    resultado, _, _, presign = _ejecutar(tmp_path, pdfs=0)

    assert resultado.data["cantidad_certificados"] == 0
    assert presign.ids == ["planilla_xlsx"]


def test_nombre_planilla() -> None:
    assert nombre_planilla(CUIT, "01/08/2025", "31/10/2025") == (
        f"CPG - {CUIT} - 01082025 - 31102025.xlsx"
    )


@pytest.mark.parametrize(
    "extra",
    [{"desde": "31/10/2025", "hasta": "01/08/2025"}, {"denominacion": ""}, {"hasta": "2025-10-31"}],
)
def test_entrada_invalida_se_rechaza_antes_del_navegador(extra: dict[str, Any]) -> None:
    with pytest.raises(InvalidInputError):
        asyncio.run(CertificacionPrimariaGranosPlugin().validate(_entrada(**extra)))


def test_portal_elige_el_tipo_depositante(tmp_path: Path) -> None:
    portal = CertificacionPrimariaGranosPortal(page=SimpleNamespace(url=""))
    llamadas: list[tuple[str, str, str]] = []

    async def _abrir() -> None:
        return None

    async def _tipos() -> list[tuple[str, str]]:
        return [("P", "Certificados como Depositario"), ("D", "Certificados como Depositante")]

    async def _consultar(desde: str, hasta: str, tipo: str) -> None:
        llamadas.append((desde, hasta, tipo))

    async def _tablas() -> list[dict[str, Any]]:
        return []

    async def _pdfs(destino_dir: Path, tipo: str) -> list[Path]:
        return []

    portal._abrir_certificados = _abrir  # type: ignore[method-assign]
    portal._tipos_certificado = _tipos  # type: ignore[method-assign]
    portal._consultar_certificados = _consultar  # type: ignore[method-assign]
    portal.tablas_hoja = _tablas  # type: ignore[method-assign]
    portal._descargar_certificados = _pdfs  # type: ignore[method-assign]

    planilla, pdfs = asyncio.run(
        portal.descargar_certificados_depositante(
            destino_xlsx=tmp_path / "p.xlsx", destino_dir=tmp_path, desde="01/08/2025", hasta="31/10/2025"
        )
    )

    assert llamadas == [("01/08/2025", "31/10/2025", "D")]
    assert planilla.is_file() and pdfs == []
