from __future__ import annotations

import asyncio
import base64
from typing import Any

import pytest

import bot_worker.runtime.captcha as captcha_module
from bot_worker.runtime.captcha import CaptchaSolver, CaptchaUnsolvableError


class _Response:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._data


class _AsyncClient:
    def __init__(self, *, responses: list[dict[str, Any]]) -> None:
        self.responses = responses
        self.requests: list[tuple[str, dict[str, Any]]] = []

    async def __aenter__(self) -> _AsyncClient:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, Any]) -> _Response:
        self.requests.append((url, json))
        return _Response(self.responses.pop(0))


def test_from_profile_redacts_key_and_bounds_values() -> None:
    solver = CaptchaSolver.from_profile(
        "arca",
        {
            "enabled": "true",
            "arca_key": "provider-test-key",
            "timeout_seconds": "9999",
            "poll_seconds": "0",
            "max_attempts": "bad",
            "case": "false",
            "threshold": "250",
        },
    )

    assert solver.enabled is True
    assert solver.timeout_seconds == 300
    assert solver.poll_seconds == 1
    assert solver.max_attempts == 5
    assert solver.case_sensitive is False
    assert solver.recognizing_threshold == 100
    assert "provider-test-key" not in repr(solver)


def test_solver_create_poll_response_without_real_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _AsyncClient(
        responses=[
            {"errorId": 0, "taskId": "task-1"},
            {"errorId": 0, "status": "ready", "solution": {"text": "AB12"}},
        ]
    )
    monkeypatch.setattr(
        captcha_module.httpx,
        "AsyncClient",
        lambda **_kwargs: fake,
    )

    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(captcha_module.asyncio, "sleep", no_sleep)
    solver = CaptchaSolver(
        enabled=True,
        api_key="provider-test-key",
        timeout_seconds=5,
        poll_seconds=1,
    )
    solution = asyncio.run(solver.solve_image(b"captcha-bytes"))

    assert solution == "AB12"
    create_url, create_payload = fake.requests[0]
    result_url, result_payload = fake.requests[1]
    assert create_url.endswith("/createTask")
    assert create_payload["task"]["body"] == base64.b64encode(b"captcha-bytes").decode()
    assert create_payload["clientKey"] == "provider-test-key"
    assert result_url.endswith("/getTaskResult")
    assert result_payload["taskId"] == "task-1"


def test_solver_sanea_error_de_proveedor_sin_divulgar_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _AsyncClient(responses=[{"errorId": 1, "errorCode": "invalid_key"}])
    monkeypatch.setattr(
        captcha_module.httpx,
        "AsyncClient",
        lambda **_kwargs: fake,
    )
    solver = CaptchaSolver(enabled=True, api_key="provider-test-key")

    with pytest.raises(CaptchaUnsolvableError) as exc:
        asyncio.run(solver.solve_image(b"captcha-bytes"))

    assert "provider-test-key" not in str(exc.value)
    assert "invalid_key" not in str(exc.value)
