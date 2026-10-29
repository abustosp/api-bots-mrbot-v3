"""Paridad de ``plugin.execute`` de ``certificado_mipyme`` con V1.

V1 (``descargar_certificado_mipyme``) descarga el certificado del representado
pedido. El plugin V3 fija dos cosas: que el PDF se guarda y se sube por slot, y
que el representado se selecciona antes de descargar.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.certificado_mipyme.plugin import CertificadoMipymePlugin, nombre_certificado
from bot_worker.bots.errors import InvalidInputError, TargetUnavailableError
from bot_worker.runtime.context import ArtifactStore, CancellationToken
from bot_worker.runtime.portals import portal_para
from bot_worker.runtime.portals.certificado_mipyme import CertificadoMipymePortal

CUIT = "20123456786"
PDF = b"%PDF-1.4 certificado"


class _Servicio:
    def __init__(self, *, contenido: bytes = PDF) -> None:
        self.contenido = contenido
        self.representado: str | None = None
        self.destino: Path | None = None

    async def seleccionar_representado(self, cuit: str) -> None:
        self.representado = cuit

    async def descargar_certificado(self, destino: Path) -> Path:
        self.destino = Path(destino)
        Path(destino).write_bytes(self.contenido)
        return Path(destino)


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


def _ejecutar(tmp_path: Path, *, contenido: bytes = PDF, **entrada: Any) -> tuple[Any, _Servicio, _Presign]:
    plugin = CertificadoMipymePlugin()
    validado = asyncio.run(
        plugin.validate({"operacion": "descargar", "representado_cuit": CUIT, **entrada})
    )
    servicio = _Servicio(contenido=contenido)
    presign = _Presign()
    runtime = SimpleNamespace(
        job_id="job-mipyme",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia", cuit_representante="20111111116"),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path,
            slots={},
            presign=presign,
            declarados={"certificado.pdf": (("application/pdf",), 10_485_760)},
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(_Sesion(servicio)),
        cancellation=CancellationToken(),
    )
    resultado = asyncio.run(plugin.execute(validado, runtime))
    return resultado, servicio, presign


def test_se_registra_por_descubrimiento() -> None:
    assert portal_para("certificado_mipyme") is CertificadoMipymePortal


def test_selecciona_representado_descarga_y_sube(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path)

    assert resultado.result == "OK"
    assert servicio.representado == CUIT
    assert servicio.destino is not None and servicio.destino.parent == tmp_path
    assert servicio.destino.name.startswith(f"sepyme_{CUIT}_")
    assert presign.ids == ["certificado.pdf"]
    assert resultado.data["archivo"] == servicio.destino.name
    assert "sha256" in resultado.data


def test_subir_false_no_sube_pero_deja_el_pdf_local(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path, subir=False)

    assert presign.ids == []
    assert resultado.artifacts == []
    assert servicio.destino is not None and servicio.destino.exists()


def test_proxy_y_clave_del_payload_se_descartan(tmp_path: Path) -> None:
    resultado, servicio, _ = _ejecutar(
        tmp_path,
        proxy_request=True,
        clave="no-debe-usarse",
        cuit_representante="20999999999",
    )

    assert resultado.result == "OK"
    assert servicio.representado == CUIT


def test_cuit_con_nombre_del_contrato_se_acepta(tmp_path: Path) -> None:
    plugin = CertificadoMipymePlugin()
    validado = asyncio.run(plugin.validate({"operacion": "descargar", "cuit_representado": "20-12345678-6"}))
    assert validado[1].representado_cuit == CUIT


def test_archivo_que_no_es_pdf_se_rechaza_antes_de_subir(tmp_path: Path) -> None:
    # El portal valida la cabecera %PDF- antes de devolver el archivo.
    portal = CertificadoMipymePortal.__new__(CertificadoMipymePortal)
    destino = tmp_path / "x.pdf"
    destino.write_bytes(b"<html>error</html>")

    async def _capturar(_: Path, __: Any, espera_ms: int = 0) -> Path:
        return destino

    portal.capturar_descarga = _capturar  # type: ignore[method-assign]
    portal.paso = lambda _n, coro: coro  # type: ignore[method-assign]
    portal._contenido_pagina = lambda: _vacio()  # type: ignore[method-assign]
    portal._verificar_respuesta_servicio = lambda _c: None  # type: ignore[method-assign]

    class _Enlace:
        async def wait_for(self, **_: Any) -> None:
            return None

        async def click(self, **_: Any) -> None:
            return None

    class _Pagina:
        def get_by_role(self, *_: Any, **__: Any) -> _Enlace:
            return _Enlace()

    portal._page = _Pagina()
    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(portal.descargar_certificado(tmp_path / "x.pdf"))
    assert error.value.diagnostic_code == "mipyme_certificate_invalid_pdf"


async def _vacio() -> str:
    return ""


def test_representado_invalido_se_rechaza_antes_del_navegador() -> None:
    plugin = CertificadoMipymePlugin()
    with pytest.raises(InvalidInputError):
        asyncio.run(plugin.validate({"operacion": "descargar", "representado_cuit": "123"}))


def test_cuit_ausente_en_lufe_falla_sin_descargar_ni_subir(tmp_path: Path) -> None:
    """Divergencia deliberada respecto de V1/V2.

    V1/V2 elegían la primera opción del selector LUFE cuando el CUIT pedido no
    aparecía, y así podían descargar el certificado de otra entidad. V3 no lo
    hace: el portal agota la búsqueda y falla con ``represented_cuit_not_selectable``.
    Este test fija que, en ese caso, el plugin no descarga ni sube nada.
    """

    class _ServicioSinCuit:
        def __init__(self) -> None:
            self.descargo = False

        async def seleccionar_representado(self, cuit: str) -> None:
            raise TargetUnavailableError(
                "no se encontró el CUIT representado solicitado en el selector LUFE",
                diagnostic_code="represented_cuit_not_selectable",
            )

        async def descargar_certificado(self, destino: Path) -> Path:
            self.descargo = True
            Path(destino).write_bytes(PDF)
            return Path(destino)

    servicio = _ServicioSinCuit()
    plugin = CertificadoMipymePlugin()
    validado = asyncio.run(
        plugin.validate({"operacion": "descargar", "representado_cuit": CUIT})
    )
    presign = _Presign()
    runtime = SimpleNamespace(
        job_id="job-mipyme-sin-cuit",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia", cuit_representante="20111111116"),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path,
            slots={},
            presign=presign,
            declarados={"certificado.pdf": (("application/pdf",), 10_485_760)},
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(_Sesion(servicio)),
        cancellation=CancellationToken(),
    )

    with pytest.raises(TargetUnavailableError) as error:
        asyncio.run(plugin.execute(validado, runtime))

    assert error.value.diagnostic_code == "represented_cuit_not_selectable"
    assert servicio.descargo is False
    assert presign.ids == []
    assert list(tmp_path.glob("sepyme_*.pdf")) == []


def test_nombre_certificado_sigue_el_patron_v1() -> None:
    nombre = nombre_certificado(CUIT)
    assert nombre.startswith(f"sepyme_{CUIT}_") and nombre.endswith(".pdf")


def test_un_504_del_portal_se_informa_como_gateway_timeout():
    import pytest

    from bot_worker.bots.errors import TargetUnavailableError
    from bot_worker.runtime.portals.certificado_mipyme import CertificadoMipymePortal

    with pytest.raises(TargetUnavailableError) as err:
        CertificadoMipymePortal._verificar_respuesta_servicio(
            "<h1>504 | Gateway Timeout</h1> BunkerWeb"
        )
    assert "504" in str(err.value)


def test_un_500_del_portal_se_informa_como_error_del_servidor():
    import pytest

    from bot_worker.bots.errors import TargetUnavailableError
    from bot_worker.runtime.portals.certificado_mipyme import CertificadoMipymePortal

    with pytest.raises(TargetUnavailableError) as err:
        CertificadoMipymePortal._verificar_respuesta_servicio(
            "<h1>500 | Internal Server Error</h1> BunkerWeb"
        )
    assert err.value.diagnostic_code == "mipyme_service_server_error"
