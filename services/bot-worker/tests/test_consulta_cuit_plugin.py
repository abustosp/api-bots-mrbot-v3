"""Paridad de ``plugin.execute`` de ``consulta_cuit`` con V1.

V1 (``consulta_cuit`` / ``consulta_cuit_masiva``) consulta la API externa de
constancias y devuelve la respuesta JSON. El plugin V3 fija el mismo contrato
de entrada (``cuit`` y ``cuits``), los mismos parametros de auth y el
resultado. El transporte HTTP se simula con ``httpx.MockTransport``.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from bot_worker.bots.consulta_cuit import plugin as modulo
from bot_worker.bots.consulta_cuit.plugin import ConsultaCuitPlugin, URL_MASIVA, URL_SIMPLE
from bot_worker.bots.errors import InvalidInputError, TargetUnavailableError
from bot_worker.runtime.context import CancellationToken

CUIT = "20123456786"
RESPUESTA = {"cuit": CUIT, "razon_social": "EJEMPLO SA", "estado": "ACTIVO"}


class _Sink:
    async def progress(self, **_: Any) -> None:
        return None


def _runtime(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        job_id="job-cuit",
        work_dir=tmp_path,
        deadline=SimpleNamespace(remaining_seconds=lambda maximum=None: 60),
        credentials=None,
        proxy=None,
        artifact_store=None,
        event_sink=_Sink(),
        cancellation=CancellationToken(),
    )


def _con_transporte(monkeypatch: pytest.MonkeyPatch, manejador: Any, registro: list[httpx.Request]) -> None:
    """Reemplaza el cliente del plugin por uno con transporte falso."""

    def manejar(request: httpx.Request) -> httpx.Response:
        registro.append(request)
        return manejador(request)

    original = httpx.AsyncClient

    def fabrica(**kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return original(transport=httpx.MockTransport(manejar), **kwargs)

    monkeypatch.setattr(modulo.httpx, "AsyncClient", fabrica)


def _ejecutar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manejador: Any, *,
              plugin: ConsultaCuitPlugin | None = None, **entrada: Any) -> tuple[Any, list[httpx.Request]]:
    registro: list[httpx.Request] = []
    _con_transporte(monkeypatch, manejador, registro)
    plugin = plugin or ConsultaCuitPlugin()
    validado = asyncio.run(plugin.validate(entrada))
    resultado = asyncio.run(plugin.execute(validado, _runtime(tmp_path)))
    return resultado, registro


def test_consulta_individual_devuelve_la_constancia(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resultado, registro = _ejecutar(
        tmp_path, monkeypatch,
        lambda r: httpx.Response(200, json=RESPUESTA),
        operacion="consultar", cuit="20-12345678-6",
    )

    assert resultado.result == "OK"
    assert resultado.data == RESPUESTA
    assert len(registro) == 1
    assert registro[0].url.path == "/consulta_constancia/"
    assert registro[0].url.params["cuit"] == CUIT
    assert registro[0].method == "GET"


def test_consulta_masiva_envia_la_lista_por_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resultado, registro = _ejecutar(
        tmp_path, monkeypatch,
        lambda r: httpx.Response(200, json={"resultados": [RESPUESTA]}),
        operacion="consultar_masivo", cuits=[CUIT, "27222222222", CUIT],
    )

    assert resultado.result == "OK"
    assert registro[0].method == "POST"
    assert registro[0].url.path == "/consulta_constancia_masiva/"
    assert json.loads(registro[0].content) == {"cuits": [CUIT, "27222222222"]}


def test_credenciales_del_sobre_viajan_como_parametros(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = ConsultaCuitPlugin().configure({"cuit_usuario": "usr-x", "cuit_api_key": "key-x"})

    _, registro = _ejecutar(
        tmp_path, monkeypatch,
        lambda r: httpx.Response(200, json=RESPUESTA),
        plugin=plugin, operacion="consultar", cuit=CUIT,
    )

    assert registro[0].url.params["usuario"] == "usr-x"
    assert registro[0].url.params["api_key"] == "key-x"


def test_sin_credenciales_no_se_agregan_parametros_de_auth(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, registro = _ejecutar(
        tmp_path, monkeypatch,
        lambda r: httpx.Response(200, json=RESPUESTA),
        operacion="consultar", cuit=CUIT,
    )

    assert "usuario" not in registro[0].url.params
    assert "api_key" not in registro[0].url.params


def test_configure_con_url_propia_se_usa_en_la_consulta(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plugin = ConsultaCuitPlugin().configure({"cuit_base_url": "https://otro.example/consulta/"})

    _, registro = _ejecutar(
        tmp_path, monkeypatch,
        lambda r: httpx.Response(200, json=RESPUESTA),
        plugin=plugin, operacion="consultar", cuit=CUIT,
    )

    assert str(registro[0].url).startswith("https://otro.example/consulta/")


def test_respuesta_que_no_es_objeto_da_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    resultado, _ = _ejecutar(
        tmp_path, monkeypatch,
        lambda r: httpx.Response(200, json=["no", "objeto"]),
        operacion="consultar", cuit=CUIT,
    )

    assert resultado.result == "ERROR"
    assert resultado.errors[0].internal_diagnostic == "respuesta no es objeto JSON"


def test_http_500_se_informa_como_objetivo_no_disponible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(TargetUnavailableError) as error:
        _ejecutar(
            tmp_path, monkeypatch,
            lambda r: httpx.Response(503, text="mantenimiento"),
            operacion="consultar", cuit=CUIT,
        )
    assert "http 503" in str(error.value)


def test_error_de_red_no_filtra_credenciales(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def caer(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"no conecta usuario=usr-x api_key=key-x")

    plugin = ConsultaCuitPlugin().configure({"cuit_usuario": "usr-x", "cuit_api_key": "key-x"})
    with pytest.raises(TargetUnavailableError) as error:
        _ejecutar(tmp_path, monkeypatch, caer, plugin=plugin, operacion="consultar", cuit=CUIT)

    assert "usr-x" not in str(error.value)
    assert "key-x" not in str(error.value)


def test_cuit_invalido_no_llega_a_la_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    registro: list[httpx.Request] = []
    _con_transporte(monkeypatch, lambda r: httpx.Response(200, json=RESPUESTA), registro)
    plugin = ConsultaCuitPlugin()

    with pytest.raises(InvalidInputError):
        asyncio.run(plugin.validate({"operacion": "consultar", "cuit": "123"}))
    assert registro == []


def test_masiva_con_mas_de_cien_cuits_se_rechaza(tmp_path: Path) -> None:
    plugin = ConsultaCuitPlugin()
    cuits = [f"2{str(i).zfill(10)}" for i in range(101)]
    with pytest.raises(InvalidInputError):
        asyncio.run(plugin.validate({"operacion": "consultar_masivo", "cuits": cuits}))


def test_urls_por_defecto_son_las_de_v1() -> None:
    assert URL_SIMPLE.endswith("/consulta_constancia/")
    assert URL_MASIVA.endswith("/consulta_constancia_masiva/")
