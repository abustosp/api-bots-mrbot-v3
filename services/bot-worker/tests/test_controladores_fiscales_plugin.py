"""Paridad de ``plugin.execute`` de ``controladores_fiscales`` con V1.

V1 (``controladores_fiscales_bot``) presenta cada archivo y, si uno falla, lo
registra y sigue con el siguiente. El resultado global es parcial. Estos tests
fijan ese comportamiento con un servicio falso, sin navegador.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from bot_worker.bots.controladores_fiscales.plugin import ControladoresFiscalesPlugin
from bot_worker.bots.errors import CaptchaUnsolvableError, InvalidInputError, TargetUnavailableError
from bot_worker.runtime.context import ArtifactStore, CancellationToken

PDF = b"%PDF-1.4 constancia"


class _Servicio:
    """Portal falso: cada archivo puede dar ``estado`` o fallar con TargetUnavailable."""

    def __init__(self, respuestas: dict[str, Any]) -> None:
        self._respuestas = respuestas
        self.presentados: list[str] = []

    async def presentar_archivo(self, archivo: str, destino: Path) -> str:
        nombre = Path(archivo).name
        self.presentados.append(nombre)
        respuesta = self._respuestas.get(nombre, "presentado_correctamente")
        if isinstance(respuesta, BaseException):
            raise respuesta
        Path(destino).write_bytes(PDF)
        return str(respuesta)


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


def _preparar(tmp_path: Path, nombres: list[str]) -> None:
    for nombre in nombres:
        (tmp_path / nombre).write_text("contenido", encoding="utf-8")


def _ejecutar(tmp_path: Path, nombres: list[str], respuestas: dict[str, Any] | None = None,
              **entrada: Any) -> tuple[Any, _Servicio, _Presign]:
    _preparar(tmp_path, nombres)
    plugin = ControladoresFiscalesPlugin()
    validado = asyncio.run(plugin.validate({"operacion": "presentar", "archivos": nombres, **entrada}))
    servicio = _Servicio(respuestas or {})
    presign = _Presign()
    runtime = SimpleNamespace(
        job_id="job-controladores",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia", cuit_representante="20111111116"),
        proxy=None,
        artifact_store=ArtifactStore(
            tmp_path,
            slots={},
            presign=presign,
            declarados={"constancia.pdf": (("application/pdf",), 52_428_800)},
        ),
        event_sink=_Sink(),
        browser_factory=_Fabrica(_Sesion(servicio)),
        cancellation=CancellationToken(),
    )
    resultado = asyncio.run(plugin.execute(validado, runtime))
    return resultado, servicio, presign


def test_todos_presentados_da_ok_y_sube_cada_constancia(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path, ["F8010.001.pem", "F8011.002.pem"])

    assert resultado.result == "OK"
    assert servicio.presentados == ["F8010.001.pem", "F8011.002.pem"]
    assert resultado.data["total_exitosos"] == 2
    assert resultado.data["total_errores"] == 0
    assert len(presign.ids) == 2
    assert all(i == "constancia_pdf" for i in presign.ids)


def test_fallo_de_un_archivo_no_corta_el_lote_y_da_parcial(tmp_path: Path) -> None:
    resultado, servicio, _ = _ejecutar(
        tmp_path,
        ["F8010.001.pem", "F8010.002.pem", "F8011.003.pem"],
        respuestas={"F8010.002.pem": TargetUnavailableError("x", diagnostic_code="controladores_presentacion_error")},
    )

    # El tercero se presenta igual aunque el segundo fallo
    assert servicio.presentados == ["F8010.001.pem", "F8010.002.pem", "F8011.003.pem"]
    assert resultado.result == "PARCIAL"
    assert resultado.data["total_exitosos"] == 2
    assert resultado.data["total_errores"] == 1
    fallido = [d for d in resultado.data["detalle"] if d["archivo"] == "F8010.002.pem"][0]
    assert fallido["estado"] == "error|controladores_presentacion_error"
    assert fallido["constancia"] is None


def test_todos_fallidos_da_error_no_ok(tmp_path: Path) -> None:
    fallo = TargetUnavailableError("x", diagnostic_code="controladores_presentacion_timeout")
    resultado, _, _ = _ejecutar(
        tmp_path,
        ["F8010.001.pem", "F8010.002.pem"],
        respuestas={"F8010.001.pem": fallo, "F8010.002.pem": fallo},
    )

    assert resultado.result == "ERROR"
    assert resultado.data["total_errores"] == 2
    assert resultado.errors and resultado.errors[0].internal_diagnostic == "presentacion_todos_los_archivos_fallaron"


def test_error_no_reintentable_de_captcha_corta_el_lote(tmp_path: Path) -> None:
    # Un captcha sin resolver es un error del job, no de un archivo: corta.
    with pytest.raises(CaptchaUnsolvableError):
        _ejecutar(
            tmp_path,
            ["F8010.001.pem", "F8010.002.pem"],
            respuestas={"F8010.001.pem": CaptchaUnsolvableError("captcha")},
        )


def test_subir_constancia_false_no_sube_nada(tmp_path: Path) -> None:
    resultado, servicio, presign = _ejecutar(tmp_path, ["F8010.001.pem"], subir_constancia=False)

    assert presign.ids == []
    assert resultado.artifacts == []
    assert resultado.result == "OK"
    assert servicio.presentados == ["F8010.001.pem"]


def test_incluir_json_false_omite_el_detalle(tmp_path: Path) -> None:
    resultado, _, _ = _ejecutar(tmp_path, ["F8010.001.pem"], incluir_json=False)

    assert "detalle" not in resultado.data
    assert resultado.data["total_exitosos"] == 1


def test_archivo_fuera_del_work_dir_se_rechaza_antes_de_presentar(tmp_path: Path) -> None:
    plugin = ControladoresFiscalesPlugin()
    with pytest.raises(InvalidInputError):
        asyncio.run(plugin.validate({"operacion": "presentar", "archivos": ["../secreto.pem"]}))


def test_archivo_inexistente_en_work_dir_se_rechaza(tmp_path: Path) -> None:
    # Nombre valido para el schema pero sin archivo en el work_dir: el plugin
    # lo rechaza antes de abrir el portal.
    plugin = ControladoresFiscalesPlugin()
    validado = asyncio.run(plugin.validate({"operacion": "presentar", "archivos": ["F8010.999.pem"]}))
    runtime = SimpleNamespace(
        job_id="job-controladores",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda: 60),
        credentials=SimpleNamespace(clave="clave-ficticia", cuit_representante="20111111116"),
        proxy=None,
        artifact_store=ArtifactStore(tmp_path, slots={}, presign=_Presign()),
        event_sink=_Sink(),
        browser_factory=_Fabrica(_Sesion(_Servicio({}))),
        cancellation=CancellationToken(),
    )
    with pytest.raises(InvalidInputError) as error:
        asyncio.run(plugin.execute(validado, runtime))
    assert "no encontrado en work_dir" in str(error.value)
