"""GET /api/round: shape of frontend/mock/round.json, a clean headline, no answers leaked."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app import db
from app.api.deps import PLAYER_COOKIE
from app.news.job import LINEUP_EVENT, LINEUP_NOTE
from fastapi.testclient import TestClient

from tests.db.rounds_fixture import (
    DISPUTED,
    LIVE_MODELS,
    seed_simple_round,
    stub_verdict,
)


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


def _answer_items(
    client: TestClient, tmp_db_path: Path, round_id: str, *, only: int | None = None
) -> None:
    """Answer the round's items in order with "doubt" and no words (``only``: first N)."""
    items = db.get_round(tmp_db_path, round_id).items[:only]
    for item in items:
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


COMPLETED_KEYS = {
    "completed",
    "daily",
    "date",
    "next_edition_at",
    "round_id",
    "round_score",
    "items",
}
ITEM_KEYS = {
    "item_id",
    "text",
    "outlet",
    "player_technique",
    "reference_technique",
    "total",
}


def test_completed_daily_returns_the_closed_state_with_the_players_recap(
    make_client, tmp_db_path: Path
) -> None:
    daily = seed_simple_round(tmp_db_path, "daily", n_outlets=9)
    db.pick_daily_challenge(tmp_db_path, TODAY, [daily.round_id], "stub")
    client = make_client(clock=lambda: NOON_UTC)
    assert "completed" not in client.get("/api/round").json()

    _answer_items(client, tmp_db_path, daily.round_id)
    res = client.get("/api/round")

    assert res.status_code == 200
    body = res.json()
    assert set(body) == COMPLETED_KEYS
    assert body["completed"] is True and body["daily"] is True
    assert body["date"] == TODAY and body["round_id"] == daily.round_id
    # Midnight at the default job time, in Helsinki (UTC+2 in November).
    assert body["next_edition_at"] == "2026-11-15T00:00:00+02:00"
    assert [set(item) for item in body["items"]] == [ITEM_KEYS] * 3
    stored = db.get_round(tmp_db_path, daily.round_id).items  # in play order
    assert [i["item_id"] for i in body["items"]] == [s.item_id for s in stored]
    assert [i["reference_technique"] for i in body["items"]] == [
        s.reference.technique for s in stored
    ]
    assert sorted(i["reference_technique"] for i in body["items"]) == [
        "appeal_to_fear",
        "loaded_language",
        "no_technique",
    ]
    assert all(i["player_technique"] == "doubt" for i in body["items"])
    # "doubt" with no words: 0 on the two flagged headlines, 40 (evidence only) on the
    # clean one; the round score is their mean (13.3, rounded).
    assert sorted(i["total"] for i in body["items"]) == [0, 0, 40]
    assert body["round_score"] == 13
    # The headlines are not offered again as a playable round.
    assert "event" not in body and "source_note" not in body


def test_no_other_round_is_served_after_the_daily_is_completed(
    make_client, tmp_db_path: Path
) -> None:
    daily = seed_simple_round(tmp_db_path, "daily", n_outlets=9)
    db.pick_daily_challenge(tmp_db_path, TODAY, [daily.round_id], "stub")
    client = make_client(clock=lambda: NOON_UTC)
    _answer_items(client, tmp_db_path, daily.round_id)
    # Rounds that appear later (or older ones) never replace the finished challenge.
    seed_simple_round(tmp_db_path, "newer", published="2026-11-14T09:00:00Z")
    seed_simple_round(tmp_db_path, "older", published="2026-11-01T10:00:00Z")

    for _ in range(3):
        body = client.get("/api/round").json()
        assert body["completed"] is True and body["round_id"] == daily.round_id

    # A fresh player still gets the daily (not a different round).
    other = make_client(clock=lambda: NOON_UTC)
    other_body = other.get("/api/round").json()
    assert other_body["round_id"] == daily.round_id and "completed" not in other_body


