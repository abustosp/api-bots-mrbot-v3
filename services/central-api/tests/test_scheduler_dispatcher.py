from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from central_api.scheduler.dispatcher import _limpio  # noqa: E402


def test_secret_dev_placeholder_is_not_provisioned() -> None:
    assert _limpio("dev-placeholder") is None


def test_real_secret_value_is_preserved_for_sealed_provisioning() -> None:
    assert _limpio("configured-test-key") == "configured-test-key"
