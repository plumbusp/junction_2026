"""GET /api/round: shape of frontend/mock/round.json, a clean headline, no answers leaked."""

from __future__ import annotations

from pathlib import Path

from app import db
from app.api.deps import PLAYER_COOKIE
from fastapi.testclient import TestClient

from tests.db.rounds_fixture import DISPUTED


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
    for leaked in ("technique", "evidence_spans", "votes", "agreement", "explanation"):
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


def test_no_rounds_is_503_json(client: TestClient) -> None:
    res = client.get("/api/round")

    assert res.status_code == 503
    assert res.json()["error"] == "no_rounds"