def test_partial_progress_keeps_serving_the_same_open_round(
    make_client, tmp_db_path: Path
) -> None:
    daily = seed_simple_round(tmp_db_path, "daily", n_outlets=9)
    db.pick_daily_challenge(tmp_db_path, TODAY, [daily.round_id], "stub")
    client = make_client(clock=lambda: NOON_UTC)
    _answer_items(client, tmp_db_path, daily.round_id, only=1)

    res = client.get("/api/round")

    body = res.json()
    assert set(body) == {"round_id", "date", "event", "source_note", "items", "daily"}
    assert body["round_id"] == daily.round_id and len(body["items"]) == 3
    # Nothing about the answered item (or any technique) leaks while the round is open.
    for leaked in ("technique", "completed", "round_score", "total", "next_edition_at"):
        assert leaked not in res.text
    # The one answer stands: answering that item again is still 409.
    first = db.get_round(tmp_db_path, daily.round_id).items[0]
    again = client.post(
        "/api/answer",
        json={
            "round_id": daily.round_id,
            "item_id": first.item_id,
            "technique": "doubt",
            "selected_words": [],
        },
    )
    assert again.status_code == 409 and again.json()["error"] == "already_answered"
    # Finishing the remaining two items completes the round.
    for item in db.get_round(tmp_db_path, daily.round_id).items[1:]:
        client.post(
            "/api/answer",
            json={
                "round_id": daily.round_id,
                "item_id": item.item_id,
                "technique": "doubt",
                "selected_words": [],
            },
        )
    assert client.get("/api/round").json()["completed"] is True


def test_without_a_daily_a_finished_player_gets_the_completed_state_not_a_409(
    make_client, tmp_db_path: Path
) -> None:
    old = seed_simple_round(tmp_db_path, "old", published="2026-11-12T10:00:00Z")
    newer = seed_simple_round(tmp_db_path, "newer", published="2026-11-13T10:00:00Z")
    client = make_client(clock=lambda: NOON_UTC)

    assert client.get("/api/round").json()["round_id"] == newer.round_id
    _answer_items(client, tmp_db_path, newer.round_id)
    # Fallback: the player's next unfinished round, then the completed state.
    assert client.get("/api/round").json()["round_id"] == old.round_id
    _answer_items(client, tmp_db_path, old.round_id)
    body = client.get("/api/round").json()

    assert set(body) == COMPLETED_KEYS
    assert body["completed"] is True and body["daily"] is False
    assert body["round_id"] == old.round_id  # the one finished last
    assert body["date"] == "2026-11-12"
    assert body["next_edition_at"] == "2026-11-15T00:00:00+02:00"


def test_a_new_day_brings_a_new_daily_challenge(
    make_client, tmp_db_path: Path
) -> None:
    first = seed_simple_round(tmp_db_path, "day1", n_outlets=9)
    second = seed_simple_round(tmp_db_path, "day2", n_outlets=8)
    db.pick_daily_challenge(tmp_db_path, TODAY, [first.round_id], "stub")
    db.pick_daily_challenge(tmp_db_path, "2026-11-15", [second.round_id], "stub")
    now = {"value": NOON_UTC}
    client = make_client(clock=lambda: now["value"])
    _answer_items(client, tmp_db_path, first.round_id)
    assert client.get("/api/round").json()["completed"] is True

    now["value"] = datetime(2026, 11, 14, 22, 0, 1, tzinfo=UTC)  # 00:00:01 on the 15th

    body = client.get("/api/round").json()
    assert body["round_id"] == second.round_id and body["daily"] is True
    assert body["date"] == "2026-11-15" and "completed" not in body


def test_next_edition_follows_the_job_time_in_the_configured_zone(
    make_client, tmp_db_path: Path
) -> None:
    daily = seed_simple_round(tmp_db_path, "daily", n_outlets=9)
    db.pick_daily_challenge(tmp_db_path, TODAY, [daily.round_id], "stub")
    settings = {"daily_job_hour": 6, "daily_job_minute": 30}  # 04:30 UTC in November
    clock = {"value": datetime(2026, 11, 14, 4, 29, tzinfo=UTC)}
    client = make_client(clock=lambda: clock["value"], settings=settings)
    _answer_items(client, tmp_db_path, daily.round_id)

    # Just before the slot, today's challenge exists (made by hand): the next edition is
    # tomorrow's, not the slot that is still ahead today.
    assert client.get("/api/round").json()["next_edition_at"] == (
        "2026-11-15T06:30:00+02:00"
    )
    # Just after the slot.
    clock["value"] = datetime(2026, 11, 14, 4, 31, tzinfo=UTC)
    assert client.get("/api/round").json()["next_edition_at"] == (
        "2026-11-15T06:30:00+02:00"
    )


