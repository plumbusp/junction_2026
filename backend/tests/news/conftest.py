"""News tests never touch the network: real httpx transports are blocked."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.news.feeds import Feed
from app.news.rss import USER_AGENT

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "rss"
FIXTURE_NOW = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def block_real_http(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(self, request):
        raise AssertionError(f"network access attempted in a test: {request.url}")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)


@pytest.fixture
def fixture_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def fixture_feeds() -> Callable[..., tuple[list[Feed], httpx.Client]]:
    """Build ``(feeds, client)`` serving the fixture RSS files through a MockTransport."""

    def build(down: Collection[str] = ()) -> tuple[list[Feed], httpx.Client]:
        manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text("utf-8"))
        feeds = [
            Feed(
                e["outlet"],
                e["name"],
                f"https://{e['outlet']}.example/rss",
                e["leaning"],
            )
            for e in manifest["feeds"]
        ]
        files = {f"{e['outlet']}.example": e["file"] for e in manifest["feeds"]}

        def handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            if host not in files or host.split(".")[0] in down:
                return httpx.Response(404)
            return httpx.Response(200, content=(FIXTURE_DIR / files[host]).read_bytes())

        client = httpx.Client(
            transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT}
        )
        return feeds, client

    return build
