"""Pruebas unitarias del registro inicial del worker."""

from __future__ import annotations

import asyncio

import httpx

from bot_worker import main
from bot_worker.config import WorkerConfig


class _Response:
    def __init__(self, status_code: int, body: dict) -> None:
        self.status_code = status_code
        self._body = body

    def json(self) -> dict:
        return self._body


class _InvalidJsonResponse(_Response):
    def json(self) -> dict:
        raise ValueError("respuesta no es JSON")


class _Client:
    def __init__(self, responses: list[object]) -> None:
        self.responses = responses
        self.calls = 0

    async def post(self, *_args, **_kwargs):
        self.calls += 1
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _settings() -> WorkerConfig:
    return WorkerConfig(
        central_url="http://central:8000",
        advertised_url="http://worker:8080",
    )


def test_register_reintenta_connect_error_y_luego_acepta(monkeypatch):
    """Una caída transitoria de conexión no deja el worker sin identidad."""
    monkeypatch.setattr(main, "REGISTER_RETRY_DELAYS_SECONDS", (0.0,))
    client = _Client(
        [
            httpx.ConnectError(
                "central unavailable",
                request=httpx.Request(
                    "POST", "http://central:8000/internal/v1/workers/register"
                ),
            ),
            _Response(
                200,
                {
                    "accepted": True,
                    "service_token": "service-token-test",
                    "assignment_verify_key_pem": "verify-key-test",
                },
            ),
        ]
    )

    result = asyncio.run(main._register_once(client, _settings(), "pubkey", "nonce"))

    assert result == ("worker:8080", "service-token-test", "verify-key-test")
    assert client.calls == 2


def test_register_4xx_rechazado_no_es_exito_ni_se_reintenta():
    """Un rechazo definitivo 4xx nunca se interpreta como registro exitoso."""
    client = _Client([_Response(403, {"detail": "worker no inventariado"})])

    result = asyncio.run(main._register_once(client, _settings(), "pubkey", "nonce"))

    assert result == (None, "", "")
    assert client.calls == 1


def test_register_reintenta_5xx_y_luego_acepta(monkeypatch):
    """Un error del central recuperable debe repetirse antes de abandonar."""
    monkeypatch.setattr(main, "REGISTER_RETRY_DELAYS_SECONDS", (0.0,))
    client = _Client(
        [
            _Response(503, {"detail": "temporal"}),
            _Response(
                200,
                {
                    "accepted": True,
                    "service_token": "service-token-test",
                    "assignment_verify_key_pem": "verify-key-test",
                },
            ),
        ]
    )

    result = asyncio.run(main._register_once(client, _settings(), "pubkey", "nonce"))

    assert result == ("worker:8080", "service-token-test", "verify-key-test")
    assert client.calls == 2


def test_register_json_invalido_no_es_exito_ni_se_reintenta():
    """Una respuesta 2xx ilegible no debe marcar el worker como registrado."""
    client = _Client([_InvalidJsonResponse(200, {})])

    result = asyncio.run(main._register_once(client, _settings(), "pubkey", "nonce"))

    assert result == (None, "", "")
    assert client.calls == 1


def test_register_accepted_false_no_es_exito():
    """La respuesta HTTP exitosa con accepted=false no provisiona al worker."""
    client = _Client([_Response(200, {"accepted": False, "motivo": "protocolo"})])

    result = asyncio.run(main._register_once(client, _settings(), "pubkey", "nonce"))

    assert result == (None, "", "")
    assert client.calls == 1
