"""Daily job: offline dry run on fixtures, file output, and the feed list."""

from __future__ import annotations

import importlib.util
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.config import REPO_ROOT
from app.news import job
from app.news.feeds import FEEDS, LEANINGS, Feed
from app.news.rss import USER_AGENT

ALLOWED_HEADLINE_KEYS = {
    "title",
    "link",
    "outlet",
    "outlet_name",
    "published",
    "declared_leaning",
}


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_dry_run_writes_expected_candidates(fixture_dir: Path, tmp_path: Path) -> None:
    result = job.run_dry(fixture_dir, tmp_path)

    assert result.path == tmp_path / "candidates-2026-11-13.jsonl"
    records = _read(result.path)
    assert sorted(tuple(r["outlets"]) for r in records) == [
        ("civic_wire", "daily_ledger", "frontier_post", "harbor_times"),
        ("daily_ledger", "frontier_post", "northgate_herald"),
    ]
    # 2-outlet wildfire story and the out-of-window election story are absent
    all_titles = " ".join(h["title"] for r in records for h in r["headlines"])
    assert "Wildfire" not in all_titles
    assert "commission" not in all_titles


def test_records_hold_only_allowed_fields_and_no_labels(
    fixture_dir: Path, tmp_path: Path
) -> None:
    records = _read(job.run_dry(fixture_dir, tmp_path).path)

    for record in records:
        assert record["mode"] == "dry_run"
        assert record["labelled"] is False
        assert "technique" not in record
        assert "source" in record["leaning_note"]
        for headline in record["headlines"]:
            assert set(headline) == ALLOWED_HEADLINE_KEYS
    assert "must never be stored" not in json.dumps(records)


def test_run_rejects_naive_time(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        job.run([], [], now=datetime(2026, 11, 13), out_dir=tmp_path, mode="dry_run")


def test_live_path_with_mocked_transport_reports_failed_feed(
    fixture_dir: Path, tmp_path: Path
) -> None:
    feeds = [
        Feed("civic_wire", "Civic Wire", "https://civic-wire.example/rss", "left"),
        Feed(
            "daily_ledger",
            "The Daily Ledger",
            "https://daily-ledger.example/atom",
            "center",
        ),
        Feed(
            "harbor_times",
            "Harbor Times",
            "https://harbor-times.example/rss",
            "lean_right",
        ),
        Feed("broken", "Broken Feed", "https://broken.example/rss", "center"),
    ]
    files = {
        "civic-wire.example": "civic_wire.xml",
        "daily-ledger.example": "daily_ledger.xml",
        "harbor-times.example": "harbor_times.xml",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        name = files.get(request.url.host)
        if name is None:
            return httpx.Response(404)
        return httpx.Response(200, content=(fixture_dir / name).read_bytes())

    client = httpx.Client(
        transport=httpx.MockTransport(handler), headers={"User-Agent": USER_AGENT}
    )
    now = datetime(2026, 11, 13, 18, 0, tzinfo=UTC)

    result = job.run_live(tmp_path, feeds=feeds, client=client, now=now)

    assert result.failed_outlets == ["broken"]
    assert [c.outlets for c in result.candidates] == [
        ["civic_wire", "daily_ledger", "harbor_times"]
    ]
    assert _read(result.path)[0]["mode"] == "live"


def test_configured_feeds_are_varied_english_outlets() -> None:
    assert 8 <= len(FEEDS) <= 15
    assert all(f.language == "en" and f.url.startswith("https://") for f in FEEDS)
    assert {f.leaning for f in FEEDS} == set(LEANINGS)
    with pytest.raises(ValueError):
        Feed("x", "X", "https://x.example/rss", "far_left")


def test_run_daily_script_dry_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spec = importlib.util.spec_from_file_location(
        "run_daily", REPO_ROOT / "scripts" / "news" / "run_daily.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--dry-run", "--out-dir", str(tmp_path)]) == 0
    assert (tmp_path / "candidates-2026-11-13.jsonl").exists()
    assert "candidate clusters (>= 3 outlets): 2" in capsys.readouterr().out
