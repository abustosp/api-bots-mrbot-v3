"""Pruebas del port MiPyME sin navegador ni credenciales reales."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from bot_worker.bots.errors import TargetUnavailableError
from bot_worker.runtime.portals import PORTALES, portal_para
from bot_worker.runtime.portals.certificado_mipyme import CertificadoMipymePortal

_CUIT_FICTICIO = "20999999999"


class _Opcion:
    def __init__(self, texto: str) -> None:
        self.texto = texto
        self.seleccionada = False

    async def inner_text(self) -> str:
        return self.texto

    async def click(self, **_: Any) -> None:
        self.seleccionada = True


class _Opciones:
    def __init__(self, textos: list[str]) -> None:
        self.elementos = [_Opcion(texto) for texto in textos]

    async def count(self) -> int:
        return len(self.elementos)

    def nth(self, indice: int) -> _Opcion:
        return self.elementos[indice]


class _Locator:
    def __init__(self, *, visible: bool = False) -> None:
        self.visible = visible
        self.clicks = 0

    @property
    def first(self) -> _Locator:
        return self

    async def count(self) -> int:
        return int(self.visible)

    async def is_visible(self, **_: Any) -> bool:
        return self.visible

    async def wait_for(self, **_: Any) -> None:
        if not self.visible:
            raise TimeoutError("elemento ausente")

    async def click(self, **_: Any) -> None:
        if not self.visible:
            raise TimeoutError("elemento ausente")
        self.clicks += 1

    async def fill(self, _: str) -> None:
        return None


class _Combo(_Locator):
    def __init__(self) -> None:
        super().__init__(visible=True)


class _Download:
    def __init__(self, contenido: bytes) -> None:
        self.contenido = contenido

    async def save_as(self, destino: str) -> None:
        Path(destino).write_bytes(self.contenido)


class _DownloadContext:
    def __init__(self, contenido: bytes) -> None:
        self._download = _Download(contenido)

    @property
    def value(self) -> Any:
        async def obtener() -> _Download:
            return self._download

        return obtener()

    async def __aenter__(self) -> _DownloadContext:
        return self

    async def __aexit__(self, *_: Any) -> None:
        return None


class _Pagina:
    def __init__(
        self,
        textos: list[str] | None = None,
        pdf: bytes = b"",
        contenido: str = "",
    ) -> None:
        self.opciones = _Opciones(textos or [])
        self.combo = _Combo()
        self.enlace = _Locator(visible=True)
        self.pdf = pdf
        self.contenido = contenido

    def get_by_role(self, rol: str, name: Any = None) -> _Locator:
        if rol == "combobox":
            return self.combo
        if rol == "link":
            return self.enlace
        return _Locator()

    def locator(self, _: str) -> _Opciones:
        return self.opciones

    async def wait_for_timeout(self, _: int) -> None:
        return None

    async def wait_for_load_state(self, *_: Any, **__: Any) -> None:
        return None

    async def content(self) -> str:
        return self.contenido

    def expect_download(self, **_: Any) -> _DownloadContext:
        return _DownloadContext(self.pdf)


def test_portal_mipyme_se_registra_por_descubrimiento() -> None:
    assert portal_para("certificado_mipyme") is CertificadoMipymePortal
    assert PORTALES["certificado_mipyme"] is CertificadoMipymePortal


def test_selecciona_solo_el_representado_con_cuit_coincidente() -> None:
    pagina = _Pagina(
        [
            "Empresa no solicitada 20-11111111-1",
            "Empresa ficticia 20-99999999-9",
        ]
    )

    asyncio.run(CertificadoMipymePortal(pagina).seleccionar_representado(_CUIT_FICTICIO))

    assert [opcion.seleccionada for opcion in pagina.opciones.elementos] == [False, True]


def test_no_selecciona_la_primera_opcion_si_el_cuit_no_coincide() -> None:
    pagina = _Pagina(["Empresa distinta 20-11111111-1"])

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(
            CertificadoMipymePortal(pagina).seleccionar_representado(_CUIT_FICTICIO)
        )

    assert exc.value.diagnostic_code == "represented_cuit_not_selectable"
    assert pagina.opciones.elementos[0].seleccionada is False


def test_clasifica_gateway_timeout_del_servicio_lufe() -> None:
    pagina = _Pagina(contenido="<title>504 | Gateway Timeout</title>")

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(
            CertificadoMipymePortal(pagina).seleccionar_representado(_CUIT_FICTICIO)
        )

    assert exc.value.diagnostic_code == "mipyme_service_gateway_timeout"


def test_detecta_gateway_timeout_antes_de_esperar_el_enlace(tmp_path: Path) -> None:
    pagina = _Pagina(contenido="<title>504 | Gateway Timeout</title>")

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(
            CertificadoMipymePortal(pagina).descargar_certificado(
                tmp_path / "certificado.pdf"
            )
        )

    assert exc.value.diagnostic_code == "mipyme_service_gateway_timeout"


def test_descarga_y_valida_un_pdf(tmp_path: Path) -> None:
    destino = tmp_path / "certificado.pdf"
    pagina = _Pagina(pdf=b"%PDF-1.7\ncontenido ficticio")

    resultado = asyncio.run(
        CertificadoMipymePortal(pagina).descargar_certificado(destino)
    )

    assert resultado == destino
    assert destino.read_bytes().startswith(b"%PDF-")
    assert destino.stat().st_size > 0


def test_rechaza_descarga_que_no_sea_pdf(tmp_path: Path) -> None:
    pagina = _Pagina(pdf=b"HTML de error")

    with pytest.raises(TargetUnavailableError) as exc:
        asyncio.run(
            CertificadoMipymePortal(pagina).descargar_certificado(
                tmp_path / "certificado.pdf"
            )
        )

    assert exc.value.diagnostic_code == "mipyme_certificate_invalid_pdf"
