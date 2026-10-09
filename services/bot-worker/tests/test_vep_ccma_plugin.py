"""Paridad de ``plugin.execute`` de ``vep_ccma`` con V1 (``bot_vep_ccma``).

Se ejecuta el plugin completo con una sesion, un servicio y un artefacto
falsos. Lo que se fija es lo que V1 hace con los filtros de capital e
intereses, el conteo ``total_seleccionado`` y el QR cuando el medio es ``qr``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.errors import InvalidInputError
from bot_worker.bots.vep_ccma.plugin import VepCcmaPlugin
from bot_worker.runtime.context import ArtifactStore, CancellationToken

CUIT = "20123456786"
PDF = b"%PDF-1.4 vep"
PNG = b"\x89PNG fake"

IMPUESTOS = [
    {"checkbox_index": 0, "tipo": "capital", "periodo": "01/2026", "impuesto": "20",
     "concepto": "19", "subconcepto": "19", "categoria": "A", "importe": 100.0},
    {"checkbox_index": 1, "tipo": "capital", "periodo": "02/2026", "impuesto": "20",
     "concepto": "19", "subconcepto": "19", "categoria": "B", "importe": 200.0},
]
INTERESES = [
    {"checkbox_index": 2, "tipo": "interes", "periodo": "01/2026", "impuesto": "20",
     "concepto": "19", "subconcepto": "51", "categoria": "", "importe": 5.0},
]


class _Servicio:
    """Portal falso: registra la seleccion que recibe y simula el volante."""

    def __init__(self, *, qr: bool) -> None:
        self._qr = qr
        self.representado: str | None = None
        self.medio_pago: str = ""
        self.generar_kwargs: dict[str, Any] = {}
        self.volante_devuelto: list[dict[str, Any]] = []
        self.pdf_escrito: Path | None = None
        self.qr_escrito: Path | None = None

    async def seleccionar_representado(self, cuit: str) -> None:
        self.representado = cuit

    async def listar_obligaciones(self) -> dict[str, list[dict[str, Any]]]:
        return {"impuestos": list(IMPUESTOS), "intereses": list(INTERESES)}

    async def generar_volante(self, **kwargs: Any) -> dict[str, Any]:
        self.generar_kwargs = kwargs
        self.medio_pago = kwargs.get("medio_pago", "")
        return {"volante": list(self.volante_devuelto), "total": len(self.volante_devuelto)}

    async def descargar_detalle(self, destino: Path) -> Path:
        Path(destino).write_bytes(PDF)
        self.pdf_escrito = Path(destino)
        return Path(destino)

    async def descargar_qr(self, destino: Path) -> bool:
        # Contrato del portal real: el QR solo existe si el medio es ``qr``.
        if not self._qr or self.medio_pago != "qr":
            return False
        Path(destino).write_bytes(PNG)
        self.qr_escrito = Path(destino)
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

    async def __call__(self, artifact_id: str, content_type: str, size: int) -> dict[str, Any]:
        self.ids.append(artifact_id)
        return {"upload_url": "", "object_key": f"jobs/x/{artifact_id}"}


def _runtime(tmp_path: Path, servicio: _Servicio, presign: _Presign) -> SimpleNamespace:
    return SimpleNamespace(
        job_id="job-vep-ccma",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia", cuit_representante="20111111116"),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path,
            slots={},
            presign=presign,
            declarados={
                "vep_ccma_detalle.pdf": (("application/pdf",), 52_428_800),
                "vep_ccma_qr.png": (("image/png",), 52_428_800),
            },
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(_Sesion(servicio)),
        cancellation=CancellationToken(),
    )


def _ejecutar(tmp_path: Path, *, qr: bool = False, volante: list[dict[str, Any]] | None = None,
              **entrada: Any) -> tuple[Any, _Servicio, _Presign]:
    plugin = VepCcmaPlugin()
    payload = {"operacion": "generar", "representado_cuit": CUIT, **entrada}
    validado = asyncio.run(plugin.validate(payload))
    servicio = _Servicio(qr=qr)
    servicio.volante_devuelto = list(volante if volante is not None else IMPUESTOS + INTERESES)
    presign = _Presign()
    resultado = asyncio.run(plugin.execute(validado, _runtime(tmp_path, servicio, presign)))
    return resultado, servicio, presign


def test_sin_filtros_toma_todas_las_obligaciones(tmp_path: Path) -> None:
    resultado, servicio, _ = _ejecutar(tmp_path)

    assert resultado.result == "OK"
    assert servicio.representado == CUIT
    assert servicio.generar_kwargs["filtro_impuestos"] is None
    assert servicio.generar_kwargs["filtro_intereses"] is None
    assert resultado.data["total_impuestos"] == 2
    assert resultado.data["total_intereses"] == 1


def test_filtros_de_capital_e_intereses_llegan_al_portal(tmp_path: Path) -> None:
    resultado, servicio, _ = _ejecutar(
        tmp_path,
        filtro_impuestos=[{"categoria": "B"}],
        filtro_intereses=[{"subcpto": "51"}],
        volante=[IMPUESTOS[1], INTERESES[0]],
    )

    assert servicio.generar_kwargs["filtro_impuestos"] == [{"categoria": "B"}]
    assert servicio.generar_kwargs["filtro_intereses"] == [{"subcpto": "51"}]
    # total_seleccionado cuenta las filas ya filtradas, como V1
    assert resultado.data["total_seleccionado"] == 2
    assert [f["checkbox_index"] for f in resultado.data["volante"]] == [1, 2]


def test_total_seleccionado_sin_generar_volante_cuenta_la_seleccion(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path, generar_volante=False)

    assert resultado.result == "OK"
    assert "volante" not in resultado.data
    assert resultado.data["total_seleccionado"] == 3
    assert servicio.pdf_escrito is None
    assert presign.ids == []


def test_medio_qr_sube_pdf_y_qr_como_artefactos(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path, qr=True, medio_pago="qr")

    assert presign.ids == ["vep_ccma_detalle_pdf", "vep_ccma_qr_png"]
    assert [a["artifact_id"] for a in resultado.artifacts] == [
        "vep_ccma_detalle_pdf",
        "vep_ccma_qr_png",
    ]
    assert resultado.data["nombre_qr"].endswith("QR.png")
    assert "sha256" in resultado.data and "sha256_qr" in resultado.data


def test_medio_no_qr_no_intenta_ni_sube_qr(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path, qr=True, medio_pago="link")

    assert presign.ids == ["vep_ccma_detalle_pdf"]
    assert "nombre_qr" not in resultado.data
    assert "sha256_qr" not in resultado.data


def test_subir_pdf_false_no_sube_nada_y_conserva_el_pdf_local(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path, qr=True, medio_pago="qr", minio_upload=False)

    assert presign.ids == []
    assert resultado.artifacts == []
    assert servicio.pdf_escrito is not None and servicio.pdf_escrito.exists()


@pytest.mark.parametrize("alias", ["minio_upload", "subir_a_minio", "subir_pdf"])
def test_alias_de_subida_se_respetan_los_tres_nombres(tmp_path: Path, alias: str) -> None:
    _, _, presign = _ejecutar(tmp_path, **{alias: False})
    assert presign.ids == []


def test_credenciales_y_proxy_del_payload_se_descartan(tmp_path: Path) -> None:
    resultado, _, _ = _ejecutar(
        tmp_path,
        clave_representante="no-debe-usarse",
        proxy_request=True,
        cuit_representante="20999999999",
    )

    assert resultado.result == "OK"
    assert resultado.data["representado_cuit"] == CUIT


def test_flags_contradictorios_se_rechazan_antes_del_portal(tmp_path: Path) -> None:
    plugin = VepCcmaPlugin()
    with pytest.raises(InvalidInputError):
        asyncio.run(
            plugin.validate(
                {
                    "operacion": "generar",
                    "representado_cuit": CUIT,
                    "subir_pdf": False,
                    "minio_upload": True,
                }
            )
        )


def test_medio_qr_es_valido_y_paypal_no(tmp_path: Path) -> None:
    plugin = VepCcmaPlugin()
    asyncio.run(plugin.validate({"operacion": "generar", "representado_cuit": CUIT, "medio_pago": "qr"}))
    with pytest.raises(InvalidInputError):
        asyncio.run(
            plugin.validate({"operacion": "generar", "representado_cuit": CUIT, "medio_pago": "paypal"})
        )
