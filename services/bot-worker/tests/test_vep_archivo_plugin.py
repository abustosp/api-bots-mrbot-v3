"""Paridad de ``plugin.execute`` de ``vep_archivo`` con V1 (``generar_vep_desde_archivo``).

Con un servicio y una sesion falsos se fija lo que V1 hace con el medio de
pago ``qr``: el QR se pide y se sube solo en ese caso, y los flags de subida
y de JSON se respetan como en V1.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.errors import InvalidInputError
from bot_worker.bots.vep_archivo.plugin import VepArchivoPlugin
from bot_worker.runtime.context import ArtifactStore, CancellationToken

CUIT = "20123456786"
PDF = b"%PDF-1.4 vep"
PNG = b"\x89PNG fake"
TXT = "01" + CUIT.zfill(11) + "\n02<VEP/>\n02<VEP/>\n"


class _Servicio:
    """Portal falso: registra el medio pedido y si se le pidio el QR."""

    def __init__(self, *, qr_disponible: bool) -> None:
        self._qr_disponible = qr_disponible
        self.medio_pago: str | None = None
        self.qr_pedidos = 0
        self.pdf_escrito: Path | None = None

    async def generar_vep_desde_archivo(self, archivo: Path, medio_pago: str) -> dict[str, Any]:
        self.medio_pago = medio_pago
        return {"lote": "001de001", "registros": 2}

    async def descargar_detalle(self, destino: Path) -> Path:
        Path(destino).write_bytes(PDF)
        self.pdf_escrito = Path(destino)
        return Path(destino)

    async def descargar_qr(self, destino: Path) -> bool:
        self.qr_pedidos += 1
        # Contrato del portal: el QR existe solo si el medio fue ``qr``.
        if not self._qr_disponible or self.medio_pago != "qr":
            return False
        Path(destino).write_bytes(PNG)
        return True


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


def _ejecutar(tmp_path: Path, *, qr_disponible: bool = True, **entrada: Any) -> tuple[Any, _Servicio, _Presign]:
    plugin = VepArchivoPlugin()
    archivo_path = entrada.setdefault("archivo_path", "vep.txt")
    (tmp_path / archivo_path).write_text(TXT, encoding="utf-8")
    payload = {"operacion": "generar", **entrada}
    validado = asyncio.run(plugin.validate(payload))
    servicio = _Servicio(qr_disponible=qr_disponible)
    presign = _Presign()
    runtime = SimpleNamespace(
        job_id="job-vep-archivo",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia", cuit_representante=CUIT),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path,
            slots={},
            presign=presign,
            declarados={
                "vep_archivo_detalle.pdf": (("application/pdf",), 52_428_800),
                "vep_archivo_qr.png": (("image/png",), 52_428_800),
            },
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(_Sesion(servicio)),
        cancellation=CancellationToken(),
    )
    resultado = asyncio.run(plugin.execute(validado, runtime))
    return resultado, servicio, presign


def test_medio_qr_pide_y_sube_pdf_y_qr(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path, medio_pago="qr", representado_cuit=CUIT)

    assert resultado.result == "OK"
    assert servicio.qr_pedidos == 1
    assert presign.ids == ["vep_archivo_detalle_pdf", "vep_archivo_qr_png"]
    assert [a["artifact_id"] for a in resultado.artifacts] == [
        "vep_archivo_detalle_pdf",
        "vep_archivo_qr_png",
    ]
    assert resultado.data["nombre_qr"].endswith("QR.png")
    assert "sha256_qr" in resultado.data


@pytest.mark.parametrize("medio", ["link", "pago_mis_cuentas", "internet_banking", "xn_group"])
def test_otros_medios_no_piden_ni_suben_qr(tmp_path: Path, medio: str) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path, qr_disponible=True, medio_pago=medio)

    assert servicio.qr_pedidos == 0
    assert presign.ids == ["vep_archivo_detalle_pdf"]
    assert "nombre_qr" not in resultado.data
    assert "sha256_qr" not in resultado.data


def test_subir_pdf_false_no_sube_qr_ni_pdf(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path, medio_pago="qr", minio_upload=False)

    assert presign.ids == []
    assert resultado.artifacts == []
    # El QR igual se genera localmente: V1 lo guarda aunque no se suba.
    assert resultado.data["nombre_qr"].endswith("QR.png")
    assert servicio.pdf_escrito is not None and servicio.pdf_escrito.exists()


def test_incluir_json_false_deja_solo_el_detalle(tmp_path: Path) -> None:
    resultado, _, _ = _ejecutar(tmp_path, medio_pago="link", incluir_json=False)

    assert set(resultado.data) == {"detalle"}


def test_datos_de_entrada_se_leen_con_nombres_v1(tmp_path: Path) -> None:
    resultado, servicio, _ = _ejecutar(
        tmp_path,
        medio_pago="internet_banking",
        cuit_inicio_sesion="20-12345678-6",
        contrasena="no-debe-usarse",
    )

    assert resultado.data["representado_cuit"] == "20123456786"
    assert servicio.medio_pago == "internet_banking"


def test_medio_no_habilitado_se_rechaza_antes_del_portal(tmp_path: Path) -> None:
    plugin = VepArchivoPlugin()
    with pytest.raises(InvalidInputError):
        asyncio.run(
            plugin.validate(
                {"operacion": "generar", "archivo_path": "vep.txt", "medio_pago": "paypal"}
            )
        )


def test_archivo_no_encontrado_en_work_dir_se_rechaza_antes_del_portal(tmp_path: Path) -> None:
    plugin = VepArchivoPlugin()
    validado = asyncio.run(
        plugin.validate({"operacion": "generar", "archivo_path": "ausente.txt"})
    )
    servicio = _Servicio(qr_disponible=False)
    runtime = SimpleNamespace(
        work_dir=tmp_path,
        artifact_store=SimpleNamespace(resolve=lambda nombre: tmp_path / nombre),
        credentials=SimpleNamespace(clave="clave", cuit_representante=CUIT),
        cancellation=CancellationToken(),
        event_sink=_Sink(),
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
    )

    with pytest.raises(InvalidInputError):
        asyncio.run(plugin.execute(validado, runtime))
    assert servicio.medio_pago is None
