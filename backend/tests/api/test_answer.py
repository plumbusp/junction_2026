"""POST /api/answer: the worked example (87, 27, 100, round 71) and answer rules."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.db.rounds_fixture import CLEAN, FEAR, LOADED


def _items_by_text(client: TestClient) -> tuple[str, dict[str, str]]:
    body = client.get("/api/round").json()
    return body["round_id"], {item["text"]: item["item_id"] for item in body["items"]}


def _answer(
    client: TestClient, round_id: str, item_id: str, technique: str, words: list[str]
):
    return client.post(
        "/api/answer",
        json={
            "round_id": round_id,
            "item_id": item_id,
            "technique": technique,
            "selected_words": words,
        },
    )


def test_worked_example_scores_87_27_100_and_round_71(
    client: TestClient, seeded
) -> None:
    round_id, ids = _items_by_text(client)

    first = _answer(client, round_id, ids[LOADED], "loaded_language", ["slams"])
    assert first.status_code == 200
    body = first.json()
    assert set(body) == {"round_id", "item_id", "player", "reference", "score", "round"}
    assert body["player"] == {
        "technique": "loaded_language",
        "selected_words": ["slams"],
    }
    assert body["reference"]["evidence_spans"] == ["slams", "rip-off"]
    assert len(body["reference"]["votes"]) == 3
    assert body["score"] == {
        "technique_points": 60,
        "evidence_points": 27,
        "total": 87,
        "token_f1": 0.6667,
        "precision": 1.0,
        "recall": 0.5,
        "no_technique_bonus": False,
    }
    assert body["round"] == {"answered": 1, "total_items": 3, "round_score": None}

    second = _answer(client, round_id, ids[FEAR], "doubt", ["drown"]).json()
    assert second["score"]["total"] == 27

    third = _answer(client, round_id, ids[CLEAN], "no_technique", []).json()
    assert third["score"]["total"] == 100
    assert third["score"]["no_technique_bonus"] is True
    assert third["round"] == {"answered": 3, "total_items": 3, "round_score": 71}


def test_second_answer_for_an_item_is_409(client: TestClient, seeded) -> None:
    round_id, ids = _items_by_text(client)
    assert _answer(client, round_id, ids[LOADED], "doubt", []).status_code == 200

    again = _answer(client, round_id, ids[LOADED], "loaded_language", ["slams"])

    assert again.status_code == 409
    assert again.json()["error"] == "already_answered"


def test_unknown_technique_is_422_and_unknown_item_is_404(
    client: TestClient, seeded
) -> None:
    round_id, ids = _items_by_text(client)

    bad = _answer(client, round_id, ids[LOADED], "propaganda", [])
    assert bad.status_code == 422
    assert bad.json()["error"] == "invalid_request"

    missing = _answer(client, round_id, "no-such-item", "doubt", [])
    assert missing.status_code == 404


def test_finished_round_is_not_served_again(client: TestClient, seeded) -> None:
    round_id, ids = _items_by_text(client)
    for item_id in ids.values():
        _answer(client, round_id, item_id, "no_technique", [])

    res = client.get("/api/round")

    assert res.status_code == 409
    assert res.json()["error"] == "all_rounds_played"
