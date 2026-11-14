"""GET /api/stats/disputes: public counts only, short cache, nothing touches metrics."""

from __future__ import annotations

from pathlib import Path

from app.api.stats import StatsCache

from tests.db.disputes_fixture import COMMENT, answer, dispute, items_of
from tests.db.rounds_fixture import FEAR

TOP_KEYS = {
    "core_mode", "answers", "disputes", "dispute_rate", "by_status",
    "by_reference", "by_player_technique", "pairs",
}


def test_empty_state_is_zero_counts(make_client) -> None:
    res = make_client(stats_ttl=0).get("/api/stats/disputes")

    assert res.status_code == 200
    body = res.json()
    assert set(body) == TOP_KEYS
    assert body["disputes"] == {"items": 0, "votes": 0}
    assert body["dispute_rate"]["value"] is None
    assert body["pairs"] == []
    assert "max-age" in res.headers["cache-control"]


def test_stats_follow_the_api_and_leak_no_text_or_ids(
    make_client, tmp_db_path: Path, metrics_path: Path
) -> None:
    client = make_client(stats_ttl=0)
    items = items_of(tmp_db_path, "api-stats", live=False)
    fear = next(i for i in items if i.text.startswith(FEAR))
    answer(tmp_db_path, fear, "someone-else-16chars", "doubt")

    client.get("/api/round")  # issues the player cookie
    res = client.post(
        "/api/dispute",
        json={
            "round_id": fear.round_id,
            "item_id": fear.item_id,
            "reference_technique": "doubt",  # wrong on purpose: the stored one is used
            "player_technique": "exaggeration_minimization",
            "comment": COMMENT,
        },
    )
    assert res.status_code == 200
    dispute_id = res.json()["dispute_id"]

    stats = client.get("/api/stats/disputes")

    body = stats.json()
    assert body["disputes"] == {"items": 1, "votes": 1}
    assert body["answers"] == {"n": 1}
    assert body["by_status"]["queued"] == 1 and body["by_status"]["n"] == 1
    assert body["pairs"] == [
        {"reference": "appeal_to_fear", "player": "exaggeration_minimization",
         "votes": 1, "items": 1}
    ]
    assert COMMENT not in stats.text and dispute_id not in stats.text
    assert fear.item_id not in stats.text and "set-cookie" not in stats.headers
    assert not metrics_path.exists()  # disputes never touch the evaluation results


def test_response_is_cached_for_the_ttl(make_client, tmp_db_path: Path) -> None:
    client = make_client(stats_ttl=3600)
    first = client.get("/api/stats/disputes").json()
    item = items_of(tmp_db_path, "cached", live=False)[0]
    dispute(tmp_db_path, item, ["doubt"])

    assert client.get("/api/stats/disputes").json() == first  # still the cached copy
    assert make_client(stats_ttl=0).get("/api/stats/disputes").json()["disputes"]["votes"] == 1


def test_stats_cache_recomputes_after_the_ttl() -> None:
    now = [0.0]
    calls: list[int] = []
    cache = StatsCache(5.0, clock=lambda: now[0])

    def compute() -> dict:
        calls.append(1)
        return {"n": len(calls)}

    assert cache.get(compute) == {"n": 1}
    now[0] = 4.9
    assert cache.get(compute) == {"n": 1}
    now[0] = 5.0
    assert cache.get(compute) == {"n": 2}


def test_pairs_are_limited_to_the_top_ones(make_client, tmp_db_path: Path) -> None:
    items = items_of(tmp_db_path, "many", live=False)
    techniques = [
        "loaded_language", "appeal_to_fear", "exaggeration_minimization", "doubt",
        "vague_authority", "changing_subject", "no_technique",
    ]
    for item in items:
        dispute(tmp_db_path, item, techniques)  # 3 items x 7 picks = up to 21 pairs

    body = make_client(stats_ttl=0).get("/api/stats/disputes").json()

    assert body["disputes"]["votes"] == 21
    assert len(body["pairs"]) == 10
