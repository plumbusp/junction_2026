"""News tests never touch the network: real httpx transports are blocked."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rss"


@pytest.fixture(autouse=True)
def block_real_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self, request):
        raise AssertionError(f"network access attempted in a test: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR
