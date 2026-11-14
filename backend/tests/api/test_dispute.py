"""POST /api/dispute: queued -> in_review after three distinct players; never touches metrics."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.db.rounds_fixture import FEAR


def _dispute(client: TestClient, round_id: str, item_id: str):
    return client.post(
        "/api/dispute",
        json={
            "round_id": round_id,
            "item_id": item_id,
            "reference_technique": "appeal_to_fear",
            "player_technique": "exaggeration_minimization",
            "player_words": ["drown"],
            "comment": "Sounds like exaggeration to me.",
        },
    )


def _fear_item(client: TestClient) -> tuple[str, str]:
    body = client.get("/api/round").json()
    item = next(i for i in body["items"] if i["text"] == FEAR)
    return body["round_id"], item["item_id"]


def test_dispute_is_queued_with_dev_target(
    client: TestClient, seeded, metrics_path: Path
) -> None:
    round_id, item_id = _fear_item(client)

    res = _dispute(client, round_id, item_id)

    assert res.status_code == 200
    body = res.json()
    assert set(body) == {
        "dispute_id",
        "round_id",
        "item_id",
        "status",
        "disputes_on_item",
        "review_threshold",
        "target_set",
        "message",
    }
    assert body["status"] == "queued"
    assert body["disputes_on_item"] == 1
    assert body["review_threshold"] == 3
    assert body["target_set"] == "dev"
    assert not metrics_path.exists()


def test_repeat_vote_counts_once_and_three_players_start_review(
    make_client, seeded
) -> None:
    first = make_client()
    round_id, item_id = _fear_item(first)
    _dispute(first, round_id, item_id)
    repeat = _dispute(first, round_id, item_id).json()
    assert repeat["disputes_on_item"] == 1
    assert repeat["status"] == "queued"

    second = make_client()
    second.get("/api/round")
    assert _dispute(second, round_id, item_id).json()["status"] == "queued"

    third = make_client()
    third.get("/api/round")
    body = _dispute(third, round_id, item_id).json()
    assert body["status"] == "in_review"
    assert body["disputes_on_item"] == 3
    assert body["dispute_id"] == repeat["dispute_id"]


def test_dispute_on_unknown_item_is_404(client: TestClient, seeded) -> None:
    res = _dispute(client, seeded.round_id, "nope")

    assert res.status_code == 404
