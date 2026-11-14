"""POST /api/analyze: the global daily cap (spend protection)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app import db
from app.api import analyze as analyze_api
from fastapi.testclient import TestClient

TEXT = {"text": "The weather in the capital is mild this week."}


def _client(make_client, cap: int, **kwargs) -> TestClient:
    return make_client(
        analyze_limit=100,
        settings={"analyze_daily_cap": cap, "daily_tz": "UTC"},
        **kwargs,
    )


def test_calls_over_the_cap_get_a_friendly_429(make_client) -> None:
    client = _client(make_client, 2)

    codes = [client.post("/api/analyze", json=TEXT).status_code for _ in range(3)]

    assert codes == [200, 200, 429]
    res = client.post("/api/analyze", json=TEXT)
    assert res.json()["error"] == "daily_limit"
    assert "tomorrow" in res.json()["detail"]
    assert 1 <= int(res.headers["retry-after"]) <= 86400


def test_a_failed_analysis_does_not_use_up_the_cap(
    make_client, tmp_db_path: Path, monkeypatch
) -> None:
    client = _client(make_client, 1)
    real = analyze_api.classify

    def broken(text: str):
        raise RuntimeError("boom")

    monkeypatch.setattr(analyze_api, "classify", broken)
    assert client.post("/api/analyze", json=TEXT).status_code == 503
    assert client.post("/api/analyze", json={"text": "x" * 6000}).status_code == 413
    today = datetime.now(UTC).date().isoformat()
    assert db.analyze_count(tmp_db_path, today) == 0

    monkeypatch.setattr(analyze_api, "classify", real)
    assert client.post("/api/analyze", json=TEXT).status_code == 200
    assert client.post("/api/analyze", json=TEXT).status_code == 429


def test_invalid_input_does_not_use_up_the_cap(make_client) -> None:
    client = _client(make_client, 1)

    assert client.post("/api/analyze", json={}).status_code == 422
    assert client.post("/api/analyze", json=TEXT).status_code == 200


def test_the_count_starts_over_on_the_next_local_day(make_client) -> None:
    now = [datetime(2026, 11, 15, 12, 0, tzinfo=UTC)]
    client = _client(make_client, 1, clock=lambda: now[0])

    assert client.post("/api/analyze", json=TEXT).status_code == 200
    assert client.post("/api/analyze", json=TEXT).status_code == 429
    now[0] += timedelta(days=1)
    assert client.post("/api/analyze", json=TEXT).status_code == 200


def test_cap_zero_means_unlimited(make_client, tmp_db_path: Path) -> None:
    client = _client(make_client, 0)

    codes = {client.post("/api/analyze", json=TEXT).status_code for _ in range(8)}

    assert codes == {200}
    assert db.analyze_count(tmp_db_path, datetime.now(UTC).date().isoformat()) == 0


def test_the_per_ip_limit_still_answers_first(make_client) -> None:
    client = make_client(analyze_limit=1, settings={"analyze_daily_cap": 5})

    client.post("/api/analyze", json=TEXT)
    res = client.post("/api/analyze", json=TEXT)

    assert res.status_code == 429 and res.json()["error"] == "rate_limited"