def test_next_edition_without_a_daily_is_the_next_slot_even_if_it_is_today(
    make_client, tmp_db_path: Path
) -> None:
    only = seed_simple_round(tmp_db_path, "only")
    settings = {"daily_job_hour": 6, "daily_job_minute": 30}
    clock = {"value": datetime(2026, 11, 14, 4, 29, tzinfo=UTC)}  # 06:29 local
    client = make_client(clock=lambda: clock["value"], settings=settings)
    _answer_items(client, tmp_db_path, only.round_id)

    body = client.get("/api/round").json()
    assert body["daily"] is False
    assert body["next_edition_at"] == "2026-11-14T06:30:00+02:00"

    clock["value"] = datetime(2026, 11, 14, 4, 30, tzinfo=UTC)  # exactly on the slot
    assert client.get("/api/round").json()["next_edition_at"] == (
        "2026-11-15T06:30:00+02:00"
    )


def test_next_edition_uses_the_zone_offset_and_daylight_saving() -> None:
    from app.api.round import next_edition_at
    from app.config import Settings

    summer = Settings(daily_tz="Europe/Helsinki", daily_job_hour=5, daily_job_minute=0)
    now = datetime(2026, 7, 1, 12, 0, tzinfo=UTC)
    assert next_edition_at(summer, now, daily_exists=True) == "2026-07-02T05:00:00+03:00"

    ny = Settings(daily_tz="America/New_York", daily_job_hour=0, daily_job_minute=0)
    late = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)  # still the 13th, 22:00 in New York
    assert next_edition_at(ny, late, daily_exists=True) == "2026-11-14T00:00:00-05:00"
    # The offset changes with daylight saving between now and the slot.
    before_fall_back = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)  # 16:00 EDT on the 31st
    assert next_edition_at(ny, before_fall_back, daily_exists=True) == (
        "2026-11-01T00:00:00-04:00"
    )


def test_completed_state_follows_the_players_local_date_not_utc(
    make_client, tmp_db_path: Path
) -> None:
    daily = seed_simple_round(tmp_db_path, "daily", n_outlets=9)
    db.pick_daily_challenge(tmp_db_path, "2026-11-14", [daily.round_id], "stub")
    # 22:30 UTC on the 14th is already 00:30 on the 15th in Helsinki: no daily today yet,
    # and the 14th's challenge is no longer "today's".
    late = datetime(2026, 11, 14, 22, 30, tzinfo=UTC)
    client = make_client(clock=lambda: late)

    body = client.get("/api/round").json()

    assert body["round_id"] == daily.round_id and "daily" not in body
    assert body["date"] == "2026-11-13"  # the round's own event date (fallback)


def test_lineup_round_is_served_with_the_normal_shape_and_leaks_nothing(
    make_client, tmp_db_path: Path
) -> None:
    refs = [
        stub_verdict("no_technique", []),
        stub_verdict("doubt", ["x"]),
        stub_verdict("appeal_to_fear", ["x"]),
    ]
    items = [
        {"text": f"Invented headline {n}", "outlet": f"Outlet {n}", "published": "2026-11-14T08:00:00Z"}
        for n in range(3)
    ]
    lineup = db.insert_candidate_round(
        tmp_db_path,
        event=LINEUP_EVENT,
        items=items,
        references=refs,
        cluster_id=f"lineup-{TODAY}",
        source_note=LINEUP_NOTE,
        n_outlets=3,
    )
    assert lineup.diversity == 3
    db.pick_daily_challenge(tmp_db_path, TODAY, [lineup.round_id], "stub")
    client = make_client(clock=lambda: NOON_UTC)

    res = client.get("/api/round")

    body = res.json()
    assert set(body) == {"round_id", "date", "event", "source_note", "items", "daily"}
    assert body["event"] == LINEUP_EVENT and body["daily"] is True
    assert all(set(item) == {"item_id", "text", "outlet"} for item in body["items"])
    for leaked in (
        "technique",
        "diversity",
        "no_technique",
        "doubt",
        "appeal_to_fear",
        "story",
        "leaning\":",
        "evidence",
        "votes",
    ):
        assert leaked not in res.text


def test_no_rounds_is_503_json(client: TestClient) -> None:
    res = client.get("/api/round")

    assert res.status_code == 503
    assert res.json()["error"] == "no_rounds"
