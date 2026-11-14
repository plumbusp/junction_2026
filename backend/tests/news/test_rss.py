"""RSS parsing from fixture files and polite fetching over a mocked transport."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.news.feeds import Feed
from app.news.rss import USER_AGENT, fetch_feed, fetch_items, parse_feed

CIVIC = Feed("civic_wire", "Civic Wire", "https://civic-wire.example/rss", "left")
LEDGER = Feed(
    "daily_ledger", "The Daily Ledger", "https://daily-ledger.example/atom", "center"
)


def _bytes(fixture_dir: Path, name: str) -> bytes:
    return (fixture_dir / name).read_bytes()


def _client(handler) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT}
    )


def test_parse_rss_fixture_gives_expected_items(fixture_dir: Path) -> None:
    items = parse_feed(_bytes(fixture_dir, "civic_wire.xml"), CIVIC)

    # duplicate link collapsed, javascript: link skipped
    assert [i.title for i in items] == [
        "Central bank hikes interest rates again, squeezing struggling families",
        "Wildfire forces thousands to evacuate in Pine Valley",
        "City council approves new bike lanes downtown",
        "Government faces backlash over school funding plan",
    ]
    first = items[0]
    assert first.link == "https://civic-wire.example/economy/central-bank-hike"
    assert first.published == datetime(2026, 11, 13, 9, 15, tzinfo=UTC)
    assert first.outlet == "civic_wire"
    assert first.declared_leaning == "left"


def test_parse_atom_unescapes_and_stores_no_body(fixture_dir: Path) -> None:
    items = parse_feed(_bytes(fixture_dir, "daily_ledger.xml"), LEDGER)

    titles = [i.title for i in items]
    assert "Tech giant reports record quarterly profit & job cuts" in titles
    record = items[0].to_dict()
    assert set(record) == {"title", "link", "outlet", "published", "declared_leaning"}
    assert record["published"] == "2026-11-13T08:00:00Z"
    assert "never be stored" not in str(record)


def test_parse_strips_tags_and_skips_undated(fixture_dir: Path) -> None:
    frontier = Feed(
        "frontier_post", "Frontier Post", "https://frontier-post.example/rss", "right"
    )
    northgate = Feed(
        "northgate_herald",
        "Northgate Herald",
        "https://northgate-herald.example/rss",
        "lean_left",
    )

    assert "Senate passes defence spending bill" in [
        i.title for i in parse_feed(_bytes(fixture_dir, "frontier_post.xml"), frontier)
    ]
    northgate_titles = [
        i.title
        for i in parse_feed(_bytes(fixture_dir, "northgate_herald.xml"), northgate)
    ]
    assert len(northgate_titles) == 4
    assert "Undated item that must be skipped" not in northgate_titles


def test_parse_garbage_returns_empty_and_str_is_refused() -> None:
    assert parse_feed(b"this is not xml at all", CIVIC) == []
    with pytest.raises(TypeError):
        parse_feed("https://civic-wire.example/rss", CIVIC)  # type: ignore[arg-type]


def test_fetch_sends_user_agent_and_parses(fixture_dir: Path) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=_bytes(fixture_dir, "civic_wire.xml"))

    report = fetch_items([CIVIC], _client(handler), sleep=lambda _: None)

    assert len(seen) == 1
    assert seen[0].headers["User-Agent"] == "spot-the-spin/0.1"
    assert len(report.items) == 4
    assert report.fetched_outlets == ["civic_wire"]


def test_transient_error_is_retried_once_then_skipped() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503)

    report = fetch_items([CIVIC], _client(handler), sleep=lambda _: None)

    assert len(calls) == 2  # one request plus one retry, no storm
    assert report.failed_outlets == ["civic_wire"]
    assert report.items == []


def test_client_error_is_not_retried() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(404)

    assert fetch_feed(CIVIC, _client(handler), sleep=lambda _: None) is None
    assert len(calls) == 1


def test_timeout_then_success_recovers(fixture_dir: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, content=_bytes(fixture_dir, "civic_wire.xml"))

    assert fetch_feed(CIVIC, _client(handler), sleep=lambda _: None) is not None
    assert len(calls) == 2


def test_oversized_body_is_skipped_without_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"x" * 5000)

    assert (
        fetch_feed(CIVIC, _client(handler), sleep=lambda _: None, max_bytes=1000)
        is None
    )
    assert len(calls) == 1


def test_same_feed_url_is_requested_once(fixture_dir: Path) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=_bytes(fixture_dir, "civic_wire.xml"))

    fetch_items([CIVIC, CIVIC], _client(handler), sleep=lambda _: None)

    assert len(calls) == 1
