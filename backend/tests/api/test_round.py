"""GET /api/round: shape of frontend/mock/round.json, a clean headline, no answers leaked."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app import db
from app.api.deps import PLAYER_COOKIE
from fastapi.testclient import TestClient

from tests.db.rounds_fixture import DISPUTED, LIVE_MODELS, seed_simple_round


def test_round_shape_has_three_headlines_one_clean_and_no_references(
    client: TestClient, seeded, tmp_db_path: Path
) -> None:
    res = client.get("/api/round")

    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"round_id", "date", "event", "source_note", "items"}
    assert body["round_id"] == seeded.round_id
    assert body["date"] == "2026-11-13"
    assert "stub classifier" in body["source_note"]
    assert len(body["items"]) == 3
    assert all(set(item) == {"item_id", "text", "outlet"} for item in body["items"])
    # No reference answers or votes leak into the round.
    for leaked in (
        "technique",
        "evidence_spans",
        "votes",
        "agreement",
        "explanation",
        "diversity",
    ):
        assert leaked not in res.text
    # The disputed headline is excluded; one in-play headline is clean.
    assert DISPUTED not in [item["text"] for item in body["items"]]
    refs = [
        db.get_round_item(tmp_db_path, seeded.round_id, i["item_id"]).reference
        for i in body["items"]
    ]
    assert sum(r.technique == "no_technique" for r in refs) >= 1
    assert not any(r.disputed for r in refs)


def test_round_sets_an_anonymous_httponly_player_cookie(
    client: TestClient, seeded
) -> None:
    res = client.get("/api/round")

    cookie = res.headers["set-cookie"]
    assert cookie.startswith(f"{PLAYER_COOKIE}=")
    assert "httponly" in cookie.lower()


TODAY = "2026-11-14"
NOON_UTC = datetime(2026, 11, 14, 10, 0, tzinfo=UTC)  # 12:00 in Helsinki


def test_todays_daily_challenge_is_served_before_a_newer_round(
    make_client, tmp_db_path: Path
) -> None:
    daily = seed_simple_round(
        tmp_db_path, "daily", n_outlets=9, published="2026-11-12T10:00:00Z"
    )
    seed_simple_round(
        tmp_db_path, "newer", n_outlets=3, published="2026-11-13T10:00:00Z"
    )
    db.pick_daily_challenge(tmp_db_path, TODAY, [daily.round_id], "stub")
    client = make_client(clock=lambda: NOON_UTC)

    body = client.get("/api/round").json()

    assert body["round_id"] == daily.round_id
    assert body["daily"] is True and body["date"] == TODAY
    assert set(body) == {"round_id", "date", "event", "source_note", "items", "daily"}


def test_without_a_daily_challenge_today_the_newest_round_is_served(
    make_client, tmp_db_path: Path
) -> None:
    old = seed_simple_round(tmp_db_path, "old", published="2026-11-12T10:00:00Z")
    newer = seed_simple_round(tmp_db_path, "newer", published="2026-11-13T10:00:00Z")
    # Yesterday's challenge does not count today; a failed job today changes nothing.
    db.pick_daily_challenge(tmp_db_path, "2026-11-13", [old.round_id], "stub")
    db.claim_job_run(tmp_db_path, TODAY, now=NOON_UTC)
    db.finish_job_run(
        tmp_db_path, TODAY, status=db.JOB_FAILED, now=NOON_UTC, error="Boom"
    )
    client = make_client(clock=lambda: NOON_UTC)

    res = client.get("/api/round")

    assert res.status_code == 200
    assert res.json()["round_id"] == newer.round_id
    assert "daily" not in res.json()


def test_daily_challenge_labelled_in_another_core_mode_is_not_served(
    make_client, tmp_db_path: Path
) -> None:
    live = seed_simple_round(tmp_db_path, "live", models=LIVE_MODELS)
    stub = seed_simple_round(tmp_db_path, "stub", published="2026-11-01T10:00:00Z")
    db.pick_daily_challenge(tmp_db_path, TODAY, [live.round_id], "live")
    client = make_client(clock=lambda: NOON_UTC)

    body = client.get("/api/round").json()

    assert body["round_id"] == stub.round_id and "daily" not in body


def test_finished_daily_challenge_falls_through_to_the_next_round_then_409(
    make_client, tmp_db_path: Path
) -> None:
    daily = seed_simple_round(tmp_db_path, "daily", n_outlets=9)
    other = seed_simple_round(tmp_db_path, "other", published="2026-11-01T10:00:00Z")
    db.pick_daily_challenge(tmp_db_path, TODAY, [daily.round_id], "stub")
    client = make_client(clock=lambda: NOON_UTC)

    def finish(round_id: str) -> None:
        for item in db.get_round(tmp_db_path, round_id).items:
            res = client.post(
                "/api/answer",
                json={
                    "round_id": round_id,
                    "item_id": item.item_id,
                    "technique": "doubt",
                    "selected_words": [],
                },
            )
            assert res.status_code == 200, res.text

    assert client.get("/api/round").json()["round_id"] == daily.round_id
    finish(daily.round_id)
    assert client.get("/api/round").json()["round_id"] == other.round_id
    finish(other.round_id)
    assert client.get("/api/round").status_code == 409


def test_no_rounds_is_503_json(client: TestClient) -> None:
    res = client.get("/api/round")

    assert res.status_code == 503
    assert res.json()["error"] == "no_rounds"
